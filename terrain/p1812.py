"""ITU-R P.1812-8 basic transmission loss through Py1812 at commit 6c9061dd (ADR 0006).

Py1812 loads `P1812.npz`, the ITU digital maps, from its own installed folder
when it is imported (P1812.py line 19). The maps are ITU's and are never
committed, baked into an image or written into site-packages, so this module
imports Py1812 once, at module level, against a placeholder built in memory:
two 1 by 1 grids of NaN. The real DN and N0 come from a map snapshot verified
against its manifest (`load_maps`) and are passed on every call; a call that
omitted them would read the placeholder and fail with IndexError, never with a
wrong number (ADR 0006 decision 2).

Each call runs with debug=1 and an in-memory log, and the troposcatter loss
Lbs is read from the line P1812.py line 510 writes, for `p1812_at_cap_share`
(item 2 (a)). tests/test_p1812_cap.py fails if that line disappears or
changes meaning.

Settings fixed by ADR 0006 question 6: p = 50, pL = 50 with sigmaL = 0, zone 4
(inland) at every point, dct = dcr = 500 km, flag4 = 0. Profiles arrive in
EPSG:26915 metres of uncurved ground; P.1812 applies its own effective earth
radius from DN (decision 3). Units change here only: metres to km, MHz to GHz,
and the endpoints to EPSG:4326 through one pyproj transformer.

Import this module lazily, on the first `p1812` call: the Dagster definitions
must load without Py1812's import or the patch it needs.
"""

import hashlib
import importlib
import importlib.resources
import importlib.util
import io
import json
import math
import sys
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
import structlog
from pyproj import Transformer

from terrain.errors import RefusalReason, RefusedDrawError, ZeroLengthPathError
from terrain.profile import PathProfile
from terrain.result import PathLossResult

FloatArray = npt.NDArray[np.float64]
Polarisation = Literal["horizontal", "vertical"]

log = structlog.get_logger(component="terrain.p1812")

PY1812_MODULE = "Py1812.P1812"
PLACEHOLDER_NAME = "P1812.npz"
MANIFEST_NAME = "manifest.json"
MAP_FILES = ("DN50.TXT", "N050.TXT")
# 1.5 degree grids, latitude 90 to -90 by row and longitude 0 to 360 by
# column, the layout Py1812's interp2 assumes (P1812.py lines 2058 to 2091).
MAP_SHAPE = (121, 241)
MAP_SPACING_DEG = 1.5
MAPS_HINT = "make p1812-maps writes the ITU map snapshot; the engine names it in its config"

TIME_PERCENT = 50.0
LOCATION_PERCENT = 50.0
LOCATION_SIGMA_DB = 0.0
ZONE_INLAND = 4
COAST_DISTANCE_KM = 500.0
# The earth radius Py1812 passes to great_circle_path for the path centre (P1812.py line 225).
GREAT_CIRCLE_RADIUS_KM = 6371.0
MIN_SAMPLES = 5
# The sampler puts the last sample on the receiver; more than this apart is a bug.
ENDPOINT_TOLERANCE_M = 0.01
POLARISATION_CODES: Mapping[str, int] = {"horizontal": 1, "vertical": 2}
LBS_NAME = "Lbs (dB)"
LBS_REFERENCE = "Eq (44)"

_TO_LONLAT = Transformer.from_crs("EPSG:26915", "EPSG:4326", always_xy=True)


class _PlaceholderAnchor:
    """Stands in for `files("Py1812")` during the import; holds no ITU data."""

    def joinpath(self, name: str) -> io.BytesIO:
        if name != PLACEHOLDER_NAME:
            raise FileNotFoundError(name)
        buffer = io.BytesIO()
        nan_grid = np.full((1, 1), np.nan)
        np.savez(buffer, DN50=nan_grid, N050=nan_grid)
        buffer.seek(0)
        return buffer


