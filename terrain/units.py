"""Physical constants and the frequency to wavelength conversion."""

SPEED_OF_LIGHT_M_PER_S = 299_792_458.0


def wavelength_m(frequency_mhz: float) -> float:
    """Free-space wavelength.

    Args:
        frequency_mhz: Carrier frequency in MHz.

    Returns:
        Wavelength in metres.

    Raises:
        ValueError: if the frequency is not positive.
    """
    if not frequency_mhz > 0.0:
        raise ValueError(f"frequency_mhz must be positive, got {frequency_mhz}")
    return SPEED_OF_LIGHT_M_PER_S / (frequency_mhz * 1e6)
