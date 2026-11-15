"""path_loss_db, the one prediction function over the five models (ADR 0006 decision 3).

Every model takes one profile of uncurved ground in EPSG:26915 (UTM zone 15N,
metres), the frequency and the two antenna profiles, and returns a
PathLossResult: the median basic transmission loss in dB, antenna gains
excluded, plus the diagnostics only some models fill (item 10 (a)).

- fspl: free space over the profile's length, ITU-R P.525-5 equation (5).
- knife_edge: free space plus the Bullington diffraction loss of ITU-R
  P.526-16 section 4.5.1, equations (49) to (57), with the k = 4/3 bulge.
- hata: Okumura-Hata over the profile's length, its variant chosen by the
  caller from the median building shares of the link-hour (decision 7.1).
- itm: NTIA ITM point to point (ADR 0002), the library and settings passed in.
- p1812: ITU-R P.1812-8 through Py1812 on P.1812's own 10 m profile with
  clutter (item 1 (b)); it fills Lbs, and `PathLossResult.at_troposcatter_cap`
  turns it into the cap flag (item 2 (a)).

`DrawModels` is the adapter `terrain.draws.sample_link` calls once per draw.
`terrain.p1812` is imported on the first p1812 call and never at module level,
so a process that runs no p1812 call never imports Py1812 or patches its map
load (decision 2). This module reads no file and writes nothing.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, get_args

from terrain.antenna import AntennaProfile
from terrain.baselines import HataVariant, fspl_db, hata_db
from terrain.diffraction import knife_edge_db
from terrain.draws import DrawProfiles, DrawQuantities
from terrain.errors import RefusedDrawError, ZeroLengthPathError
from terrain.itm import DEFAULT_SETTINGS, ItmLibrary, ItmSettings, Polarisation, itm_loss
from terrain.profile import (
    ENDPOINT_TOLERANCE_M,
    MIN_SAMPLES,
    PathProfile,
    refuse_ground_gap,
)
from terrain.result import PathLossResult

if TYPE_CHECKING:
    from terrain.p1812 import P1812Maps

Model = Literal["fspl", "knife_edge", "hata", "itm", "p1812"]
MODELS: tuple[Model, ...] = get_args(Model)

ITM_HINT = (
    "itm needs the NTIA library: the Dagster image builds it (docker/dagster/Dockerfile, "
    "stage itm-build) and the caller loads it from ITM_LIBRARY_PATH (ADR 0002)"
)
MAPS_HINT = (
    "p1812 needs the ITU map snapshot: make p1812-maps writes it and the engine config "
    "names it (ADR 0006 decision 2)"
)


def path_loss_db(
    model: Model,
    profile: PathProfile,
    frequency_mhz: float,
    tx: AntennaProfile,
    rx: AntennaProfile,
    *,
    hata_variant: HataVariant | None = None,
    itm_library: ItmLibrary | None = None,
    itm_settings: ItmSettings = DEFAULT_SETTINGS,
    p1812_maps: "P1812Maps | None" = None,
) -> PathLossResult:
    """Median basic transmission loss in dB by one model, antenna gains excluded.

    Args:
        model: One of fspl, knife_edge, hata, itm, p1812.
        profile: Uncurved ground in metres above NAVD88, endpoints in
            EPSG:26915 metres, distances from 0 at the transmitter to the
            receiver at a uniform step. For p1812, P.1812's 10 m profile with
            clutter; for the others, the ground profile of the draw.
        frequency_mhz: Carrier frequency in MHz, a run parameter.
        tx: Transmitter antenna profile; its height and polarisation are read.
        rx: Receiver antenna profile; its height and polarisation are read.
        hata_variant: "urban" or "suburban" for hata, from
            `terrain.baselines.hata_variant` on the link-hour's median shares.
        itm_library: The loaded NTIA library, for itm.
        itm_settings: ITM's climate, ground and statistics (ADR 0002 defaults).
        p1812_maps: DN and N0 grids from a verified map snapshot, for p1812.

    Returns:
        The loss, with Lbs for p1812 and ITM's warning flags for itm.

    Raises:
        ZeroLengthPathError: if the two endpoints coincide.
        RefusedDrawError: `short_profile` for fewer than five samples, `dem_gap`
            for a missing ground height, `clutter_nan` for a NaN in P.1812's
            clutter.
        ValueError: for an unknown model, a frequency that is not positive,
            distances that do not end at the receiver, hata without a variant,
            p1812 on a profile without clutter, or itm or p1812 with the two
            antennas in different polarisations.
        RuntimeError: naming the install step when itm has no library or p1812
            no maps, or when a model library fails.
    """
    check_path(profile, frequency_mhz)
    if model == "fspl":
        return PathLossResult(loss_db=fspl_db(profile.length_m, frequency_mhz))
    if model == "knife_edge":
        diffraction_db = knife_edge_db(profile, frequency_mhz, tx.height_m, rx.height_m)
        return PathLossResult(loss_db=fspl_db(profile.length_m, frequency_mhz) + diffraction_db)
    if model == "hata":
        variant = _require_variant(hata_variant)
        loss_db = hata_db(profile.length_m, frequency_mhz, tx.height_m, rx.height_m, variant)
        return PathLossResult(loss_db=loss_db)
    if model == "itm":
        return _itm(profile, frequency_mhz, tx, rx, itm_library, itm_settings)
    if model == "p1812":
        return _p1812(profile, frequency_mhz, tx, rx, p1812_maps)
    raise _unknown_model(model)


def _unknown_model(model: str) -> ValueError:
    return ValueError(f"unknown model {model!r}; expected one of {', '.join(MODELS)}")


def check_path(profile: PathProfile, frequency_mhz: float) -> None:
    """Check what every model needs of a profile, before any model runs.

    Args:
        profile: Profile in EPSG:26915 metres.
        frequency_mhz: Carrier frequency in MHz.

    Raises:
        ValueError: if the frequency is not a positive number, or the last
            distance is more than 0.01 m from the receiver.
        ZeroLengthPathError: if the two endpoints coincide; checked before the
            sample count, so a zero-length link is always recorded per link.
        RefusedDrawError: `short_profile` for fewer than five samples, `dem_gap`
            for a missing ground height.
    """
    if not (math.isfinite(frequency_mhz) and frequency_mhz > 0.0):
        raise ValueError(f"frequency_mhz must be a positive number, got {frequency_mhz}")
    endpoint_distance_m = math.dist(profile.tx_xy_m, profile.rx_xy_m)
    if endpoint_distance_m == 0.0:
        raise ZeroLengthPathError("zero-length path: transmitter and receiver coincide")
    if abs(profile.length_m - endpoint_distance_m) > ENDPOINT_TOLERANCE_M:
        raise ValueError(
            f"profile ends at {profile.length_m:.3f} m but the receiver is "
            f"{endpoint_distance_m:.3f} m from the transmitter"
        )
    if profile.distance_m.size < MIN_SAMPLES:
        raise RefusedDrawError(
            "short_profile",
            f"path_loss_db needs at least five samples, got {profile.distance_m.size}",
        )
    refuse_ground_gap(profile)


def _require_variant(variant: HataVariant | None) -> HataVariant:
    if variant is None:
        raise ValueError(
            "hata needs hata_variant, one per link-hour from the median building shares "
            "(terrain.baselines.hata_variant, ADR 0006 decision 7.1)"
        )
    return variant


def _shared_polarisation(tx: AntennaProfile, rx: AntennaProfile) -> Polarisation:
    """The one polarisation ITM and P.1812 take; neither models a cross-polarised link."""
    if tx.polarisation != rx.polarisation:
        raise ValueError(
            f"the antennas differ in polarisation ({tx.name} {tx.polarisation}, "
            f"{rx.name} {rx.polarisation}); ITM and P.1812 take one"
        )
    return tx.polarisation


def _itm(
    profile: PathProfile,
    frequency_mhz: float,
    tx: AntennaProfile,
    rx: AntennaProfile,
    library: ItmLibrary | None,
    settings: ItmSettings,
) -> PathLossResult:
    if library is None:
        raise RuntimeError(ITM_HINT)
    return itm_loss(
        library,
        profile,
        tx_height_m=tx.height_m,
        rx_height_m=rx.height_m,
        frequency_mhz=frequency_mhz,
        polarisation=_shared_polarisation(tx, rx),
        settings=settings,
    )


def _p1812(
    profile: PathProfile,
    frequency_mhz: float,
    tx: AntennaProfile,
    rx: AntennaProfile,
    maps: "P1812Maps | None",
) -> PathLossResult:
    if maps is None:
        raise RuntimeError(MAPS_HINT)
    polarisation = _shared_polarisation(tx, rx)
    # Imported here, on the first p1812 call: importing it runs Py1812's map
    # load against the in-memory placeholder (ADR 0006 decision 2).
    from terrain import p1812 as p1812_wrapper

    return p1812_wrapper.p1812_loss(
        profile,
        tx_height_m=tx.height_m,
        rx_height_m=rx.height_m,
        frequency_mhz=frequency_mhz,
        maps=maps,
        polarisation=polarisation,
    )


@dataclass(frozen=True, kw_only=True)
class DrawModels:
    """The quantities function `sample_link` calls: a run's models on one draw.

    Each model's loss is reported as the value `<model>_db`, and P.1812's "Lb
    within 0.1 dB of Lbs" as the flag `p1812_at_cap`, so a DrawSummary gives
    `median("itm_db")` for `itm_db_median` and `share("p1812_at_cap")` for
    `p1812_at_cap_share`. fspl, knife_edge, hata and itm read the draw's
    ground profile and p1812 its 10 m cell profile. A draw on the metro DEM has
    no cell profile, so p1812 is left out and its columns are null there
    (decision 4). A RefusedDrawError from any model leaves this call, so
    `sample_link` drops the draw for every model (item 11 (b)). The Hata
    variant is one per link-hour: set it per link with `dataclasses.replace`.

    Attributes:
        models: The run's models, each once, in the order they run.
        frequency_mhz: Carrier frequency in MHz.
        tx: Transmitter antenna profile.
        rx: Receiver antenna profile.
        hata_variant: The link-hour's Okumura-Hata variant, for hata.
        itm_library: The loaded NTIA library, for itm.
        itm_settings: ITM's settings.
        p1812_maps: The run's verified map grids, for p1812.
    """

    models: tuple[Model, ...]
    frequency_mhz: float
    tx: AntennaProfile
    rx: AntennaProfile
    hata_variant: HataVariant | None = None
    itm_library: ItmLibrary | None = None
    itm_settings: ItmSettings = DEFAULT_SETTINGS
    p1812_maps: "P1812Maps | None" = None

    def __post_init__(self) -> None:
        """Check the model list.

        Raises:
            ValueError: if the list is empty, names an unknown model or names
                one twice.
        """
        if not self.models:
            raise ValueError("DrawModels needs at least one model")
        for model in self.models:
            if model not in MODELS:
                raise _unknown_model(model)
        if len(set(self.models)) != len(self.models):
            raise ValueError(f"a model is listed twice: {self.models}")

    def __call__(self, draw: DrawProfiles) -> DrawQuantities:
        """Run every model on one draw's profiles.

        Args:
            draw: The draw's ground profile and, on the 1 m layers, P.1812's.

        Returns:
            One loss per model, and the cap flag where P.1812 ran.

        Raises:
            RefusedDrawError, ZeroLengthPathError, ValueError, RuntimeError:
                as `path_loss_db` raises them.
        """
        values: dict[str, float] = {}
        flags: dict[str, bool] = {}
        for model in self.models:
            profile = draw.p1812 if model == "p1812" else draw.ground
            if profile is None:
                continue
            result = path_loss_db(
                model,
                profile,
                self.frequency_mhz,
                self.tx,
                self.rx,
                hata_variant=self.hata_variant,
                itm_library=self.itm_library,
                itm_settings=self.itm_settings,
                p1812_maps=self.p1812_maps,
            )
            values[f"{model}_db"] = result.loss_db
            if result.at_troposcatter_cap is not None:
                flags[f"{model}_at_cap"] = result.at_troposcatter_cap
        return DrawQuantities(values=values, flags=flags)
