"""What the engine computes on one draw: the models' losses plus the path geometry.

`LinkQuantities` is the quantities function `terrain.draws.sample_link` calls
per draw. It runs the run's models through `terrain.path_loss.DrawModels` and
adds what the predictions carry for every run, whatever the models:

- `los_clear`, a flag: no sample of the ground, with the k = 4/3 bulge, rises
  above the ray between the antenna tops (`terrain.clearance.los_clear`);
- `fresnel_clearance_pct`: least clearance as a fraction of the first Fresnel
  radius, on the same ground profile;
- the clutter height: the median over the interior points of P.1812's 10 m
  profile, the cell means of the 1 m clutter rule (ADR 0006 items 6 to 9).
  Only the 1 m layers with a DSM or canopy have that profile, so on the
  metro DEM there is none and the column is null, never zero.

A short path can have a P.1812 profile with no interior point. When the run
lists p1812 that draw is refused as `short_profile` for every model, since
P.1812 cannot run on it (ADR 0006 item 11 (b)). Otherwise the draw keeps its
losses and only its clutter is missing (the lead's decision B2 on PR #40).
`sample_link` needs every kept draw to report the same values, so the clutter
is not one of them: it is collected in `clutter_m`, one entry per draw this
function returns for, appended only after every model has run, so it lists
exactly the draws the sampler keeps. A refusal from any model or the geometry
(`RefusedDrawError`) drops the draw for all.
"""

from dataclasses import dataclass, field

import numpy as np

from terrain.clearance import fresnel_clearance_pct, los_clear
from terrain.draws import DrawProfiles, DrawQuantities
from terrain.errors import RefusedDrawError
from terrain.path_loss import DrawModels


@dataclass(frozen=True, kw_only=True)
class LinkQuantities:
    """The run's models plus the path geometry on one link's draws.

    Attributes:
        models: The run's models, with the link's Okumura-Hata variant set.
        cells_required: True when the run lists p1812, which needs P.1812's
            profile to have interior points.
        clutter_m: Clutter height of each kept draw in metres, None for a
            draw whose P.1812 profile has no interior point or that has no
            P.1812 profile; filled as the sampler calls this function.
    """

    models: DrawModels
    cells_required: bool
    clutter_m: list[float | None] = field(default_factory=list)

    def __call__(self, draw: DrawProfiles) -> DrawQuantities:
        """Run the models and the geometry on one draw.

        Args:
            draw: The draw's ground profile and, on the 1 m layers, P.1812's.

        Returns:
            The losses as `<model>_db`, `fresnel_clearance_pct`, and the flags
            `los_clear` and `p1812_at_cap`.

        Raises:
            RefusedDrawError: as the models and the geometry raise it, and
                `short_profile` when the run lists p1812 and P.1812's profile
                has no interior point.
        """
        tx_height_m, rx_height_m = self.models.tx.height_m, self.models.rx.height_m
        clutter = self._clutter(draw)
        values = {
            "fresnel_clearance_pct": fresnel_clearance_pct(
                draw.ground, self.models.frequency_mhz, tx_height_m, rx_height_m
            )
        }
        flags = {"los_clear": los_clear(draw.ground, tx_height_m, rx_height_m)}
        losses = self.models(draw)
        self.clutter_m.append(clutter)
        return DrawQuantities(values={**losses.values, **values}, flags={**losses.flags, **flags})

    def clutter_median_m(self) -> float | None:
        """Median clutter height over the kept draws that have one, metres; None without any."""
        heights = [h for h in self.clutter_m if h is not None]
        return float(np.median(heights)) if heights else None

    def _clutter(self, draw: DrawProfiles) -> float | None:
        profile = draw.p1812
        if profile is None:
            return None
        if profile.clutter_m is None:
            raise ValueError("the 1 m layers' profile has no clutter")
        interior = profile.clutter_m[1:-1]
        if interior.size > 0:
            return float(np.median(interior))
        if self.cells_required:
            raise RefusedDrawError(
                "short_profile",
                f"P.1812's profile has {profile.distance_m.size} points, no interior",
            )
        return None
