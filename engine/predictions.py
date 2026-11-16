"""`predictions.parquet`: one GeoParquet row per site and receiver pair (`docs/INTERFACE.md`).

Columns, in order: `site_id`, `rx_id`, `distance_m`, `bearing_deg`, then
`<model>_db_median` and `<model>_db_iqr` for each model the run lists (in the
order fspl, knife_edge, hata, itm, p1812), `los_share`,
`fresnel_clearance_pct_median`, `clutter_height_m_median`, `hata_variant`,
`endpoint_draws`, `refused_draws`, `seed` (the pair's draw seed,
`draw_seed(run_seed, link_id)`, a signed 64-bit integer; Casey, PR #42),
`tx_building_draws`, `rx_building_draws`, `p1812_at_cap_share`, and the
LINESTRING from the site's box centre to the receiver's, in EPSG:26915.
Losses are basic transmission loss in dB, antenna gains excluded;
`knife_edge` is free space plus the Bullington loss.

`clutter_height_m_median` reads the same clutter as `terrain/clutter.tif` and
P.1812 (Casey, PR #42): `terrain.clutter.clutter_height_m`, the ADR 0006 rule
with its floor and fills, applied per 1 m pixel and averaged into P.1812's
10 m cells; per draw the median over the interior points of P.1812's profile,
then the median over the draws (`engine.quantities`).

Null, never zero, where a value does not exist: every median, range and share
when every draw was refused, a zero-length pair counting as all refused;
clutter and P.1812 without the 1 m layers' P.1812 profile; the building draws
without a 1 m DEM; `hata_variant` and the building draws without the
buildings snapshot. Any other quantity a pair's draws did not report is a bug
and fails the run.
"""

from collections.abc import Callable
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from engine.predict import PairPrediction
from terrain.draws import DrawSummary
from terrain.path_loss import MODELS, Model

GEOMETRY_CRS = "EPSG:26915"
P1812_QUANTITIES = frozenset({"p1812_db", "p1812_at_cap"})
"""What draws without a P.1812 profile, or runs without p1812, never report."""


def prediction_frame(
    pairs: list[PairPrediction], models: tuple[Model, ...], absent: frozenset[str]
) -> gpd.GeoDataFrame:
    """The predictions as a GeoDataFrame with the interface's columns and types.

    Args:
        pairs: Every pair's prediction.
        models: The run's models.
        absent: Quantities the run's draws cannot report, written as null.
    """
    columns: dict[str, pd.Series] = {
        "site_id": pd.Series([p.site_id for p in pairs], dtype="string"),
        "rx_id": pd.Series([p.rx_id for p in pairs], dtype="string"),
        "distance_m": pd.Series([p.distance_m for p in pairs], dtype="Float64"),
        "bearing_deg": pd.Series([p.bearing_deg for p in pairs], dtype="Float64"),
    }
    for model in (m for m in MODELS if m in models):
        for stat in ("median", "iqr"):
            columns[f"{model}_db_{stat}"] = _floats(pairs, stat, f"{model}_db", absent)
    columns["los_share"] = _floats(pairs, "share", "los_clear", absent)
    columns["fresnel_clearance_pct_median"] = _floats(
        pairs, "median", "fresnel_clearance_pct", absent
    )
    columns["clutter_height_m_median"] = _column(pairs, lambda p: p.clutter_height_m_median)
    columns["hata_variant"] = pd.Series([p.hata_variant for p in pairs], dtype="string")
    columns["endpoint_draws"] = _column(pairs, lambda p: p.endpoint_draws, "Int64")
    columns["refused_draws"] = _column(pairs, lambda p: p.refused_draws, "Int64")
    columns["seed"] = _column(pairs, lambda p: p.seed, "Int64")
    columns["tx_building_draws"] = _column(pairs, lambda p: p.tx_building_draws, "Int64")
    columns["rx_building_draws"] = _column(pairs, lambda p: p.rx_building_draws, "Int64")
    columns["p1812_at_cap_share"] = _floats(pairs, "share", "p1812_at_cap", absent)
    geometry = [LineString([p.tx_xy_m, p.rx_xy_m]) for p in pairs]
    return gpd.GeoDataFrame(columns, geometry=geometry, crs=GEOMETRY_CRS)


def write_predictions(
    path: Path, pairs: list[PairPrediction], models: tuple[Model, ...], absent: frozenset[str]
) -> None:
    """Write `predictions.parquet` as GeoParquet with WKB geometry."""
    prediction_frame(pairs, models, absent).to_parquet(
        path, index=False, compression="zstd", geometry_encoding="WKB", write_covering_bbox=False
    )


def summary_value(
    summary: DrawSummary | None, stat: str, name: str, absent: frozenset[str]
) -> float | None:
    """A median, interquartile range or share from a pair's summary, or None.

    Args:
        summary: The pair's summary; None for a zero-length pair.
        stat: "median", "iqr" or "share".
        name: The quantity's name as the draws report it.
        absent: Quantities the run's draws cannot report (`P1812_QUANTITIES`
            on the metro DEM or without p1812).

    Returns:
        The value; None for a zero-length pair, a pair with every draw
        refused, or a name in `absent`.

    Raises:
        KeyError: if draws were kept and none reported a name the run should
            have, which is a bug, not a missing value.
    """
    if summary is None:
        return None
    lookup = {"median": summary.median, "iqr": summary.iqr, "share": summary.share}[stat]
    try:
        return lookup(name)
    except KeyError:
        if name in absent:
            return None
        raise


def _floats(pairs: list[PairPrediction], stat: str, name: str, absent: frozenset[str]) -> pd.Series:
    return pd.Series([summary_value(p.summary, stat, name, absent) for p in pairs], dtype="Float64")


def _column(
    pairs: list[PairPrediction],
    read: Callable[[PairPrediction], float | int | None],
    dtype: str = "Float64",
) -> pd.Series:
    return pd.Series([read(p) for p in pairs], dtype=dtype)