def check_import_preconditions(loaded_modules: Collection[str], package_dir: Path) -> None:
    """Refuse to import Py1812 when its map load could reach real or stale data.

    Args:
        loaded_modules: Names of the modules already imported (`sys.modules`).
        package_dir: The installed Py1812 package folder.

    Raises:
        RuntimeError: if Py1812.P1812 is already imported, so its maps did not
            come from the placeholder, or a P1812.npz sits in the package folder.
    """
    if PY1812_MODULE in loaded_modules:
        raise RuntimeError(
            f"{PY1812_MODULE} was already imported before terrain.p1812; import it only "
            "through terrain.p1812 so its map load reads the in-memory placeholder"
        )
    if (package_dir / PLACEHOLDER_NAME).exists():
        raise RuntimeError(
            f"{package_dir / PLACEHOLDER_NAME} exists; the ITU maps are never placed in the "
            f"installed package (ADR 0006 decision 2). {MAPS_HINT}"
        )


def _package_dir() -> Path:
    spec = importlib.util.find_spec("Py1812")
    if spec is None or spec.origin is None:
        raise RuntimeError("Py1812 is not installed; uv sync --frozen installs it from uv.lock")
    return Path(spec.origin).parent


def _import_with_placeholder() -> ModuleType:
    """Import Py1812.P1812 with `importlib.resources.files` patched for the import only."""
    check_import_preconditions(frozenset(sys.modules), _package_dir())
    real_files = importlib.resources.files

    def files(anchor: Any = None) -> Any:
        return _PlaceholderAnchor() if anchor == "Py1812" else real_files(anchor)

    # setattr, because the replacement returns the placeholder, not a Traversable.
    setattr(importlib.resources, "files", files)  # noqa: B010
    try:
        return importlib.import_module(PY1812_MODULE)
    finally:
        setattr(importlib.resources, "files", real_files)  # noqa: B010


P1812 = _import_with_placeholder()


@dataclass(frozen=True, eq=False)
class P1812Maps:
    """DN and N0 grids from one verified map snapshot.

    Attributes:
        snapshot_id: The snapshot folder name, for the run manifest.
        manifest_sha256: sha256 of the snapshot's manifest file.
        dn50: DN, average refractivity lapse rate over the lowest 1 km, N-units/km.
        n050: N0, sea-level surface refractivity, N-units.
    """

    snapshot_id: str
    manifest_sha256: str
    dn50: FloatArray
    n050: FloatArray


def _maps_error(folder: Path, problem: str) -> RuntimeError:
    return RuntimeError(f"P.1812 map snapshot {folder}: {problem}. {MAPS_HINT}")


def _read_manifest(folder: Path) -> tuple[bytes, dict[str, Any]]:
    path = folder / MANIFEST_NAME
    if not path.is_file():
        raise _maps_error(folder, "no manifest.json")
    raw = path.read_bytes()
    try:
        manifest: dict[str, Any] = json.loads(raw)
        expected = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    except (ValueError, KeyError, TypeError) as err:
        raise _maps_error(folder, "manifest.json does not list files with sha256") from err
    if manifest.get("snapshot_id") != folder.name:
        raise _maps_error(folder, f"manifest names snapshot {manifest.get('snapshot_id')!r}")
    return raw, expected


def _verified_grid(folder: Path, name: str, expected: Mapping[str, str]) -> FloatArray:
    """Read one map file, check its sha256 against the manifest, then parse the same bytes."""
    if name not in expected:
        raise _maps_error(folder, f"{name} is not in the manifest")
    path = folder / name
    if not path.is_file():
        raise _maps_error(folder, f"{name} is missing")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected[name]:
        raise _maps_error(folder, f"{name} does not match its sha256 in the manifest")
    grid = np.loadtxt(io.BytesIO(data), dtype=np.float64, ndmin=2)
    if grid.shape != MAP_SHAPE:
        raise _maps_error(folder, f"{name} is {grid.shape}, not the 121 by 241 grid")
    if not np.isfinite(grid).all():
        raise _maps_error(folder, f"{name} holds a value that is not a finite number")
    return grid


