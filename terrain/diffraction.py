"""Knife-edge diffraction loss on a terrain profile, ITU-R P.526 with Bullington.

A single obstacle is treated as an ideal knife edge (ITU-R P.526-15 section 4.1).
Several obstacles are replaced by one equivalent edge with the Bullington
construction: draw the steepest line from the transmitter that touches the
terrain and the steepest from the receiver; the edge stands where the two meet.
The construction follows ITU-R P.1812-6 section 4.3.1, equations (13) to (20),
which is the Bullington part of the general path method P.526 also describes.

Deliberately left out: the empirical distance correction of P.1812-6 equation
(21), Lbull = Luc + (1 - exp(-Luc / 6)) (10 + 0.02 d), and the spherical-earth
part of the delta-Bullington method. Both belong to P.1812, which the project
runs as its own model; here the loss is J(nu) of the equivalent edge, so a single
ridge gives exactly the P.526 knife-edge figure. The ground carries the k = 4/3
earth bulge (`terrain.curvature`) and no clutter.
"""

import math

import numpy as np

from terrain.clearance import require_complete_ground
from terrain.curvature import K_FACTOR, earth_bulge_m
from terrain.profile import FloatArray, PathProfile
from terrain.units import wavelength_m

NU_FLOOR = -0.78
"""Below this nu, P.526 equation (31) is not valid and the loss is taken as 0 dB."""

# Slopes are compared with this margin so a ray grazing an obstacle takes the
# line-of-sight branch. Without it, a rounding error can send the path to the
# Bullington branch with both slopes summing to about 0, a 0/0 for the
# Bullington point. 1e-12 m/m is 4e-8 m over 40 km.
_SLOPE_TOLERANCE = 1e-12


def diffraction_parameter(height_m: float, d1_m: float, d2_m: float, frequency_mhz: float) -> float:
    """Dimensionless knife-edge parameter nu, ITU-R P.526-15 equation (26).

    nu = h sqrt((2 / lambda) (1 / d1 + 1 / d2)).

    Args:
        height_m: Height of the edge above the ray between the antennas, metres;
            negative when the edge is below it.
        d1_m: Distance from the transmitter to the edge, metres.
        d2_m: Distance from the edge to the receiver, metres.
        frequency_mhz: Carrier frequency in MHz.

    Returns:
        nu, dimensionless.
    """
    wavelength = wavelength_m(frequency_mhz)
    return height_m * math.sqrt(2.0 / wavelength * (1.0 / d1_m + 1.0 / d2_m))


def knife_edge_loss_db(nu: float) -> float:
    """Loss of an ideal knife edge, J(nu), ITU-R P.526-15 equation (31).

    J(nu) = 6.9 + 20 log10(sqrt((nu - 0.1)^2 + 1) + nu - 0.1) for nu > -0.78,
    and 0 dB otherwise. J(0), grazing incidence, is about 6 dB.

    Args:
        nu: Diffraction parameter from equation (26).

    Returns:
        Loss in dB, never negative.
    """
    if nu <= NU_FLOOR:
        return 0.0
    loss = 6.9 + 20.0 * math.log10(math.sqrt((nu - 0.1) ** 2 + 1.0) + nu - 0.1)
    return max(0.0, loss)


def knife_edge_db(
    profile: PathProfile,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    k_factor: float = K_FACTOR,
) -> float:
    """Diffraction loss of the Bullington equivalent knife edge on a profile.

    When no sample of curved ground rises above the ray (P.1812-6 equation (14)
    case 1), the loss is J of the largest nu among the samples, equation (15).
    Otherwise the edge stands at the Bullington point, equations (17) to (20).

    Args:
        profile: Uncurved ground profile in EPSG:26915.
        frequency_mhz: Carrier frequency in MHz.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        k_factor: Effective earth radius factor; math.inf gives a flat earth.

    Returns:
        Loss in dB, 0 or more.

    Raises:
        ValueError: if the profile has no interior sample.
        RefusedDrawError: if a ground height is NaN (reason "dem_gap").
    """
    require_complete_ground(profile)
    length_m = profile.length_m
    distance = profile.distance_m[1:-1]
    ground = profile.ground_m[1:-1] + earth_bulge_m(distance, length_m, k_factor)
    tx_top_m = float(profile.ground_m[0]) + tx_height_m
    rx_top_m = float(profile.ground_m[-1]) + rx_height_m
    slope_to_tx = float(((ground - tx_top_m) / distance).max())  # equation (13)
    slope_of_ray = (rx_top_m - tx_top_m) / length_m  # equation (14)
    if slope_to_tx <= slope_of_ray + _SLOPE_TOLERANCE:
        nu = _largest_nu(distance, ground, tx_top_m, rx_top_m, length_m, frequency_mhz)
    else:
        nu = _bullington_nu(distance, ground, tx_top_m, rx_top_m, length_m, frequency_mhz)
    return knife_edge_loss_db(nu)


def _largest_nu(
    distance: FloatArray,
    ground: FloatArray,
    tx_top_m: float,
    rx_top_m: float,
    length_m: float,
    frequency_mhz: float,
) -> float:
    """Largest nu over the samples on a line-of-sight path, P.1812-6 equation (15)."""
    ray_m = tx_top_m + (rx_top_m - tx_top_m) * distance / length_m
    scale = np.sqrt(
        2.0 * length_m / (wavelength_m(frequency_mhz) * distance * (length_m - distance))
    )
    return float(((ground - ray_m) * scale).max())


def _bullington_nu(
    distance: FloatArray,
    ground: FloatArray,
    tx_top_m: float,
    rx_top_m: float,
    length_m: float,
    frequency_mhz: float,
) -> float:
    """nu at the Bullington point of a path with no line of sight.

    P.1812-6 equations (13), (17), (18) and (20): the point is where the steepest
    line from the transmitter meets the steepest line from the receiver.
    """
    slope_to_tx = float(((ground - tx_top_m) / distance).max())
    slope_to_rx = float(((ground - rx_top_m) / (length_m - distance)).max())
    point_m = (rx_top_m - tx_top_m + slope_to_rx * length_m) / (slope_to_tx + slope_to_rx)
    edge_m = tx_top_m + slope_to_tx * point_m
    ray_m = tx_top_m + (rx_top_m - tx_top_m) * point_m / length_m
    return diffraction_parameter(edge_m - ray_m, point_m, length_m - point_m, frequency_mhz)
