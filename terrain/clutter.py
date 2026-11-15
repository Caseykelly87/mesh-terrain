"""The per-pixel clutter rule: how tall buildings and trees stand above bare ground.

Clutter is the height of what stands on the ground, in metres above it. It comes
from three 1 m layers on one grid in EPSG:26915: the DEM (bare earth), the DSM
(the first-return lidar surface, roof and tree tops, flown leaf-off) and the
canopy height map (satellite imagery, much of it leaf-on). The rule is ADR 0006
item 6 (a) with the canopy fill of item 11 (i), applied to every pixel before any
averaging (the 10 m cells are `terrain.cells`). It is P.1812's R and a covariate
in the statistics. This module does not read rasters, average cells or decide
whether a draw is kept; a pixel the rule cannot define stays NaN for the caller
to refuse.
"""

import numpy as np
import numpy.typing as npt

from terrain.errors import RefusedDrawError
from terrain.profile import FloatArray, PathProfile

BoolArray = npt.NDArray[np.bool_]


def clutter_height_m(
    dem_m: FloatArray, *, dsm_m: FloatArray | None, canopy_m: FloatArray | None
) -> FloatArray:
    """Clutter height of each pixel, ADR 0006 item 6 (a) and item 11 (i).

    In order: DSM minus DEM; a negative result raised to 0 (lidar noise on bare
    ground); 0 where the DSM has no value and the DEM has one (mostly water);
    then the canopy height where it is larger, a canopy pixel with no value
    counting as 0. A missing DSM layer counts as no value everywhere and a
    missing canopy layer as 0 everywhere, so either layer alone gives clutter.

    Args:
        dem_m: Bare-earth heights in metres, NaN where the DEM has no value.
        dsm_m: First-return surface heights in metres on the same grid, NaN where
            the DSM has no value; None when no DSM snapshot is named.
        canopy_m: Canopy heights in metres above ground on the same grid, NaN
            where the canopy layer has no value; None when none is named.

    Returns:
        Clutter in metres above ground, at least 0, and NaN exactly where the DEM
        has no value.

    Raises:
        ValueError: if both layers are None or the arrays differ in shape.
    """
    if dsm_m is None and canopy_m is None:
        raise ValueError("clutter needs a DSM or a canopy layer")
    for layer in (dsm_m, canopy_m):
        if layer is not None and layer.shape != dem_m.shape:
            raise ValueError(f"layer shape {layer.shape} differs from the DEM's {dem_m.shape}")
    surface_m = np.zeros_like(dem_m) if dsm_m is None else np.fmax(dsm_m - dem_m, 0.0)
    # fmax already maps a DSM gap to 0; a DEM gap must stay NaN, so restore it.
    surface_m = np.where(np.isnan(dem_m), np.nan, surface_m)
    canopy_or_zero_m = (
        np.zeros_like(dem_m) if canopy_m is None else np.nan_to_num(canopy_m, nan=0.0)
    )
    clutter: FloatArray = np.maximum(surface_m, canopy_or_zero_m)
    return clutter


def dsm_filled_mask(dem_m: FloatArray, dsm_m: FloatArray) -> BoolArray:
    """Pixels where the rule fills a DSM gap with 0 (ADR 0006 decision 6).

    Args:
        dem_m: Bare-earth heights in metres, NaN where the DEM has no value.
        dsm_m: Surface heights in metres on the same grid, NaN where it has none.

    Returns:
        True where the DSM has no value and the DEM has one.
    """
    mask: BoolArray = np.isnan(dsm_m) & ~np.isnan(dem_m)
    return mask


def require_complete_clutter(profile: PathProfile) -> None:
    """Refuse a profile that still holds a missing clutter height.

    Decision 6 refuses any NaN left in R after the fills, because Py1812 returns
    finite, wrong losses for some of them (ADR 0006 question 6, spike run 3).

    Args:
        profile: Profile in EPSG:26915; a profile without clutter passes.

    Raises:
        RefusedDrawError: reason "clutter_nan", if any clutter height is NaN.
    """
    if profile.clutter_m is None:
        return
    missing = np.flatnonzero(np.isnan(profile.clutter_m))
    if missing.size:
        raise RefusedDrawError("clutter_nan", f"no clutter height at profile index {missing[0]}")