def load_maps(snapshot_dir: Path) -> P1812Maps:
    """Read DN50.TXT and N050.TXT from a map snapshot, verified against its manifest.

    Called once per run. The manifest is the `pipeline.common.snapshot` layout:
    `snapshot_id` equal to the folder name and `files` entries with `path` and
    `sha256`.

    Args:
        snapshot_dir: The finished snapshot folder, `itu-r-p1812-8-202509` for
            real runs or the committed `p1812-maps-synthetic` fixture.

    Raises:
        RuntimeError: naming `make p1812-maps`, when the folder, its manifest or
            a map file is missing, a file does not match its sha256, or a grid
            is not 121 by 241 finite values.
    """
    log.info("p1812_maps_load_begin", path=str(snapshot_dir))
    started = time.monotonic()
    if not snapshot_dir.is_dir():
        raise _maps_error(snapshot_dir, "no such folder")
    manifest_bytes, expected = _read_manifest(snapshot_dir)
    dn50, n050 = (_verified_grid(snapshot_dir, name, expected) for name in MAP_FILES)
    maps = P1812Maps(
        snapshot_id=snapshot_dir.name,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        dn50=dn50,
        n050=n050,
    )
    log.info(
        "p1812_maps_load_end",
        path=str(snapshot_dir),
        duration_s=round(time.monotonic() - started, 3),
        snapshot_id=maps.snapshot_id,
        manifest_sha256=maps.manifest_sha256,
    )
    return maps


def refractivity_at_path_centre(
    maps: P1812Maps,
    tx_lonlat: tuple[float, float],
    rx_lonlat: tuple[float, float],
    length_km: float,
) -> tuple[float, float]:
    """DN and N0 at the path centre, found as Py1812 finds them (P1812.py lines 223 to 241).

    Args:
        maps: The verified map grids.
        tx_lonlat: Transmitter longitude and latitude, degrees, EPSG:4326.
        rx_lonlat: Receiver longitude and latitude, degrees, EPSG:4326.
        length_km: Profile length, km; the centre is at half of it.

    Returns:
        DN in N-units/km and N0 in N-units, bilinear from the 1.5 degree grids.
    """
    centre_lon, centre_lat, _, _ = P1812.great_circle_path(
        rx_lonlat[0],
        tx_lonlat[0],
        rx_lonlat[1],
        tx_lonlat[1],
        GREAT_CIRCLE_RADIUS_KM,
        length_km / 2,
    )
    dn = P1812.interp2(maps.dn50, centre_lon, centre_lat, MAP_SPACING_DEG, MAP_SPACING_DEG)
    n0 = P1812.interp2(maps.n050, centre_lon, centre_lat, MAP_SPACING_DEG, MAP_SPACING_DEG)
    return float(dn), float(n0)


@dataclass(frozen=True, eq=False)
class BtLossInputs:
    """Everything one `bt_loss` call takes, already in Py1812's units.

    Attributes:
        distance_km: Distance of each profile point from the transmitter, km,
            ascending from 0.
        ground_m: Ground height at each point, metres above NAVD88, uncurved.
        clutter_m: Clutter height R at each point, metres above ground.
        tx_height_m: Transmitter antenna height above ground, metres.
        rx_height_m: Receiver antenna height above ground, metres.
        frequency_ghz: Frequency, GHz.
        polarisation_code: 1 horizontal, 2 vertical (Py1812's codes).
        tx_lat_deg: Transmitter latitude, degrees, EPSG:4326.
        tx_lon_deg: Transmitter longitude, degrees, EPSG:4326.
        rx_lat_deg: Receiver latitude, degrees, EPSG:4326.
        rx_lon_deg: Receiver longitude, degrees, EPSG:4326.
        dn: DN at the path centre, N-units/km.
        n0: N0 at the path centre, N-units.
    """

    distance_km: FloatArray
    ground_m: FloatArray
    clutter_m: FloatArray
    tx_height_m: float
    rx_height_m: float
    frequency_ghz: float
    polarisation_code: int
    tx_lat_deg: float
    tx_lon_deg: float
    rx_lat_deg: float
    rx_lon_deg: float
    dn: float
    n0: float


