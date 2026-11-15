"""Exceptions the terrain functions raise for a path they cannot compute.

ZeroLengthPathError is the one ValueError here: the feature asset catches it,
and only it, to record a link whose two endpoints coincide, so any other
ValueError (malformed input, a sampler bug) still fails the run. The two
TerrainError classes do not derive from ValueError, so that catch cannot take a
link that left the DEM
(recorded per link as `terrain_status = out_of_extent`) or a draw refused for a
missing value (dropped for every model and counted in `refused_draws`, ADR 0006
item 11). This module only defines the classes; deciding which draw to refuse is
the endpoint sampler's job.
"""

from typing import Literal, get_args

RefusalReason = Literal["dem_gap", "empty_cell", "clutter_nan", "short_profile"]
"""Why a draw was refused (ADR 0006 item 11).

dem_gap: a ground profile point reads a DEM pixel without a value.
empty_cell: a 10 m cell of P.1812's profile holds a pixel without a value (item 7).
clutter_nan: a NaN is left in the clutter heights after the fills of decision 6.
short_profile: the profile has too few samples for a model, for the geometry
    functions fewer than three (no interior sample); Casey added this cause on
    PR #30, 2026-11-15.
"""


class ZeroLengthPathError(ValueError):
    """The two endpoints of a path coincide, so it has no length."""


class TerrainError(Exception):
    """Base class for paths the terrain functions cannot compute."""


class OutOfExtentError(TerrainError):
    """A profile point lies outside the raster it is sampled from."""


class RefusedDrawError(TerrainError):
    """An endpoint draw needs a height or clutter value that is missing.

    Attributes:
        reason: One of the RefusalReason values.
    """

    def __init__(self, reason: RefusalReason, detail: str) -> None:
        """Record the reason and a human-readable detail.

        Args:
            reason: Why the draw was refused.
            detail: Where the missing value is, for the log.

        Raises:
            ValueError: if reason is not a RefusalReason value.
        """
        if reason not in get_args(RefusalReason):
            raise ValueError(f"unknown refusal reason {reason!r}")
        super().__init__(f"{reason}: {detail}")
        self.reason: RefusalReason = reason
