"""Earth bulge for an effective earth radius, applied by the geometry functions.

Profiles from `terrain.profile` are uncurved ground (ADR 0006 decision 3). Line
of sight, Fresnel clearance and the knife-edge code add this bulge to the ground
before comparing it with the straight ray between the antennas, which is the same
as bending the ray down by the earth's curvature. ITM and P.1812 apply their own
effective earth radius, so a profile passed to them must never carry this bulge.
"""

import numpy as np

from terrain.profile import FloatArray

EARTH_RADIUS_M = 6_371_000.0
"""Earth radius of ITU-R P.526-16 (ae = k x 6371 km, after equation (44)) and P.1812-8."""

K_FACTOR = 4.0 / 3.0
"""Effective earth radius factor for the standard atmosphere."""


def earth_bulge_m(
    distance_m: FloatArray, path_length_m: float, k_factor: float = K_FACTOR
) -> FloatArray:
    """Height of the curved earth above the chord between the two endpoints.

    h = d1 d2 / (2 k a), with d1 the distance from the transmitter, d2 = d - d1 and
    a the earth radius. It is the term 500 Ce di (d - di) of ITU-R P.526-16
    equations (49), (51) and (53) written in metres instead of kilometres.

    Args:
        distance_m: Distances from the transmitter along the path, metres.
        path_length_m: Total path length d, metres.
        k_factor: Effective earth radius factor; math.inf gives a flat earth.

    Returns:
        Bulge in metres at each distance, 0 at both ends.
    """
    d2_m = path_length_m - distance_m
    bulge: FloatArray = distance_m * d2_m / (2.0 * k_factor * EARTH_RADIUS_M)
    return np.asarray(bulge, dtype=np.float64)
