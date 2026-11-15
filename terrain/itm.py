"""Longley-Rice ITM point-to-point loss through the NTIA library and ctypes (ADR 0002).

The Irregular Terrain Model (ITM) reads the ground profile between two
antennas and returns the basic transmission loss. NTIA publishes it as public
domain C++; the Dagster image compiles NTIA commit 183ad95b into libitm.so
(docker/dagster/Dockerfile, stage itm-build) and this module calls
`ITM_P2P_CR_Ex` with the signature of `include/itm.h` at that commit. The
library path comes from the caller, which reads `settings.itm_library_path`
(ITM_LIBRARY_PATH).

Profiles arrive as uncurved ground in EPSG:26915 metres at a uniform step; ITM
applies its own effective earth radius from the surface refractivity (ADR 0006
decision 3). Settings, named defaults per ADR 0002 and the agent brief:
continental temperate climate, N0 = 301 N-units, average ground (relative
permittivity 15, conductivity 0.005 S/m), 50 percent confidence and
reliability, mode of variability 12.
"""

import ctypes
import math
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import numpy.typing as npt
import structlog

from terrain.errors import RefusedDrawError, ZeroLengthPathError
from terrain.profile import MIN_SAMPLES, PathProfile
from terrain.result import PathLossResult

FloatArray = npt.NDArray[np.float64]
Polarisation = Literal["horizontal", "vertical"]

log = structlog.get_logger(component="terrain.itm")

CLIMATE_CONTINENTAL_TEMPERATE = 5
# Mode of variability 12: mobile mode (2) plus 10, which removes location
# variability; the usual choice for a point-to-point link between fixed sites.
MDVAR_POINT_TO_POINT = 12
POLARISATION_CODES = {"horizontal": 0, "vertical": 1}  # include/Enums.h
STEP_TOLERANCE_M = 1e-6
SUCCESS_CODES = frozenset({0, 1})  # SUCCESS, SUCCESS_WITH_WARNINGS (include/Errors.h)
ERROR_NAMES = {
    1000: "TX_TERMINAL_HEIGHT",
    1001: "RX_TERMINAL_HEIGHT",
    1002: "INVALID_RADIO_CLIMATE",
    1003: "INVALID_TIME",
    1004: "INVALID_LOCATION",
    1005: "INVALID_SITUATION",
    1006: "INVALID_CONFIDENCE",
    1007: "INVALID_RELIABILITY",
    1008: "REFRACTIVITY",
    1009: "FREQUENCY",
    1010: "POLARIZATION",
    1011: "EPSILON",
    1012: "SIGMA",
    1013: "GROUND_IMPEDANCE",
    1014: "MDVAR",
    1016: "EFFECTIVE_EARTH",
    1017: "PATH_DISTANCE",
    1018: "DELTA_H",
    1019: "TX_SITING_CRITERIA",
    1020: "RX_SITING_CRITERIA",
    1021: "SURFACE_REFRACTIVITY_SMALL",
    1022: "SURFACE_REFRACTIVITY_LARGE",
}


class IntermediateValues(ctypes.Structure):
    """`struct IntermediateValues` of include/itm.h, filled by the _Ex entry points."""

    _fields_ = [
        ("theta_hzn", ctypes.c_double * 2),
        ("d_hzn__meter", ctypes.c_double * 2),
        ("h_e__meter", ctypes.c_double * 2),
        ("N_s", ctypes.c_double),
        ("delta_h__meter", ctypes.c_double),
        ("A_ref__db", ctypes.c_double),
        ("A_fs__db", ctypes.c_double),
        ("d__km", ctypes.c_double),
        ("mode", ctypes.c_int),
    ]


_DOUBLE_POINTER = ctypes.POINTER(ctypes.c_double)
_P2P_CR_EX_ARGTYPES = (
    ctypes.c_double,  # h_tx__meter
    ctypes.c_double,  # h_rx__meter
    _DOUBLE_POINTER,  # pfl[]
    ctypes.c_int,  # climate
    ctypes.c_double,  # N_0
    ctypes.c_double,  # f__mhz
    ctypes.c_int,  # pol
    ctypes.c_double,  # epsilon
    ctypes.c_double,  # sigma
    ctypes.c_int,  # mdvar
    ctypes.c_double,  # confidence
    ctypes.c_double,  # reliability
    _DOUBLE_POINTER,  # A__db
    ctypes.POINTER(ctypes.c_long),  # warnings
    ctypes.POINTER(IntermediateValues),  # interValues
)


@dataclass(frozen=True)
class ItmSettings:
    """ITM inputs that are not the profile, the heights or the frequency.

    Attributes:
        climate: Radio climate code of include/Enums.h; 5 is continental temperate.
        surface_refractivity_n: N_0, surface refractivity, N-units.
        ground_permittivity: Relative permittivity epsilon of the ground.
        ground_conductivity_s_per_m: Ground conductivity sigma, S/m.
        mdvar: Mode of variability.
        confidence_pct: Confidence, percent.
        reliability_pct: Reliability, percent.
    """

    climate: int = CLIMATE_CONTINENTAL_TEMPERATE
    surface_refractivity_n: float = 301.0
    ground_permittivity: float = 15.0
    ground_conductivity_s_per_m: float = 0.005
    mdvar: int = MDVAR_POINT_TO_POINT
    confidence_pct: float = 50.0
    reliability_pct: float = 50.0


DEFAULT_SETTINGS = ItmSettings()


@dataclass(frozen=True, eq=False)
class ItmLibrary:
    """The loaded NTIA library with `ITM_P2P_CR_Ex` declared."""

    path: Path
    handle: ctypes.CDLL


