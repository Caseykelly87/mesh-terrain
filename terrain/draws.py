"""Endpoint draws: one link's quantities over positions drawn inside its two boxes.

A node's reported position is only known to a box (`box_half_width_m` from its
`precision_bits`), so each link-hour is computed N times, 16 by default, each
time with both endpoints placed uniformly at random inside their boxes in
EPSG:26915 (UTM zone 15N, metres). One placement is a draw. The draws are seeded
from the run seed and the link id, so a rerun reproduces them (`snp_01`); with
one draw the endpoints are the box centres, the single-draw mode for tests and
the cut-list fallback.

Each draw builds its profiles with a builder (the metro DEM, or the 1 m layers
with P.1812's 10 m cell profile) and passes them to a quantities function the
caller supplies, which runs the models. A `RefusedDrawError` from either drops
the draw for every model and is counted by reason (ADR 0006 item 11 (b)); every
median, interquartile range and share comes from the remaining draws, and all
are null when none remain. An `OutOfExtentError` or `ZeroLengthPathError` is not
a refusal and goes to the caller, which records the link-hour.

This module runs no model itself and does not choose the Okumura-Hata variant;
it reads no database and writes nothing.
"""

import hashlib
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import get_args

import numpy as np
import structlog
from affine import Affine
from rasterio.io import DatasetReader

from terrain.cells import CELL_STEP_M, FineLayers, cell_profile
from terrain.clutter import BoolArray
from terrain.errors import OutOfExtentError, RefusalReason, RefusedDrawError
from terrain.profile import XY, PathProfile, refuse_ground_gap, sample_profile

DEFAULT_ENDPOINT_DRAWS = 16
METRO_STEP_M = 10.0
FINE_STEP_M = 1.0

log = structlog.get_logger(__name__).bind(component="terrain.draws")


@dataclass(frozen=True, kw_only=True)
class EndpointBox:
    """The square an endpoint is known to lie in.

    Attributes:
        centre_xy_m: Box centre, easting and northing in EPSG:26915, metres.
        half_width_m: Half the side of the box in metres; 0 for a known point.
    """

    centre_xy_m: XY
    half_width_m: float

    def __post_init__(self) -> None:
        """Check the half-width.

        Raises:
            ValueError: if the half-width is negative or not finite.
        """
        if not (math.isfinite(self.half_width_m) and self.half_width_m >= 0.0):
            raise ValueError(f"half_width_m must be finite and at least 0, got {self.half_width_m}")


@dataclass(frozen=True, eq=False)
class DrawProfiles:
    """The profiles of one draw.

    Attributes:
        ground: Bare-ground profile: 1 m steps on the 1 m DEM, 10 m on the metro
            DEM. Line of sight, Fresnel, knife edge and ITM read it.
        p1812: P.1812's 10 m profile with clutter on the 1 m layers; None on
            the metro DEM, where P.1812 is null (ADR 0006 decision 4).
        dsm_filled_pixels: DSM gaps filled with clutter 0 in the cells
            `p1812` read; None without a DSM layer or without the 1 m layers.
    """

    ground: PathProfile
    p1812: PathProfile | None
    dsm_filled_pixels: int | None


@dataclass(frozen=True, kw_only=True)
class DrawQuantities:
    """What the models computed for one draw.

    Attributes:
        values: Numbers summarised by median and interquartile range, such as
            a loss in dB or a clearance fraction. Never NaN.
        flags: Yes or no results summarised as the share of draws that are
            true, such as line of sight.
    """

    values: Mapping[str, float]
    flags: Mapping[str, bool] = field(default_factory=dict)


ProfileBuilder = Callable[[XY, XY], DrawProfiles]
"""Builds a draw's profiles from its transmitter and receiver points, EPSG:26915."""

QuantityFunction = Callable[[DrawProfiles], DrawQuantities]
"""Runs the models on one draw's profiles."""


