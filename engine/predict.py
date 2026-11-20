"""Predictions for every site and receiver pair, through the endpoint sampler.

Per pair, in the order the `path_loss_db` unit fixed (docs/STATUS.md,
Interfaces):

1. the pair's draws with `draw_endpoints(tx, rx, n, draw_seed(run_seed,
   link_id))`, the same `n` and seed `sample_link` uses, so both see the same
   endpoint pairs;
2. each end's building share within 100 m per draw, and their medians;
3. the Okumura-Hata variant from the two medians (ADR 0006 decision 7.1);
4. the draws whose transmitter or receiver end lies on a building pixel, on
   any 1 m DEM, with or without a DSM or canopy (the lead's decision B5 on
   PR #40), counted here over the same endpoint pairs `sample_link` draws;
5. `sample_link` with the variant set on the models by `dataclasses.replace`.

A pair whose two points coincide raises `ZeroLengthPathError` in the sampler;
it is the one error caught here. The pair's row keeps its ids, every value is
null, and all its draws count as refused, so `refused_draws` stays within 0
to `endpoint_draws` (decision B3 on PR #40).
A refused draw is not an error: the sampler drops and counts it. Pairs run in
order of site id then receiver id, so a rerun repeats every draw.
"""

import json
import math
import time
from dataclasses import dataclass, replace

import numpy as np
import structlog

from engine.buildings import Footprints
from engine.points import Endpoint
from engine.quantities import LinkQuantities
from terrain.baselines import HataVariant, hata_variant
from terrain.draws import (
    BuildingPixels,
    DrawSummary,
    PositionBox,
    ProfileBuilder,
    draw_endpoints,
    draw_seed,
    sample_link,
)
from terrain.errors import ZeroLengthPathError
from terrain.path_loss import DrawModels
from terrain.profile import XY

log = structlog.get_logger(component="engine.predict")


@dataclass(frozen=True, kw_only=True)
class RunModels:
    """Everything a pair's prediction needs that is the same for the whole run.

    Attributes:
        builder: Builds each draw's profiles from the run's terrain layers.
        models: The run's models; the variant is set per pair.
        run_seed: The config's seed.
        endpoint_draws: Draws for a pair with a position box at either end.
        position_floor_m: The GPS error floor under the draws, metres (ADR 0009).
        footprints: Building footprints, or None without a buildings snapshot.
        building_pixels: Building pixels of a 1 m DEM's grid, or None without
            footprints or a 1 m DEM.
    """

    builder: ProfileBuilder
    models: DrawModels
    run_seed: int
    endpoint_draws: int
    position_floor_m: float
    footprints: Footprints | None
    building_pixels: BuildingPixels | None


@dataclass(frozen=True, kw_only=True)
class PairPrediction:
    """One site and receiver pair's result.

    Attributes:
        site_id: The site's id.
        rx_id: The receiver's id.
        tx_xy_m: Site box centre, EPSG:26915 metres.
        rx_xy_m: Receiver box centre, EPSG:26915 metres.
        endpoint_draws: Draws made for the pair.
        seed: The pair's draw seed, `draw_seed(run_seed, link_id(site_id,
            rx_id))`, so a reader can repeat its draws (Casey, PR #42).
        summary: The sampler's summary; None for a zero-length pair.
        hata_variant: The pair's Okumura-Hata variant; None without footprints.
        mean_building_share: Mean of the two ends' median shares, or None.
        clutter_height_m_median: Median clutter height over the kept draws
            that have one, metres; None without.
        tx_building_draws: Draws whose transmitter lies on a building pixel;
            None without building pixels or for a zero-length pair.
        rx_building_draws: The same for the receiver.
    """

    site_id: str
    rx_id: str
    tx_xy_m: XY
    rx_xy_m: XY
    endpoint_draws: int
    seed: int
    summary: DrawSummary | None
    hata_variant: HataVariant | None
    mean_building_share: float | None
    clutter_height_m_median: float | None = None
    tx_building_draws: int | None = None
    rx_building_draws: int | None = None

    @property
    def refused_draws(self) -> int:
        """Draws refused for every model; all of them for a zero-length pair."""
        return self.endpoint_draws if self.summary is None else self.summary.refused_draws

    @property
    def distance_m(self) -> float:
        """Distance between the two box centres, metres."""
        return math.dist(self.tx_xy_m, self.rx_xy_m)

    @property
    def bearing_deg(self) -> float | None:
        """Grid bearing from site to receiver, degrees clockwise from grid north in
        EPSG:26915; None for a zero-length pair."""
        east_m = self.rx_xy_m[0] - self.tx_xy_m[0]
        north_m = self.rx_xy_m[1] - self.tx_xy_m[1]
        if east_m == 0.0 and north_m == 0.0:
            return None
        return math.degrees(math.atan2(east_m, north_m)) % 360.0