def _refuse_missing(values: FloatArray, what: str, reason: RefusalReason) -> None:
    missing = np.flatnonzero(~np.isfinite(values))
    if missing.size:
        raise RefusedDrawError(
            reason,
            f"{what} height missing at profile index {int(missing[0])} "
            f"({missing.size} points); the draw is refused (ADR 0006 item 11)",
        )


def call_bt_loss(inputs: BtLossInputs) -> tuple[float, str]:
    """Run Py1812's bt_loss once with debug=1 and return Lb with the debug log text.

    NaN never reaches bt_loss: at 6c9061dd it returns a finite, wrong loss for
    some NaN in R (ADR 0006 question 6, spike run 3). The log is a StringIO,
    so bt_loss opens no file in the working folder (P1812.py lines 243 to 256).

    Returns:
        Basic transmission loss Lb in dB, equation (69) of P.1812-8, and the log.

    Raises:
        RefusedDrawError: `dem_gap` if a ground height is NaN or infinite,
            `clutter_nan` if a clutter height is.
        RuntimeError: if bt_loss returns a loss that is not finite.
    """
    _refuse_missing(inputs.ground_m, "ground", "dem_gap")
    _refuse_missing(inputs.clutter_m, "clutter", "clutter_nan")
    zones = np.full(inputs.distance_km.size, ZONE_INLAND)
    with io.StringIO() as debug_log:
        loss_db, _ = P1812.bt_loss(
            inputs.frequency_ghz, TIME_PERCENT, inputs.distance_km, inputs.ground_m,
            inputs.clutter_m, zones, inputs.tx_height_m, inputs.rx_height_m,
            inputs.polarisation_code, inputs.tx_lat_deg, inputs.rx_lat_deg,
            inputs.tx_lon_deg, inputs.rx_lon_deg,
            pL=LOCATION_PERCENT, sigmaL=LOCATION_SIGMA_DB, DN=inputs.dn, N0=inputs.n0,
            dct=COAST_DISTANCE_KM, dcr=COAST_DISTANCE_KM, flag4=0, debug=1, fid_log=debug_log,
        )  # fmt: skip
        log_text = debug_log.getvalue()
    if not math.isfinite(loss_db):
        raise RuntimeError(f"P.1812 returned a loss that is not finite: {loss_db}")
    return float(loss_db), log_text


def troposcatter_loss_from_log(log_text: str) -> float:
    """The troposcatter loss Lbs, equation (44) of P.1812-8, from a bt_loss debug log.

    Py1812 at 6c9061dd writes it once per call as `Lbs (dB),Eq (44),,<value>,`
    with %.10g (P1812.py lines 258 and 510).

    Raises:
        RuntimeError: if the log holds no such line, more than one, or a value
            that is not a finite number; the Py1812 pin has moved under us.
    """
    rows = [line.split(",") for line in log_text.splitlines()]
    matches = [
        row
        for row in rows
        if len(row) >= 2 and row[0].strip() == LBS_NAME and row[1].strip() == LBS_REFERENCE
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"the P.1812 debug log holds {len(matches)} Lbs lines, not one; "
            "check the Py1812 pin against tests/test_p1812_cap.py (ADR 0006 item 2)"
        )
    try:
        lbs_db = float(matches[0][3])
    except (IndexError, ValueError) as err:
        raise RuntimeError(f"the P.1812 Lbs line holds no number: {','.join(matches[0])}") from err
    if not math.isfinite(lbs_db):
        raise RuntimeError(f"the P.1812 Lbs line holds a value that is not finite: {lbs_db}")
    return lbs_db


