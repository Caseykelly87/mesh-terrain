"""Free-space loss and Okumura-Hata, the two baselines that read no terrain.

Free-space loss is the floor every other model adds to. Okumura-Hata is an
empirical fit to Okumura's 1968 Tokyo measurements; it knows the distance, the
frequency, the two antenna heights and whether the area is urban or suburban,
and nothing of the terrain between. Its published validity is 150 to 1500 MHz,
base station 30 to 200 m, mobile 1 to 10 m and 1 to 20 km. Only the frequency
range is enforced: the validation runs put 2 m antennas at both ends (ADR 0006
decision 5.1), which is outside the base station range, so Hata is extrapolated
there and serves as a baseline, not a prediction.
"""

import math
from typing import Literal

from terrain.units import wavelength_m

HataVariant = Literal["urban", "suburban"]

HATA_URBAN_SHARE = 0.20
"""Mean building footprint share at which a link counts as urban (ADR 0006 decision 7.1)."""

HATA_MIN_MHZ = 150.0
HATA_MAX_MHZ = 1500.0


def fspl_db(distance_m: float, frequency_mhz: float) -> float:
    """Free-space basic transmission loss, ITU-R P.525-4 equation (4).

    Lbf = 20 log10(4 pi d / lambda).

    Args:
        distance_m: Path length in metres.
        frequency_mhz: Carrier frequency in MHz.

    Returns:
        Loss in dB.

    Raises:
        ValueError: if the distance or the frequency is not positive.
    """
    if not distance_m > 0.0:
        raise ValueError(f"distance_m must be positive, got {distance_m}")
    return 20.0 * math.log10(4.0 * math.pi * distance_m / wavelength_m(frequency_mhz))


def hata_db(
    distance_m: float,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    variant: HataVariant,
) -> float:
    """Median path loss from the Okumura-Hata formula.

    Hata, "Empirical formula for propagation loss in land mobile radio
    services", IEEE Trans. Veh. Technol. VT-29(3), 1980: the urban formula with
    the small and medium city mobile antenna correction a(hm), and for the
    suburban variant the urban loss less 2 (log10(f / 28))^2 + 5.4 dB. The higher
    antenna is the base station hb and the lower the mobile hm, since the formula
    is not written for two antennas of equal role.

    Args:
        distance_m: Path length in metres.
        frequency_mhz: Carrier frequency in MHz, 150 to 1500.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        variant: "urban" or "suburban", from `hata_variant`.

    Returns:
        Loss in dB.

    Raises:
        ValueError: for a frequency outside 150 to 1500 MHz, a distance or
            antenna height that is not positive, or an unknown variant.
    """
    if not HATA_MIN_MHZ <= frequency_mhz <= HATA_MAX_MHZ:
        raise ValueError(f"Hata is valid from 150 to 1500 MHz, got {frequency_mhz}")
    if not (distance_m > 0.0 and tx_height_m > 0.0 and rx_height_m > 0.0):
        raise ValueError("distance and antenna heights must be positive")
    if variant not in ("urban", "suburban"):
        raise ValueError(f"unknown Hata variant {variant!r}")
    base_m, mobile_m = max(tx_height_m, rx_height_m), min(tx_height_m, rx_height_m)
    log_f = math.log10(frequency_mhz)
    mobile_correction_db = (1.1 * log_f - 0.7) * mobile_m - (1.56 * log_f - 0.8)
    urban_db = (
        69.55
        + 26.16 * log_f
        - 13.82 * math.log10(base_m)
        - mobile_correction_db
        + (44.9 - 6.55 * math.log10(base_m)) * math.log10(distance_m / 1000.0)
    )
    if variant == "urban":
        return urban_db
    return urban_db - 2.0 * math.log10(frequency_mhz / 28.0) ** 2 - 5.4


def hata_variant(tx_building_share: float, rx_building_share: float) -> HataVariant:
    """Choose the Okumura-Hata variant from the building shares at the two ends.

    Urban when the mean of the two endpoints' building footprint shares within
    100 m reaches 0.20, suburban otherwise (ADR 0006 decision 7.1, contract
    `ter_06`). Each share is the median over the endpoint draws.

    Args:
        tx_building_share: Footprint share near the transmitter, 0 to 1.
        rx_building_share: Footprint share near the receiver, 0 to 1.

    Returns:
        "urban" or "suburban".

    Raises:
        ValueError: if a share is NaN or outside 0 to 1.
    """
    for share in (tx_building_share, rx_building_share):
        if not 0.0 <= share <= 1.0:
            raise ValueError(f"building share must be in [0, 1], got {share}")
    mean_share = (tx_building_share + rx_building_share) / 2.0
    return "urban" if mean_share >= HATA_URBAN_SHARE else "suburban"
