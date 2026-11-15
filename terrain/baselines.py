"""Free-space loss and Okumura-Hata, the two baselines that read no terrain.

Free-space loss is the floor every other model adds to. Okumura-Hata is an
empirical fit to Okumura's 1968 Tokyo measurements; it knows the distance, the
frequency, the two antenna heights and whether the area is urban or suburban,
and nothing of the terrain between.

Hata's source is M. Hata, "Empirical formula for propagation loss in land mobile
radio services", IEEE Trans. Veh. Technol., vol. VT-29, no. 3, pp. 317-325,
Aug. 1980, doi:10.1109/T-VT.1980.23859. The paper has not been read for this
code, so no equation number is cited; the formula and validity ranges are as the
Wikipedia article "Hata model" (read 2026-11-15) gives them, citing that paper:
150 to 1500 MHz, base station 30 to 200 m, mobile 1 to 10 m, 1 to 10 km. Only
the frequency range is enforced: the validation runs put 2 m antennas at both
ends (ADR 0006 decision 5.1), below the base station range, so Hata is
extrapolated there and serves as a baseline, not a prediction.
"""

import math
from typing import Literal

from terrain.errors import ZeroLengthPathError
from terrain.units import wavelength_m

HataVariant = Literal["urban", "suburban"]

HATA_URBAN_SHARE = 0.20
"""Mean building footprint share at which a link counts as urban (ADR 0006 decision 7.1)."""

HATA_MIN_MHZ = 150.0
HATA_MAX_MHZ = 1500.0


def fspl_db(distance_m: float, frequency_mhz: float) -> float:
    """Free-space basic transmission loss, ITU-R P.525-5 section 2.3, equation (5).

    Lbf = 20 log10(4 pi d / lambda).

    Args:
        distance_m: Path length in metres.
        frequency_mhz: Carrier frequency in MHz.

    Returns:
        Loss in dB.

    Raises:
        ZeroLengthPathError: if the distance is 0.
        ValueError: if the distance is negative or the frequency not positive.
    """
    require_path_length(distance_m)
    return 20.0 * math.log10(4.0 * math.pi * distance_m / wavelength_m(frequency_mhz))


def hata_db(
    distance_m: float,
    frequency_mhz: float,
    tx_height_m: float,
    rx_height_m: float,
    variant: HataVariant,
) -> float:
    """Median path loss from the Okumura-Hata formula.

    Hata (1980, module docstring): urban LU = 69.55 + 26.16 log f - 13.82 log hB
    - CH + (44.9 - 6.55 log hB) log d, with the small or medium city correction
    CH = 0.8 + (1.1 log f - 0.7) hM - 1.56 log f (f in MHz, d in km); suburban
    LU - 2 (log(f / 28))^2 - 5.4. The higher antenna is the base station hB.

    Args:
        distance_m: Path length in metres.
        frequency_mhz: Carrier frequency in MHz, 150 to 1500.
        tx_height_m: Transmitting antenna height above ground, metres.
        rx_height_m: Receiving antenna height above ground, metres.
        variant: "urban" or "suburban", from `hata_variant`.

    Returns:
        Loss in dB.

    Raises:
        ZeroLengthPathError: if the distance is 0.
        ValueError: for a frequency outside 150 to 1500 MHz, a negative distance,
            an antenna height that is not positive, or an unknown variant.
    """
    _check_hata_inputs(distance_m, frequency_mhz, tx_height_m, rx_height_m, variant)
    base_m, mobile_m = max(tx_height_m, rx_height_m), min(tx_height_m, rx_height_m)
    urban_db = _hata_urban_db(distance_m, frequency_mhz, base_m, mobile_m)
    if variant == "urban":
        return urban_db
    return urban_db - 2.0 * math.log10(frequency_mhz / 28.0) ** 2 - 5.4


def _check_hata_inputs(
    distance_m: float, frequency_mhz: float, tx_height_m: float, rx_height_m: float, variant: str
) -> None:
    if not HATA_MIN_MHZ <= frequency_mhz <= HATA_MAX_MHZ:
        raise ValueError(f"Hata is valid from 150 to 1500 MHz, got {frequency_mhz}")
    require_path_length(distance_m)
    if not (tx_height_m > 0.0 and rx_height_m > 0.0):
        raise ValueError("antenna heights must be positive")
    if variant not in ("urban", "suburban"):
        raise ValueError(f"unknown Hata variant {variant!r}")


def _hata_urban_db(
    distance_m: float, frequency_mhz: float, base_m: float, mobile_m: float
) -> float:
    """Hata's urban loss with the small and medium city a(hm); distance in metres."""
    log_f = math.log10(frequency_mhz)
    log_base = math.log10(base_m)
    mobile_correction_db = (1.1 * log_f - 0.7) * mobile_m - (1.56 * log_f - 0.8)
    distance_term_db = (44.9 - 6.55 * log_base) * math.log10(distance_m / 1000.0)
    return 69.55 + 26.16 * log_f - 13.82 * log_base - mobile_correction_db + distance_term_db


def require_path_length(distance_m: float) -> None:
    """Check a path length before a loss is computed from it.

    Args:
        distance_m: Path length in metres.

    Raises:
        ZeroLengthPathError: if the length is 0.
        ValueError: if the length is negative or not a number.
    """
    if distance_m == 0.0:
        raise ZeroLengthPathError("zero-length path: the two endpoints coincide")
    if not distance_m > 0.0:
        raise ValueError(f"distance_m must be positive, got {distance_m}")


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
