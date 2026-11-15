"""Line of sight, obstruction height and first Fresnel zone clearance on a profile.

Each function compares the ground, raised by the earth bulge for an effective
earth radius factor k (4/3 by default, `terrain.curvature`), with the straight ray
from the transmitting antenna to the receiving one. Antennas stand on the ground
at the first and last profile points. Only interior samples count: at the two
ends the ray is the antenna height above the ground by construction. The ground
is bare earth; clutter enters through P.1812 and the statistics, not here.
"""

import numpy as np

from terrain.curvature import K_FACTOR, earth_bulge_m
from terrain.errors import RefusedDrawError
from terrain.profile import FloatArray, PathProfile
from terrain.units import wavelength_m


def clearance_m(
    profile: PathProfile, tx_height_m: float, rx_height_m: float, k_factor: float = K_FACTOR
) -> FloatArray:
    """Height of the ray above the curved ground at each interior sample.

    Args:
        profile: Uncurved ground profile in EPSG:26915.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        k_factor: Effective earth radius factor; math.inf gives a flat earth.

    Returns:
        Metres at samples 1 to n-2; negative where the ground rises above the ray.

    Raises:
        ValueError: if the profile has no interior sample.
        RefusedDrawError: if a ground height is NaN (reason "dem_gap").
    """
    require_complete_ground(profile)
    distance = profile.distance_m[1:-1]
    tx_top_m = float(profile.ground_m[0]) + tx_height_m
    rx_top_m = float(profile.ground_m[-1]) + rx_height_m
    ray_m = tx_top_m + (rx_top_m - tx_top_m) * distance / profile.length_m
    ground_m = profile.ground_m[1:-1] + earth_bulge_m(distance, profile.length_m, k_factor)
    clearance: FloatArray = ray_m - ground_m
    return clearance


def require_complete_ground(profile: PathProfile) -> None:
    """Check the profile has an interior sample and no missing ground height.

    Raises:
        ValueError: if the profile has fewer than three samples.
        RefusedDrawError: if a ground height is NaN (reason "dem_gap").
    """
    if profile.distance_m.size < 3:
        raise ValueError("the profile needs at least one interior sample")
    missing = np.flatnonzero(~np.isfinite(profile.ground_m))
    if missing.size:
        raise RefusedDrawError("dem_gap", f"no ground height at profile index {missing[0]}")


def los_clear(
    profile: PathProfile, tx_height_m: float, rx_height_m: float, k_factor: float = K_FACTOR
) -> bool:
    """Whether no sample of the curved ground rises above the ray.

    Args and Raises as `clearance_m`.
    """
    return bool(np.all(clearance_m(profile, tx_height_m, rx_height_m, k_factor) >= 0.0))


def obstruction_m(
    profile: PathProfile, tx_height_m: float, rx_height_m: float, k_factor: float = K_FACTOR
) -> float:
    """Height of the highest sample of curved ground above the ray.

    Args and Raises as `clearance_m`.

    Returns:
        Metres; positive when the path is obstructed, negative when clear.
    """
    return float(-clearance_m(profile, tx_height_m, rx_height_m, k_factor).min())


def fresnel_radius_m(
    distance_m: FloatArray, path_length_m: float, frequency_mhz: float
) -> FloatArray:
    """Radius of the first Fresnel zone, ITU-R P.526-15 section 2.1, equation (2).

    R1 = sqrt(lambda d1 d2 / (d1 + d2)).

    Args:
        distance_m: Distances d1 from the transmitter, metres.
        path_length_m: Path length d1 + d2, metres.
        frequency_mhz: Carrier frequency in MHz.

    Returns:
        Radius in metres at each distance, 0 at both ends.
    """
    wavelength = wavelength_m(frequency_mhz)
    product = distance_m * (path_length_m - distance_m)
    radius: FloatArray = np.sqrt(wavelength * product / path_length_m)
    return radius


def fresnel_clearance_pct(
    profile: PathProfile,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    k_factor: float = K_FACTOR,
) -> float:
    """Least clearance along the path as a fraction of the first Fresnel radius.

    The name keeps the column name `fresnel_clearance_pct`; the value is a
    fraction, capped at 1 (contract `ter_03`).

    Args:
        profile: Uncurved ground profile in EPSG:26915.
        frequency_mhz: Carrier frequency in MHz.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        k_factor: Effective earth radius factor; math.inf gives a flat earth.

    Returns:
        A value in [-inf, 1]. 1.0 means the first Fresnel zone is fully clear;
        0.0 means the ground touches the ray; negative means obstructed.

    Raises:
        ValueError: if the profile has no interior sample.
        RefusedDrawError: if a ground height is NaN (reason "dem_gap").
    """
    clearance = clearance_m(profile, tx_height_m, rx_height_m, k_factor)
    radius = fresnel_radius_m(profile.distance_m[1:-1], profile.length_m, frequency_mhz)
    return min(1.0, float((clearance / radius).min()))