@dataclass(frozen=True, kw_only=True)
class MetroProfileBuilder:
    """Profiles from the metro 10 m DEM: ground only, no clutter, no P.1812.

    Attributes:
        dem: Open metro DEM, north-up in EPSG:26915.
        step_m: Largest profile spacing in metres.
    """

    dem: DatasetReader
    step_m: float = METRO_STEP_M

    def __call__(self, tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
        """Build one draw's ground profile.

        Args:
            tx_xy_m: Transmitter easting and northing in EPSG:26915, metres.
            rx_xy_m: Receiver easting and northing in EPSG:26915, metres.

        Returns:
            The ground profile, with `p1812` and `dsm_filled_pixels` None.

        Raises:
            RefusedDrawError: "dem_gap" when the profile reads a DEM gap.
            OutOfExtentError: if the path leaves the DEM.
            ZeroLengthPathError: if the endpoints coincide.
        """
        ground = sample_profile(self.dem, tx_xy_m, rx_xy_m, self.step_m)
        refuse_ground_gap(ground)
        return DrawProfiles(ground=ground, p1812=None, dsm_filled_pixels=None)


@dataclass(frozen=True, kw_only=True)
class FineProfileBuilder:
    """Profiles from the 1 m layers: 1 m ground and P.1812's 10 m cell profile.

    Attributes:
        layers: The run's open 1 m layers.
        step_m: Largest ground profile spacing in metres.
        cell_step_m: Largest spacing of P.1812's profile in metres.
    """

    layers: FineLayers
    step_m: float = FINE_STEP_M
    cell_step_m: float = CELL_STEP_M

    def __call__(self, tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
        """Build one draw's two profiles; P.1812's ends share the 1 m ground.

        Args:
            tx_xy_m: Transmitter easting and northing in EPSG:26915, metres.
            rx_xy_m: Receiver easting and northing in EPSG:26915, metres.

        Returns:
            Both profiles and the filled DSM pixel count.

        Raises:
            RefusedDrawError: "dem_gap" or "empty_cell". A cell whose clutter
                is missing is an empty cell, so this path never raises
                "clutter_nan".
            OutOfExtentError: if the path leaves the 1 m DEM.
            ZeroLengthPathError: if the endpoints coincide.
        """
        ground = sample_profile(self.layers.dem, tx_xy_m, rx_xy_m, self.step_m)
        refuse_ground_gap(ground)
        cells = cell_profile(
            self.layers,
            tx_xy_m,
            rx_xy_m,
            tx_ground_m=float(ground.ground_m[0]),
            rx_ground_m=float(ground.ground_m[-1]),
            step_m=self.cell_step_m,
        )
        return DrawProfiles(
            ground=ground, p1812=cells.profile, dsm_filled_pixels=cells.dsm_filled_pixels
        )


@dataclass(frozen=True, eq=False)
class BuildingPixels:
    """Which 1 m pixels are buildings (ADR 0006, addition C).

    A building pixel is a pixel of the run's 1 m grid whose centre lies inside
    an OpenStreetMap building footprint; the mask is rasterised elsewhere.

    Attributes:
        mask: True on building pixels, one value per pixel of the 1 m grid.
        transform: The grid's affine transform, north-up, EPSG:26915 metres.
    """

    mask: BoolArray
    transform: Affine

    def __post_init__(self) -> None:
        """Check the grid.

        Raises:
            ValueError: if the mask is not 2-D or the transform is not north-up.
        """
        if self.mask.ndim != 2:
            raise ValueError("the building mask must be 2-D")
        if self.transform.b != 0.0 or self.transform.d != 0.0 or self.transform.e >= 0.0:
            raise ValueError("the building mask must be on a north-up grid")

    def require_grid_of(self, dem: DatasetReader) -> None:
        """Check the mask is on a DEM's grid, pixel for pixel.

        Args:
            dem: The open DEM the draws' profiles read.

        Raises:
            ValueError: if the shape or transform differs from the DEM's; the
                metro DEM never matches, so building draws stay null there.
        """
        if self.mask.shape != dem.shape or self.transform != dem.transform:
            raise ValueError(f"the building mask is not on the grid of {Path(dem.name).name}")

    def contains(self, xy_m: XY) -> bool:
        """Whether a point lies on a building pixel.

        Args:
            xy_m: Easting and northing in EPSG:26915, metres.

        Returns:
            True when the pixel holding the point is a building pixel.

        Raises:
            OutOfExtentError: if the point lies outside the mask.
        """
        col = math.floor((xy_m[0] - self.transform.c) / self.transform.a)
        row = math.floor((xy_m[1] - self.transform.f) / self.transform.e)
        rows, cols = self.mask.shape
        if not (0 <= row < rows and 0 <= col < cols):
            raise OutOfExtentError("endpoint lies outside the building mask")
        return bool(self.mask[row, col])


@dataclass(frozen=True, kw_only=True)
class DrawSummary:
    """One link's quantities over its unrefused draws.

    Attributes:
        link_id: The caller's link key.
        seed: Seed of the draws (`draw_seed`).
        endpoint_draws: Number of draws made.
        refusals: Refused draws per reason, every reason present; for the run
            report, not a column.
        medians: Median of each value quantity.
        iqrs: Interquartile range (75th minus 25th percentile, linear
            interpolation) of each value quantity.
        shares: Share of draws where each flag is true.
        tx_building_draws: Draws, refused or not, whose transmitter lands on a
            building pixel; None without a building mask.
        rx_building_draws: The same for the receiver.
        dsm_filled_pixels_median: Median of the filled DSM pixels per draw;
            None without a DSM, without the 1 m layers or with no draw left.
    """

    link_id: str
    seed: int
    endpoint_draws: int
    refusals: dict[RefusalReason, int]
    medians: dict[str, float]
    iqrs: dict[str, float]
    shares: dict[str, float]
    tx_building_draws: int | None
    rx_building_draws: int | None
    dsm_filled_pixels_median: float | None

    @property
    def refused_draws(self) -> int:
        """Draws refused for every model, from 0 to `endpoint_draws`."""
        return sum(self.refusals.values())

    def median(self, name: str) -> float | None:
        """Median of a value quantity.

        Args:
            name: A name the quantities function reported in `values`.

        Returns:
            The median, or None when every draw was refused.

        Raises:
            KeyError: if draws were kept and none reported this name.
        """
        return self._lookup(self.medians, name)

    def iqr(self, name: str) -> float | None:
        """Interquartile range of a value quantity; None and KeyError as for `median`."""
        return self._lookup(self.iqrs, name)

    def share(self, name: str) -> float | None:
        """Share of kept draws where a flag is true; None and KeyError as for `median`."""
        return self._lookup(self.shares, name)

    def _lookup(self, summary: dict[str, float], name: str) -> float | None:
        if self.refused_draws == self.endpoint_draws:
            return None
        if name not in summary:
            raise KeyError(f"no kept draw of {self.link_id} reported {name!r}")
        return summary[name]


def draw_seed(run_seed: int, link_id: str) -> int:
    """Seed of one link's draws, from the run seed and the link id.

    The first 8 bytes of SHA-256 of "<run_seed>:<link_id>", shifted right one
    bit so the seed fits a signed 64-bit column. A hash, unlike Python's
    `hash`, gives the same seed in every process.

    Args:
        run_seed: The run's seed from its config.
        link_id: The link's key, stable across runs.

    Returns:
        An integer from 0 to 2**63 - 1.
    """
    digest = hashlib.sha256(f"{run_seed}:{link_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def draw_endpoints(
    tx: EndpointBox, rx: EndpointBox, n_draws: int, seed: int
) -> list[tuple[XY, XY]]:
    """Endpoint pairs drawn uniformly inside the two boxes.

    Each draw takes four numbers in the order tx easting, tx northing, rx
    easting, rx northing from numpy's default generator seeded with `seed`.
    With one draw the pair is the two box centres.

    Args:
        tx: Transmitter box in EPSG:26915.
        rx: Receiver box in EPSG:26915.
        n_draws: Number of pairs, at least 1.
        seed: Generator seed (`draw_seed`).

    Returns:
        (tx, rx) points in EPSG:26915, metres.

    Raises:
        ValueError: if n_draws is below 1.
    """
    if n_draws < 1:
        raise ValueError(f"n_draws must be at least 1, got {n_draws}")
    if n_draws == 1:
        return [(tx.centre_xy_m, rx.centre_xy_m)]
    unit = np.random.default_rng(seed).uniform(-1.0, 1.0, size=(n_draws, 4))
    return [(_offset(tx, u[0], u[1]), _offset(rx, u[2], u[3])) for u in unit]


def sample_link(
    link_id: str,
    tx: EndpointBox,
    rx: EndpointBox,
    builder: ProfileBuilder,
    quantities: QuantityFunction,
    *,
    run_seed: int,
    n_draws: int = DEFAULT_ENDPOINT_DRAWS,
    buildings: BuildingPixels | None = None,
) -> DrawSummary:
    """Run the models on every draw of one link and summarise the results.

    Args:
        link_id: The link's key, which with `run_seed` seeds the draws.
        tx: Transmitter box in EPSG:26915.
        rx: Receiver box in EPSG:26915.
        builder: Builds a draw's profiles from its two points.
        quantities: Runs the models on a draw's profiles.
        run_seed: The run's seed.
        n_draws: Number of draws; 1 uses the box centres.
        buildings: Building pixels of the 1 m grid; None where there is no
            OpenStreetMap snapshot or no 1 m DEM.

    Returns:
        Medians, interquartile ranges, shares, refusal counts and building
        draw counts.

    Raises:
        OutOfExtentError: if a draw leaves the DEM or the building mask.
        ZeroLengthPathError: if a draw's endpoints coincide.
        ValueError: if a quantity is NaN, draws report different quantities,
            or the building mask is not on the builder's DEM grid.
    """
    _check_building_grid(builder, buildings)
    seed = draw_seed(run_seed, link_id)
    pairs = draw_endpoints(tx, rx, n_draws, seed)
    tx_on, rx_on = _building_draws(pairs, buildings)
    refusals: dict[RefusalReason, int] = dict.fromkeys(get_args(RefusalReason), 0)
    kept: list[tuple[DrawProfiles, DrawQuantities]] = []
    for index, (tx_xy_m, rx_xy_m) in enumerate(pairs):
        try:
            draw = builder(tx_xy_m, rx_xy_m)
            kept.append((draw, _checked(quantities(draw))))
        except RefusedDrawError as err:
            refusals[err.reason] += 1
            # Safe to continue: item 11 (b) drops the draw for every model and
            # counts it in refused_draws.
            log.warning(
                "draw_refused", link_id=link_id, draw=index, reason=err.reason, detail=str(err)
            )
    medians, iqrs, shares = _summarise([result for _, result in kept])
    log.debug("link_draws_summarised", link_id=link_id, draws=n_draws, refusals=refusals)
    return DrawSummary(
        link_id=link_id,
        seed=seed,
        endpoint_draws=n_draws,
        refusals=refusals,
        medians=medians,
        iqrs=iqrs,
        shares=shares,
        tx_building_draws=tx_on,
        rx_building_draws=rx_on,
        dsm_filled_pixels_median=_filled_median([draw for draw, _ in kept]),
    )


def _offset(box: EndpointBox, unit_x: float, unit_y: float) -> XY:
    return (
        box.centre_xy_m[0] + box.half_width_m * float(unit_x),
        box.centre_xy_m[1] + box.half_width_m * float(unit_y),
    )


def _check_building_grid(builder: ProfileBuilder, buildings: BuildingPixels | None) -> None:
    """Hold the mask to the grid of the DEM a raster builder reads."""
    if buildings is None:
        return
    if isinstance(builder, FineProfileBuilder):
        buildings.require_grid_of(builder.layers.dem)
    elif isinstance(builder, MetroProfileBuilder):
        buildings.require_grid_of(builder.dem)


def _building_draws(
    pairs: list[tuple[XY, XY]], buildings: BuildingPixels | None
) -> tuple[int | None, int | None]:
    """Draws whose tx and rx land on a building pixel, counted over every draw."""
    if buildings is None:
        return None, None
    tx_on = sum(buildings.contains(tx_xy_m) for tx_xy_m, _ in pairs)
    rx_on = sum(buildings.contains(rx_xy_m) for _, rx_xy_m in pairs)
    return tx_on, rx_on


def _checked(result: DrawQuantities) -> DrawQuantities:
    """Reject a NaN quantity: it is a model bug, not a missing input."""
    for name, value in result.values.items():
        if math.isnan(value):
            raise ValueError(f"quantity {name} is NaN; a missing input must refuse the draw")
    return result


def _summarise(
    kept: list[DrawQuantities],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Medians, interquartile ranges and shares over the unrefused draws."""
    if not kept:
        return {}, {}, {}
    names, flags = set(kept[0].values), set(kept[0].flags)
    if any(set(r.values) != names or set(r.flags) != flags for r in kept):
        raise ValueError("every draw must report the same quantities")
    medians, iqrs = {}, {}
    for name in sorted(names):
        values = np.array([r.values[name] for r in kept], dtype=np.float64)
        medians[name] = float(np.median(values))
        iqrs[name] = float(np.percentile(values, 75) - np.percentile(values, 25))
    shares = {name: sum(r.flags[name] for r in kept) / len(kept) for name in sorted(flags)}
    return medians, iqrs, shares


def _filled_median(kept: list[DrawProfiles]) -> float | None:
    counts = [d.dsm_filled_pixels for d in kept if d.dsm_filled_pixels is not None]
    return float(np.median(counts)) if counts else None
