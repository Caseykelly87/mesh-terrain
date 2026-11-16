"""Predictions for every site and receiver pair, through the endpoint sampler.

Per pair, in the order the `path_loss_db` unit fixed (docs/STATUS.md,
Interfaces):

1. the pair's draws with `draw_endpoints(tx, rx, n, draw_seed(run_seed,
   link_id))`, the same `n` and seed `sample_link` uses, so both see the same
   endpoint pairs;
2. each end's building share within 100 m per draw, and their medians;
3. the Okumura-Hata variant from the two medians (ADR 0006 decision 7.1);
4. `sample_link` with the variant set on the models by `dataclasses.replace`.

A pair whose two points coincide raises `ZeroLengthPathError` in the sampler;
it is the one error caught here, and the pair's row keeps its ids and nulls.
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
    EndpointBox,
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
        footprints: Building footprints, or None without a buildings snapshot.
        building_pixels: Building pixels of the 1 m grid, or None.
    """

    builder: ProfileBuilder
    models: DrawModels
    run_seed: int
    endpoint_draws: int
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
        summary: The sampler's summary; None for a zero-length pair.
        hata_variant: The pair's Okumura-Hata variant; None without footprints.
        mean_building_share: Mean of the two ends' median shares, or None.
    """

    site_id: str
    rx_id: str
    tx_xy_m: XY
    rx_xy_m: XY
    endpoint_draws: int
    summary: DrawSummary | None
    hata_variant: HataVariant | None
    mean_building_share: float | None

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


def draws_for(tx: EndpointBox, rx: EndpointBox, endpoint_draws: int) -> int:
    """Draws for a pair: the configured number when either end has a box, 1 for two points."""
    return endpoint_draws if tx.half_width_m > 0.0 or rx.half_width_m > 0.0 else 1


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
        refused_draws=sum(p.summary.refused_draws for p in pairs if p.summary is not None),
        duration_s=round(time.monotonic() - started, 3),
    )
    return pairs


def predict_pair(site: Endpoint, receiver: Endpoint, run: RunModels) -> PairPrediction:
    """Predict one pair: draws, building shares, Hata variant, then the sampler.

    Raises:
        OutOfExtentError: if a draw leaves the terrain build, which the run
            grid's margin rules out.
    """
    key = link_id(site.endpoint_id, receiver.endpoint_id)
    n_draws = draws_for(site.box, receiver.box, run.endpoint_draws)
    variant, mean_share = _variant(key, site.box, receiver.box, n_draws, run)
    try:
        summary: DrawSummary | None = sample_link(
            key,
            site.box,
            receiver.box,
            run.builder,
            LinkQuantities(models=replace(run.models, hata_variant=variant)),
            run_seed=run.run_seed,
            n_draws=n_draws,
            buildings=run.building_pixels,
        )
    except ZeroLengthPathError:
        # Safe to continue: the pair's row keeps its ids with null losses, and
        # the run report counts it.
        log.warning("zero_length_pair", site_id=site.endpoint_id, rx_id=receiver.endpoint_id)
        summary = None
    return PairPrediction(
        site_id=site.endpoint_id,
        rx_id=receiver.endpoint_id,
        tx_xy_m=site.box.centre_xy_m,
        rx_xy_m=receiver.box.centre_xy_m,
        endpoint_draws=n_draws,
        summary=summary,
        hata_variant=variant,
        mean_building_share=mean_share,
    )


def _variant(
    key: str, tx: EndpointBox, rx: EndpointBox, n_draws: int, run: RunModels
) -> tuple[HataVariant | None, float | None]:
    """The pair's variant from each end's median share over the sampler's own draws."""
    if run.footprints is None:
        return None, None
    pairs = draw_endpoints(tx, rx, n_draws, draw_seed(run.run_seed, key))
    tx_share = float(np.median([run.footprints.share_within(t) for t, _ in pairs]))
    rx_share = float(np.median([run.footprints.share_within(r) for _, r in pairs]))
    return hata_variant(tx_share, rx_share), (tx_share + rx_share) / 2.0
