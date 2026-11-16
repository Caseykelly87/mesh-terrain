"""What the engine computes on one draw: the models' losses plus the path geometry.

`LinkQuantities` is the quantities function `terrain.draws.sample_link` calls
per draw. It runs the run's models through `terrain.path_loss.DrawModels` and
adds three numbers the predictions carry for every run, whatever the models:

- `los_clear`, a flag: no sample of the ground, with the k = 4/3 bulge, rises
  above the ray between the antenna tops (`terrain.clearance.los_clear`);
- `fresnel_clearance_pct`: least clearance as a fraction of the first Fresnel
  radius, on the same ground profile;
- `clutter_height_m`: the median clutter height over the interior points of
  P.1812's 10 m profile, the cell means of the 1 m clutter rule (ADR 0006
  items 6 to 9). Only the 1 m layers have that profile, so on the metro DEM
  the value is absent and the column is null, never zero.

A draw refused by any of them (`RefusedDrawError`) is refused for all.
"""

from dataclasses import dataclass

import numpy as np

from terrain.clearance import fresnel_clearance_pct, los_clear
from terrain.draws import DrawProfiles, DrawQuantities
from terrain.errors import RefusedDrawError
from terrain.path_loss import DrawModels


@dataclass(frozen=True, kw_only=True)
class LinkQuantities:
    """The run's models plus the path geometry on one draw.

    Attributes:
        models: The run's models, with the link's Okumura-Hata variant set.
    """

    models: DrawModels

    def __call__(self, draw: DrawProfiles) -> DrawQuantities:
        """Run the models and the geometry on one draw.

        Args:
            draw: The draw's ground profile and, on the 1 m layers, P.1812's.

        Returns:
            The losses as `<model>_db`, `fresnel_clearance_pct`,
            `clutter_height_m` on the 1 m layers, and the flags `los_clear`
            and `p1812_at_cap`.

        Raises:
            RefusedDrawError: as the models and the geometry raise it, and
                `short_profile` when P.1812's profile has no interior point.
        """
        tx_height_m, rx_height_m = self.models.tx.height_m, self.models.rx.height_m
        ground = draw.ground
        values = {
            "fresnel_clearance_pct": fresnel_clearance_pct(
                ground, self.models.frequency_mhz, tx_height_m, rx_height_m
            )
        }
        flags = {"los_clear": los_clear(ground, tx_height_m, rx_height_m)}
        if draw.p1812 is not None:
            values["clutter_height_m"] = _interior_clutter_median(draw)
        losses = self.models(draw)
        return DrawQuantities(values={**losses.values, **values}, flags={**losses.flags, **flags})


def _interior_clutter_median(draw: DrawProfiles) -> float:
    profile = draw.p1812
    if profile is None or profile.clutter_m is None:
        raise ValueError("the 1 m layers' profile has no clutter")
    interior = profile.clutter_m[1:-1]
    if interior.size == 0:
        raise RefusedDrawError(
            "short_profile", f"P.1812's profile has {profile.distance_m.size} points, no interior"
        )
    return float(np.median(interior))