def _checked_clutter(profile: PathProfile) -> FloatArray:
    """The profile's clutter, once the profile is shown to describe the whole path.

    PathProfile already checks the array lengths and that distances start at 0
    and ascend; this adds what P.1812 needs beyond that.
    """
    if profile.clutter_m is None:
        raise ValueError("P.1812 needs clutter heights; it is null where only the metro DEM exists")
    distance_m = profile.distance_m
    if distance_m.size < MIN_SAMPLES:
        raise RefusedDrawError(
            "short_profile",
            f"a P.1812 profile needs at least five samples, got {distance_m.size}",
        )
    endpoint_distance_m = math.dist(profile.tx_xy_m, profile.rx_xy_m)
    if endpoint_distance_m == 0.0:
        raise ZeroLengthPathError("zero-length path: transmitter and receiver coincide")
    if abs(distance_m[-1] - endpoint_distance_m) > ENDPOINT_TOLERANCE_M:
        raise ValueError(
            f"profile ends at {distance_m[-1]:.3f} m but the receiver is "
            f"{endpoint_distance_m:.3f} m from the transmitter"
        )
    return profile.clutter_m


def p1812_loss(
    profile: PathProfile,
    *,
    tx_height_m: float,
    rx_height_m: float,
    frequency_mhz: float,
    maps: P1812Maps,
    polarisation: Polarisation,
) -> PathLossResult:
    """Median basic transmission loss over one profile by ITU-R P.1812-8, equation (69).

    Args:
        profile: Uncurved ground and clutter R in metres along the path, endpoints
            in EPSG:26915 metres, distances from 0 to the receiver; P.1812's own
            10 m profile on the 1 m layers (ADR 0006 item 1 (b)).
        tx_height_m: Transmitter antenna height above ground, metres.
        rx_height_m: Receiver antenna height above ground, metres.
        frequency_mhz: Frequency, MHz.
        maps: DN and N0 grids from `load_maps`.
        polarisation: From the antenna profile; required, so no caller inherits one.

    Returns:
        Lb in dB, antenna gains excluded, with Lbs (equation 44) as a diagnostic.

    Raises:
        ZeroLengthPathError: if the two endpoints coincide.
        ValueError: for a profile without clutter or distances that do not end
            at the receiver.
        RefusedDrawError: `short_profile` for fewer than five samples, `dem_gap`
            or `clutter_nan` for a missing ground or clutter height.
        RuntimeError: if Py1812 returns a non-finite loss or an unusable log.
    """
    clutter_m = _checked_clutter(profile)
    if polarisation not in POLARISATION_CODES:
        raise ValueError(f"polarisation must be horizontal or vertical, got {polarisation!r}")
    tx_lon, tx_lat = _TO_LONLAT.transform(*profile.tx_xy_m)
    rx_lon, rx_lat = _TO_LONLAT.transform(*profile.rx_xy_m)
    distance_km = np.array(profile.distance_m, dtype=np.float64) / 1000.0
    dn, n0 = refractivity_at_path_centre(
        maps, (tx_lon, tx_lat), (rx_lon, rx_lat), float(distance_km[-1])
    )
    inputs = BtLossInputs(
        distance_km=distance_km,
        ground_m=np.array(profile.ground_m, dtype=np.float64),
        clutter_m=np.array(clutter_m, dtype=np.float64),
        tx_height_m=tx_height_m,
        rx_height_m=rx_height_m,
        frequency_ghz=frequency_mhz / 1000.0,
        polarisation_code=POLARISATION_CODES[polarisation],
        tx_lat_deg=tx_lat,
        tx_lon_deg=tx_lon,
        rx_lat_deg=rx_lat,
        rx_lon_deg=rx_lon,
        dn=dn,
        n0=n0,
    )
    loss_db, log_text = call_bt_loss(inputs)
    return PathLossResult(
        loss_db=loss_db, troposcatter_loss_db=troposcatter_loss_from_log(log_text)
    )