@lru_cache(maxsize=4)
def load_itm_library(path: Path) -> ItmLibrary:
    """Load libitm.so and declare `ITM_P2P_CR_Ex` as include/itm.h has it.

    Args:
        path: The shared library, `settings.itm_library_path`.

    Raises:
        RuntimeError: naming the install step when the file is missing or
            does not load.
    """
    hint = (
        "the Dagster image builds it (docker/dagster/Dockerfile, stage itm-build); "
        "a host-side build sets ITM_LIBRARY_PATH"
    )
    log.info("itm_library_load_begin", path=str(path))
    started = time.monotonic()
    if not Path(path).is_file():
        raise RuntimeError(f"no ITM library at {path}: {hint}")
    try:
        handle = ctypes.CDLL(str(path))
        function = handle.ITM_P2P_CR_Ex
    except (OSError, AttributeError) as err:
        raise RuntimeError(f"ITM library at {path} does not load: {hint}") from err
    function.restype = ctypes.c_int
    function.argtypes = _P2P_CR_EX_ARGTYPES
    log.info(
        "itm_library_load_end", path=str(path), duration_s=round(time.monotonic() - started, 3)
    )
    return ItmLibrary(path=Path(path), handle=handle)


def itm_profile(distance_m: FloatArray, ground_m: FloatArray) -> FloatArray:
    """Build ITM's `pfl` array: [intervals, step in metres, elevations in metres...].

    Args:
        distance_m: Distance of each sample from the transmitter, metres,
            from 0 at a uniform step.
        ground_m: Ground height at each sample, metres above NAVD88, uncurved.

    Raises:
        ZeroLengthPathError: if the profile has no extent.
        ValueError: for arrays of different lengths, a first distance other
            than 0 or an uneven step.
        RefusedDrawError: `short_profile` for fewer than five samples, `dem_gap`
            if a ground height is missing.
    """
    if distance_m.shape != ground_m.shape:
        raise ValueError("distance and ground arrays must have one length")
    if distance_m.size < MIN_SAMPLES:
        raise RefusedDrawError(
            "short_profile", f"an ITM profile needs at least five samples, got {distance_m.size}"
        )
    if distance_m[-1] == 0.0:
        raise ZeroLengthPathError("zero-length path: the profile has no extent")
    if distance_m[0] != 0.0:
        raise ValueError(f"profile distances must start at 0, got {distance_m[0]} m")
    step_m = float(distance_m[-1]) / (distance_m.size - 1)
    if not np.allclose(np.diff(distance_m), step_m, rtol=0.0, atol=STEP_TOLERANCE_M):
        raise ValueError("ITM needs a uniform profile step")
    missing = np.flatnonzero(~np.isfinite(ground_m))
    if missing.size:
        raise RefusedDrawError(
            "dem_gap",
            f"ground height missing at profile index {int(missing[0])}; "
            "the draw is refused (ADR 0006 item 11)",
        )
    header = np.array([distance_m.size - 1, step_m], dtype=np.float64)
    return np.concatenate([header, np.asarray(ground_m, dtype=np.float64)])


def check_itm_status(code: int) -> None:
    """Pass ITM's success codes; raise on its error codes (include/Errors.h).

    Raises:
        RuntimeError: naming the error, for any code other than 0 or 1.
    """
    if code in SUCCESS_CODES:
        return
    raise RuntimeError(f"ITM returned error {code} ({ERROR_NAMES.get(code, 'unknown')})")


def itm_loss(
    library: ItmLibrary,
    profile: PathProfile,
    *,
    tx_height_m: float,
    rx_height_m: float,
    frequency_mhz: float,
    polarisation: Polarisation,
    settings: ItmSettings = DEFAULT_SETTINGS,
) -> PathLossResult:
    """Basic transmission loss by ITM point-to-point, confidence and reliability form.

    Args:
        library: From `load_itm_library`.
        profile: Uncurved ground in metres along the path, EPSG:26915, at a
            uniform step (1 m on the 1 m layers, 10 m on the metro DEM); its
            clutter is not used.
        tx_height_m: Transmitter antenna height above ground, metres.
        rx_height_m: Receiver antenna height above ground, metres.
        frequency_mhz: Frequency, MHz.
        polarisation: From the antenna profile; required, so no caller inherits one.
        settings: Climate, refractivity, ground and statistics.

    Returns:
        The loss in dB, antenna gains excluded, with ITM's warning flags.

    Raises:
        ZeroLengthPathError: if the profile has no extent.
        ValueError: for a profile `itm_profile` rejects or an unknown polarisation.
        RefusedDrawError: `short_profile` or `dem_gap`, as `itm_profile` raises them.
        RuntimeError: if ITM returns an error code or a loss that is not finite.
    """
    if polarisation not in POLARISATION_CODES:
        raise ValueError(f"polarisation must be horizontal or vertical, got {polarisation!r}")
    pfl = np.ascontiguousarray(itm_profile(profile.distance_m, profile.ground_m))
    loss_db, warnings, intermediate = ctypes.c_double(), ctypes.c_long(), IntermediateValues()
    status = library.handle.ITM_P2P_CR_Ex(
        tx_height_m, rx_height_m, pfl.ctypes.data_as(_DOUBLE_POINTER), settings.climate,
        settings.surface_refractivity_n, frequency_mhz, POLARISATION_CODES[polarisation],
        settings.ground_permittivity, settings.ground_conductivity_s_per_m, settings.mdvar,
        settings.confidence_pct, settings.reliability_pct,
        ctypes.byref(loss_db), ctypes.byref(warnings), ctypes.byref(intermediate),
    )  # fmt: skip
    check_itm_status(int(status))
    if not math.isfinite(loss_db.value):
        raise RuntimeError(f"ITM returned a loss that is not finite: {loss_db.value}")
    return PathLossResult(loss_db=loss_db.value, itm_warning_flags=int(warnings.value))
