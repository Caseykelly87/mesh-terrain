"""Diffraction loss on a terrain profile, the Bullington method of ITU-R P.526.

The method is ITU-R P.526-16 (11/2025, in force) section 4.5.1, "Bullington
model", equations (49) to (57); P.526-15 (10/2019) numbers them the same. Draw
the steepest line from the transmitter that touches the terrain and the steepest
from the receiver; one knife edge stands where the two meet (case 2,
trans-horizon), or, when the terrain stays below the ray (case 1, line of sight),
at the sample with the largest diffraction parameter nu. The edge's loss Luc is
J(nu), section 4.1 equation (31), and equation (57) tapers it into the Bullington
loss: Lb = Luc + (1 - exp(-Luc / 6)) (10 + 0.02 d), d in km. P.1812-8 section
4.3.1 carries the same steps as equations (13) to (21).

Left out: the spherical-earth part of P.526 section 4.5.2, which combines this
loss with a smooth-earth model, and clutter. P.1812 runs the complete method as
its own model. The ground carries the k = 4/3 earth bulge (`terrain.curvature`),
which is the term 500 Ce di (d - di) of equations (49), (51) and (53) in metres.
Every equation was read in the Recommendation PDFs on 2026-11-15 (sha256 in
docs/STATUS.md).
"""

import math

import numpy as np

from terrain.clearance import CurvedPath, curved_path
from terrain.curvature import K_FACTOR
from terrain.profile import PathProfile
from terrain.units import wavelength_m

NU_FLOOR = -0.78
"""At or below this nu, P.526-16 takes J as zero (text after equation (52))."""

# Slopes are compared with this margin so a ray grazing an obstacle takes the
# line-of-sight branch. Without it, a rounding error can send the path to the
# Bullington branch with both slopes summing to about 0, a 0/0 for the
# Bullington point. 1e-12 m/m is 4e-8 m over 40 km.
_SLOPE_TOLERANCE = 1e-12


def diffraction_parameter(height_m: float, d1_m: float, d2_m: float, frequency_mhz: float) -> float:
    """Dimensionless knife-edge parameter nu, ITU-R P.526-16 equation (26).

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
    """Loss of an ideal knife edge, J(nu), ITU-R P.526-16 equation (31).

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


def bullington_loss_db(luc_db: float, length_m: float) -> float:
    """Bullington diffraction loss from the edge loss, ITU-R P.526-16 equation (57).

    Lb = Luc + (1 - exp(-Luc / 6)) (10 + 0.02 d), with d in km.

    Args:
        luc_db: Knife-edge loss of the Bullington edge, equation (52) or (56), dB.
        length_m: Path length in metres.

    Returns:
        Loss in dB; 0 when Luc is 0.
    """
    taper = 1.0 - math.exp(-luc_db / 6.0)
    return luc_db + taper * (10.0 + 0.02 * length_m / 1000.0)


def knife_edge_db(
    profile: PathProfile,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    k_factor: float = K_FACTOR,
) -> float:
    """Bullington diffraction loss on a profile, ITU-R P.526-16 section 4.5.1.

    Case 1 (Stim < Str, equations (49) and (50)): Luc is J of the largest nu among
    the samples, (51) and (52). Case 2: the edge stands at the Bullington point,
    (53) to (56). The result is equation (57).

    Args:
        profile: Uncurved ground profile in EPSG:26915.
        frequency_mhz: Carrier frequency in MHz.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        k_factor: Effective earth radius factor; math.inf gives a flat earth.

    Returns:
        Lb in dB, 0 or more.

    Raises:
        ValueError: if the profile has no interior sample.
        RefusedDrawError: if a ground height is NaN (reason "dem_gap").
    """
    path = curved_path(profile, tx_height_m, rx_height_m, k_factor)
    slope_to_tx = float(((path.ground_m - path.tx_top_m) / path.distance_m).max())  # (49)
    slope_of_ray = (path.rx_top_m - path.tx_top_m) / path.length_m  # (50)
    if slope_to_tx <= slope_of_ray + _SLOPE_TOLERANCE:
        nu = _largest_nu(path, frequency_mhz)
    else:
        nu = _bullington_nu(path, slope_to_tx, frequency_mhz)
    return bullington_loss_db(knife_edge_loss_db(nu), path.length_m)


def _largest_nu(path: CurvedPath, frequency_mhz: float) -> float:
    """Largest nu over the samples on a line-of-sight path, P.526-16 equation (51)."""
    d1_m = path.distance_m
    d2_m = path.length_m - d1_m
    height_m = path.ground_m - path.ray_m(d1_m)
    scale = np.sqrt(2.0 * path.length_m / (wavelength_m(frequency_mhz) * d1_m * d2_m))
    return float((height_m * scale).max())


def _bullington_nu(path: CurvedPath, slope_to_tx: float, frequency_mhz: float) -> float:
    """nu at the Bullington point of a path with no line of sight.

    P.526-16 equations (53) to (55): the point is where the steepest line from
    the transmitter, slope (49), meets the steepest line from the receiver.
    """
    d2_m = path.length_m - path.distance_m
    slope_to_rx = float(((path.ground_m - path.rx_top_m) / d2_m).max())
    rise_m = path.rx_top_m - path.tx_top_m
    point_m = (rise_m + slope_to_rx * path.length_m) / (slope_to_tx + slope_to_rx)
    edge_m = path.tx_top_m + slope_to_tx * point_m
    ray_m = float(path.ray_m(np.array([point_m]))[0])
    return diffraction_parameter(edge_m - ray_m, point_m, path.length_m - point_m, frequency_mhz)