def link_id(site_id: str, rx_id: str) -> str:
    """The pair's key for `draw_seed`: a JSON list, so no two id pairs collide."""
    return json.dumps([site_id, rx_id], separators=(",", ":"))


def draws_for(tx: PositionBox, rx: PositionBox, endpoint_draws: int) -> int:
    """Draws for a pair: the configured number when either end has a box, 1 for two points."""
    return endpoint_draws if tx.has_area or rx.has_area else 1


def predict_pairs(
    sites: list[Endpoint], receivers: list[Endpoint], run: RunModels
) -> list[PairPrediction]:
    """Predict every site and receiver pair, sites then receivers in id order.

    Args:
        sites: The run's sites.
        receivers: The run's receivers.
        run: What every pair shares.

    Returns:
        One prediction per pair.
    """
    started = time.monotonic()
    log.info("predictions_begin", sites=len(sites), receivers=len(receivers))
    pairs = [
        predict_pair(site, receiver, run)
        for site in sorted(sites, key=lambda e: e.endpoint_id)
        for receiver in sorted(receivers, key=lambda e: e.endpoint_id)
    ]
    log.info(
        "predictions_end",
        pairs=len(pairs),
        zero_length=sum(p.summary is None for p in pairs),
        refused_draws=sum(p.refused_draws for p in pairs),
        duration_s=round(time.monotonic() - started, 3),
    )
    return pairs


def predict_pair(site: Endpoint, receiver: Endpoint, run: RunModels) -> PairPrediction:
    """Predict one pair: draws, building shares, Hata variant, building draws, then the sampler.

    Raises:
        OutOfExtentError: if a draw leaves the terrain build, which the run
            grid's margin rules out.
    """
    key = link_id(site.endpoint_id, receiver.endpoint_id)
    n_draws = draws_for(site.box, receiver.box, run.endpoint_draws)
    seed = draw_seed(run.run_seed, key)
    pairs = draw_endpoints(site.box, receiver.box, n_draws, seed, floor_m=run.position_floor_m)
    variant, mean_share = _variant(pairs, run.footprints)
    tx_on, rx_on = _building_draws(pairs, run.building_pixels)
    summary, clutter_m = _sample(
        key, site, receiver, n_draws, replace(run.models, hata_variant=variant), run
    )
    if summary is None:
        tx_on, rx_on = None, None
    return PairPrediction(
        site_id=site.endpoint_id,
        rx_id=receiver.endpoint_id,
        tx_xy_m=site.box.centre_xy_m,
        rx_xy_m=receiver.box.centre_xy_m,
        endpoint_draws=n_draws,
        seed=seed,
        summary=summary,
        hata_variant=variant,
        mean_building_share=mean_share,
        clutter_height_m_median=clutter_m,
        tx_building_draws=tx_on,
        rx_building_draws=rx_on,
    )


def _sample(
    key: str, site: Endpoint, receiver: Endpoint, n_draws: int, models: DrawModels, run: RunModels
) -> tuple[DrawSummary | None, float | None]:
    """The sampler's summary and the clutter median; (None, None) for a zero-length pair."""
    quantities = LinkQuantities(models=models, cells_required="p1812" in models.models)
    try:
        summary = sample_link(
            key,
            site.box,
            receiver.box,
            run.builder,
            quantities,
            run_seed=run.run_seed,
            n_draws=n_draws,
            floor_m=run.position_floor_m,
        )
    except ZeroLengthPathError:
        # Safe to continue: the pair's row keeps its ids with every draw
        # refused and every value null, and the run report counts it.
        log.warning("zero_length_pair", site_id=site.endpoint_id, rx_id=receiver.endpoint_id)
        return None, None
    return summary, quantities.clutter_median_m()


def _variant(
    pairs: list[tuple[XY, XY]], footprints: Footprints | None
) -> tuple[HataVariant | None, float | None]:
    """The pair's variant from each end's median share over the sampler's own draws."""
    if footprints is None:
        return None, None
    tx_share = float(np.median([footprints.share_within(t) for t, _ in pairs]))
    rx_share = float(np.median([footprints.share_within(r) for _, r in pairs]))
    return hata_variant(tx_share, rx_share), (tx_share + rx_share) / 2.0


def _building_draws(
    pairs: list[tuple[XY, XY]], pixels: BuildingPixels | None
) -> tuple[int | None, int | None]:
    """Draws, refused or not, whose transmitter and receiver lie on a building pixel."""
    if pixels is None:
        return None, None
    return (
        sum(pixels.contains(tx_xy_m) for tx_xy_m, _ in pairs),
        sum(pixels.contains(rx_xy_m) for _, rx_xy_m in pairs),
    )
