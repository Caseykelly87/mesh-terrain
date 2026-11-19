"""`crossing.md`'s section on paths between real endpoints, built from made-up reports.

The section says the run has no real receiver only when the run was prepared
with `--no-real-receivers`, never because a list of real receivers happens to
be empty. Every pseudonym and hash here is made up.
"""

from pathlib import Path

from engine.prepare.report import CrossingReport, Site, crossing_markdown
from engine.snapshots import NamedSnapshot

SITE = "a1" * 32


def snapshot(role: str) -> NamedSnapshot:
    return NamedSnapshot(
        role=role,
        snapshot_id=f"{role}-synthetic",
        folder=Path(f"{role}-synthetic"),
        manifest_sha256="0" * 64,
        files=(),
    )


def report(*, real_receivers: bool) -> CrossingReport:
    return CrossingReport(
        tile="x72y429",
        dem=snapshot("dem"),
        dsm=snapshot("dsm"),
        threshold_m=60.0,
        within_m=10.0,
        tall_cells=0,
        sites=[Site(site_id=SITE, role="both_ends_gateway", reach_m=10.0, in_run=True)],
        receiver_reaches={},
        real=[],
        grid=[],
        real_receivers=real_receivers,
    )


def real_section(text: str) -> str:
    return text.split("## Paths between real endpoints")[1].split("## Grid paths")[0]


def test_a_run_without_real_receivers_says_so_and_lists_no_table() -> None:
    section = real_section(crossing_markdown(report(real_receivers=False)))
    assert "no real receiver" in section
    assert "--no-real-receivers" in section
    assert "|" not in section


def test_a_run_with_real_receivers_keeps_its_table_even_when_the_list_is_empty() -> None:
    section = real_section(crossing_markdown(report(real_receivers=True)))
    assert "no real receiver" not in section
    assert "| site_id | role | rx_id |" in section
