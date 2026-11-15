"""What a propagation model returns for one draw (ADR 0006).

Every model behind `path_loss_db` returns a PathLossResult (ADR 0006 decision
3 as amended by item 10 (a)): the median basic transmission loss, plus
diagnostics that only some models fill. A draw that cannot be computed raises
`terrain.errors.RefusedDrawError` instead (item 11 (b)).
"""

from dataclasses import dataclass

# p1812_at_cap_share counts draws where Lb is within this of Lbs (ADR 0006 item 2 (a)).
CAP_TOLERANCE_DB = 0.1


@dataclass(frozen=True)
class PathLossResult:
    """One model's answer for one endpoint draw.

    Attributes:
        loss_db: Median basic transmission loss in dB, antenna gains excluded.
        troposcatter_loss_db: P.1812's troposcatter loss Lbs in dB, equation
            (44) of ITU-R P.1812-8; None for every other model.
        itm_warning_flags: The ITM warning bit field (NTIA `Warnings.h`), 0
            when ITM raised none; None for every other model.
    """

    loss_db: float
    troposcatter_loss_db: float | None = None
    itm_warning_flags: int | None = None

    @property
    def at_troposcatter_cap(self) -> bool | None:
        """Whether Lb is within CAP_TOLERANCE_DB of Lbs; None when the model has no Lbs."""
        if self.troposcatter_loss_db is None:
            return None
        return abs(self.loss_db - self.troposcatter_loss_db) <= CAP_TOLERANCE_DB
