"""`predictions.parquet`: one GeoParquet row per site and receiver pair (`docs/INTERFACE.md`).

Columns, in order: `site_id`, `rx_id`, `distance_m`, `bearing_deg`, then
`<model>_db_median` and `<model>_db_iqr` for each model the run lists (in the
order fspl, knife_edge, hata, itm, p1812), `los_share`,
`fresnel_clearance_pct_median`, `clutter_height_m_median`, `hata_variant`,
`endpoint_draws`, `refused_draws`, `tx_building_draws`, `rx_building_draws`,
`p1812_at_cap_share`, and the LINESTRING from the site's box centre to the
receiver's, in EPSG:26915. Losses are basic transmission loss in dB, antenna
gains excluded; `knife_edge` is free space plus the Bullington loss.

Null, never zero, where a value does not exist: every median, range and share
when every draw was refused or the pair has zero length; clutter, P.1812 and
the building draws on the metro DEM; `hata_variant` and the building draws
without the buildings snapshot. `refused_draws` is null on a zero-length row,
where no draw read a profile.
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


def prediction_frame(pairs: list[PairPrediction], models: tuple[Model, ...]) -> gpd.GeoDataFrame:
    """The predictions as a GeoDataFrame with the interface's columns and types."""
    columns: dict[str, pd.Series] = {
        "site_id": pd.Series([p.site_id for p in pairs], dtype="string"),
        "rx_id": pd.Series([p.rx_id for p in pairs], dtype="string"),
        "distance_m": pd.Series([p.distance_m for p in pairs], dtype="Float64"),
        "bearing_deg": pd.Series([p.bearing_deg for p in pairs], dtype="Float64"),
    }
    for model in (m for m in MODELS if m in models):
        for stat in ("median", "iqr"):
            columns[f"{model}_db_{stat}"] = _floats(pairs, stat, f"{model}_db")
    columns["los_share"] = _floats(pairs, "share", "los_clear")
    columns["fresnel_clearance_pct_median"] = _floats(pairs, "median", "fresnel_clearance_pct")
    columns["clutter_height_m_median"] = _floats(pairs, "median", "clutter_height_m")
    columns["hata_variant"] = pd.Series([p.hata_variant for p in pairs], dtype="string")
    columns["endpoint_draws"] = pd.Series([p.endpoint_draws for p in pairs], dtype="Int64")
    columns["refused_draws"] = _integers(pairs, lambda s: s.refused_draws)
    columns["tx_building_draws"] = _integers(pairs, lambda s: s.tx_building_draws)
    columns["rx_building_draws"] = _integers(pairs, lambda s: s.rx_building_draws)
    columns["p1812_at_cap_share"] = _floats(pairs, "share", "p1812_at_cap")
    geometry = [LineString([p.tx_xy_m, p.rx_xy_m]) for p in pairs]
    return gpd.GeoDataFrame(columns, geometry=geometry, crs=GEOMETRY_CRS)


def write_predictions(path: Path, pairs: list[PairPrediction], models: tuple[Model, ...]) -> None:
    """Write `predictions.parquet` as GeoParquet with WKB geometry."""
    prediction_frame(pairs, models).to_parquet(
        path, index=False, compression="zstd", geometry_encoding="WKB", write_covering_bbox=False
    )


def summary_value(summary: DrawSummary | None, stat: str, name: str) -> float | None:
    """A median, interquartile range or share from a pair's summary, or None.

    None for a zero-length pair, for a pair with every draw refused, and for a
    quantity the draws did not report: P.1812 and clutter on the metro DEM,
    where `DrawModels` leaves P.1812 out (ADR 0006 decision 4).
    """
    if summary is None:
        return None
    lookup = {"median": summary.median, "iqr": summary.iqr, "share": summary.share}[stat]
    try:
        return lookup(name)
    except KeyError:
        return None


def _floats(pairs: list[PairPrediction], stat: str, name: str) -> pd.Series:
    return pd.Series([summary_value(p.summary, stat, name) for p in pairs], dtype="Float64")


def _integers(pairs: list[PairPrediction], read: Callable[[DrawSummary], int | None]) -> pd.Series:
    return pd.Series([None if p.summary is None else read(p.summary) for p in pairs], dtype="Int64")
