"""Named antenna profiles: the heights and polarisation every model reads.

A profile is one record of `engine/profiles/<name>.yaml` (`docs/INTERFACE.md`,
Profiles): antenna height above ground, gain, transmit power where the profile
transmits, polarisation and a description. `path_loss_db` reads the height and
the polarisation; the gain and the power are carried so that every output names
the profile it came from, and are never applied, since every model returns
basic transmission loss (ADR 0006, addition A). A profile never carries a
frequency. This module does not read YAML; the engine does.
"""

import math
from dataclasses import dataclass

from terrain.itm import Polarisation

POLARISATIONS: tuple[Polarisation, ...] = ("horizontal", "vertical")


@dataclass(frozen=True, kw_only=True)
class AntennaProfile:
    """One end of a link, as a named profile.

    Attributes:
        name: The profile's name, `mesh_node` or `site_gateway` for the two
            that ship with the engine.
        height_m: Antenna height above the ground at the endpoint, metres.
        gain_dbi: Antenna gain in dBi, net of cable; never applied by a model.
        polarisation: "horizontal" or "vertical".
        transmit_power_dbm: Transmit power in dBm; None for a profile that only
            receives.
        description: Free text for a reader of the profile.
    """

    name: str
    height_m: float
    gain_dbi: float
    polarisation: Polarisation
    transmit_power_dbm: float | None = None
    description: str = ""

    def __post_init__(self) -> None:
        """Check the record.

        Raises:
            ValueError: for an empty name, a height that is not a positive
                finite number, a gain or power that is not finite, or an
                unknown polarisation.
        """
        if not self.name:
            raise ValueError("an antenna profile needs a name")
        if not (math.isfinite(self.height_m) and self.height_m > 0.0):
            raise ValueError(f"height_m must be positive and finite, got {self.height_m}")
        if not math.isfinite(self.gain_dbi):
            raise ValueError(f"gain_dbi must be finite, got {self.gain_dbi}")
        if self.transmit_power_dbm is not None and not math.isfinite(self.transmit_power_dbm):
            raise ValueError(f"transmit_power_dbm must be finite, got {self.transmit_power_dbm}")
        if self.polarisation not in POLARISATIONS:
            raise ValueError(
                f"polarisation must be horizontal or vertical, got {self.polarisation!r}"
            )
