"""Endpoint draws: seeding, the two profile builders, refusals and the per-link summary."""

import hashlib
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from terrain_dem import ORIGIN_X_M, ORIGIN_Y_M, centre_xy_m, open_layers, write_dem

from terrain.baselines import fspl_db
from terrain.clearance import fresnel_clearance_pct, los_clear
from terrain.draws import (
    BuildingPixels,
    DrawProfiles,
    DrawQuantities,
    DrawSummary,
    EndpointBox,
    FineProfileBuilder,
    MetroProfileBuilder,
    draw_endpoints,
    draw_seed,
    sample_link,
)
from terrain.errors import OutOfExtentError, RefusalReason, RefusedDrawError, ZeroLengthPathError
from terrain.profile import XY, PathProfile, profile_distances_m

FREQUENCY_MHZ = 906.875
RUN_SEED = 20261108
NO_REFUSALS = {"dem_gap": 0, "empty_cell": 0, "clutter_nan": 0, "short_profile": 0}


def flat_profiles(tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
    """A builder that reads no raster: flat ground at 100 m along the draw."""
    length_m = math.hypot(rx_xy_m[0] - tx_xy_m[0], rx_xy_m[1] - tx_xy_m[1])
    distance_m = profile_distances_m(length_m, 10.0)
    ground = PathProfile(
        distance_m=distance_m,
        ground_m=np.full(distance_m.size, 100.0),
        tx_xy_m=tx_xy_m,
        rx_xy_m=rx_xy_m,
    )
    return DrawProfiles(ground=ground, p1812=None, dsm_filled_pixels=None)


def free_space(draw: DrawProfiles) -> DrawQuantities:
    return DrawQuantities(values={"fspl_db": fspl_db(draw.ground.length_m, FREQUENCY_MHZ)})


def geometry(draw: DrawProfiles) -> DrawQuantities:
    ground = draw.ground
    values = {
        "fspl_db": fspl_db(ground.length_m, FREQUENCY_MHZ),
        "fresnel": fresnel_clearance_pct(ground, FREQUENCY_MHZ, 2.0, 2.0),
    }
    if draw.p1812 is not None and draw.p1812.clutter_m is not None:
        values["p1812_clutter_max_m"] = float(draw.p1812.clutter_m.max())
        values["p1812_ground_mean_m"] = float(draw.p1812.ground_m.mean())
    return DrawQuantities(values=values, flags={"los": los_clear(ground, 2.0, 2.0)})


def tx_east_of(
    centre_x_m: float, reason: RefusalReason = "empty_cell"
) -> Callable[[XY, XY], DrawProfiles]:
    def build(tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
        if tx_xy_m[0] > centre_x_m:
            raise RefusedDrawError(reason, "test refusal")
        return flat_profiles(tx_xy_m, rx_xy_m)

    return build


TX = EndpointBox(centre_xy_m=(721_000.0, 4_289_000.0), half_width_m=40.0)
RX = EndpointBox(centre_xy_m=(724_000.0, 4_289_500.0), half_width_m=25.0)


def test_draw_seed_mixes_the_run_seed_and_the_link_id() -> None:
    expected = int.from_bytes(hashlib.sha256(b"20261108:link-a").digest()[:8], "big") >> 1

    assert draw_seed(RUN_SEED, "link-a") == expected
    assert draw_seed(RUN_SEED, "link-b") != expected
    assert draw_seed(RUN_SEED + 1, "link-a") != expected
    assert 0 <= expected < 2**63


def test_draws_fall_uniformly_inside_each_box() -> None:
    draws = draw_endpoints(TX, RX, n_draws=4000, seed=5, floor_m=0.0)
    tx_xy = np.array([tx for tx, _ in draws])
    rx_xy = np.array([rx for _, rx in draws])

    for points, box in ((tx_xy, TX), (rx_xy, RX)):
        offset = points - np.array(box.centre_xy_m)
        assert np.all(np.abs(offset) <= box.half_width_m)
        assert np.abs(offset.mean(axis=0)).max() < 0.05 * box.half_width_m
        # A uniform spread over [-h, h] has standard deviation h / sqrt(3).
        assert offset.std(axis=0) == pytest.approx(box.half_width_m / math.sqrt(3), rel=0.05)


def test_single_draw_mode_is_the_pair_of_box_centres() -> None:
    assert draw_endpoints(TX, RX, n_draws=1, seed=5, floor_m=0.0) == [
        (TX.centre_xy_m, RX.centre_xy_m)
    ]


def test_same_seed_reproduces_every_number_exactly() -> None:
    first = sample_link("link-a", TX, RX, flat_profiles, geometry, run_seed=RUN_SEED, floor_m=0.0)
    again = sample_link("link-a", TX, RX, flat_profiles, geometry, run_seed=RUN_SEED, floor_m=0.0)
    other = sample_link(
        "link-a", TX, RX, flat_profiles, geometry, run_seed=RUN_SEED + 1, floor_m=0.0
    )

    assert first == again
    assert first.seed == draw_seed(RUN_SEED, "link-a")
    assert first.endpoint_draws == 16
    assert first.median("fspl_db") != other.median("fspl_db")


def rough_metro_dem(tmp_path: Path) -> Path:
    rng = np.random.default_rng(3)
    heights = rng.uniform(140.0, 190.0, size=(80, 400)).astype(np.float32)
    return write_dem(tmp_path / "metro.tif", heights, resolution_m=10.0)


def test_zero_size_boxes_give_every_draw_equal_to_a_single_draw(tmp_path: Path) -> None:
    dem_path = rough_metro_dem(tmp_path)
    tx = EndpointBox(centre_xy_m=centre_xy_m(40, 20, 10.0), half_width_m=0.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(30, 370, 10.0), half_width_m=0.0)

    with rasterio.open(dem_path) as dem:
        builder = MetroProfileBuilder(dem=dem)
        many = sample_link(
            "link-z", tx, rx, builder, geometry, run_seed=RUN_SEED, n_draws=16, floor_m=0.0
        )
        single = sample_link(
            "link-z", tx, rx, builder, geometry, run_seed=RUN_SEED, n_draws=1, floor_m=0.0
        )

    for name in ("fspl_db", "fresnel"):
        assert many.median(name) == single.median(name)
        assert many.iqr(name) == 0.0
    assert many.share("los") == single.share("los")
    assert many.refused_draws == 0


def test_single_draw_mode_ignores_the_box_size(tmp_path: Path) -> None:
    dem_path = rough_metro_dem(tmp_path)
    centres = centre_xy_m(40, 20, 10.0), centre_xy_m(30, 370, 10.0)
    points = [EndpointBox(centre_xy_m=c, half_width_m=0.0) for c in centres]
    boxes = [EndpointBox(centre_xy_m=c, half_width_m=60.0) for c in centres]

    with rasterio.open(dem_path) as dem:
        builder = MetroProfileBuilder(dem=dem)
        at_points = sample_link("l", *points, builder, geometry, run_seed=1, n_draws=1, floor_m=0.0)
        in_boxes = sample_link("l", *boxes, builder, geometry, run_seed=1, n_draws=1, floor_m=0.0)

    assert in_boxes.median("fresnel") == at_points.median("fresnel")
    assert in_boxes.median("fspl_db") == at_points.median("fspl_db")


def flat_fine_layers(tmp_path: Path, rows: int = 200, cols: int = 1100) -> dict[str, Path]:
    dem = np.full((rows, cols), 150.0, dtype=np.float32)
    return {
        "dem": write_dem(tmp_path / "dem.tif", dem),
        "dsm": write_dem(tmp_path / "dsm.tif", dem.copy()),
        "canopy": write_dem(tmp_path / "canopy.tif", np.zeros_like(dem)),
    }


def test_flat_plane_median_equals_the_centre_result(tmp_path: Path) -> None:
    paths = flat_fine_layers(tmp_path)
    centres = centre_xy_m(100, 50), centre_xy_m(100, 1050)
    points = [EndpointBox(centre_xy_m=c, half_width_m=0.0) for c in centres]
    boxes = [EndpointBox(centre_xy_m=c, half_width_m=5.0) for c in centres]

    with open_layers(paths["dem"], dsm=paths["dsm"], canopy=paths["canopy"]) as layers:
        builder = FineProfileBuilder(layers=layers)
        centre = sample_link(
            "flat", *points, builder, geometry, run_seed=RUN_SEED, n_draws=1, floor_m=0.0
        )
        drawn = sample_link("flat", *boxes, builder, geometry, run_seed=RUN_SEED, floor_m=0.0)

    assert drawn.refused_draws == 0
    assert drawn.median("fspl_db") == pytest.approx(centre.median("fspl_db"), abs=0.02)
    fspl_iqr, fresnel_iqr = drawn.iqr("fspl_db"), drawn.iqr("fresnel")
    assert fspl_iqr is not None and fspl_iqr < 0.1
    assert drawn.median("fresnel") == pytest.approx(centre.median("fresnel"), abs=0.01)
    assert fresnel_iqr is not None and fresnel_iqr < 0.01
    assert drawn.share("los") == 1.0
    assert drawn.median("p1812_clutter_max_m") == 0.0
    assert drawn.median("p1812_ground_mean_m") == pytest.approx(150.0)


def test_refused_draws_are_counted_by_reason_and_left_out_of_every_median() -> None:
    builder = tx_east_of(TX.centre_xy_m[0])
    seed = draw_seed(RUN_SEED, "link-r")
    kept = [
        flat_profiles(tx, rx)
        for tx, rx in draw_endpoints(TX, RX, 16, seed, floor_m=0.0)
        if tx[0] <= TX.centre_xy_m[0]
    ]
    kept_fspl = np.array([fspl_db(d.ground.length_m, FREQUENCY_MHZ) for d in kept])
    kept_los = [los_clear(d.ground, 2.0, 2.0) for d in kept]

    summary = sample_link("link-r", TX, RX, builder, geometry, run_seed=RUN_SEED, floor_m=0.0)

    assert 0 < summary.refused_draws < 16
    assert summary.refused_draws == 16 - len(kept)
    assert summary.refusals == {**NO_REFUSALS, "empty_cell": 16 - len(kept)}
    assert summary.median("fspl_db") == pytest.approx(float(np.median(kept_fspl)), abs=1e-12)
    expected_iqr = float(np.percentile(kept_fspl, 75) - np.percentile(kept_fspl, 25))
    assert summary.iqr("fspl_db") == pytest.approx(expected_iqr, abs=1e-12)
    assert summary.share("los") == pytest.approx(sum(kept_los) / len(kept))


def test_every_draw_refused_leaves_every_number_null() -> None:
    builder = tx_east_of(-math.inf, reason="clutter_nan")

    summary = sample_link("link-n", TX, RX, builder, geometry, run_seed=RUN_SEED, floor_m=0.0)

    assert summary.refused_draws == 16
    assert summary.refusals == {**NO_REFUSALS, "clutter_nan": 16}
    for name in ("fspl_db", "fresnel", "never-reported"):
        assert summary.median(name) is None
        assert summary.iqr(name) is None
    assert summary.share("los") is None
    assert summary.dsm_filled_pixels_median is None


def test_a_dem_gap_refuses_the_draw_even_for_free_space(tmp_path: Path) -> None:
    dem = np.full((200, 600), 100.0, dtype=np.float32)
    dem[:, :50] = -999999.0
    dem_path = write_dem(tmp_path / "dem.tif", dem)
    canopy_path = write_dem(tmp_path / "canopy.tif", np.zeros_like(dem))
    tx = EndpointBox(centre_xy_m=centre_xy_m(100, 50), half_width_m=20.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(100, 550), half_width_m=20.0)
    # The tx end reads the gap whenever it falls west of its pixel's centre.
    expected = sum(
        t[0] < tx.centre_xy_m[0]
        for t, _ in draw_endpoints(tx, rx, 16, draw_seed(RUN_SEED, "g"), floor_m=0.0)
    )

    with open_layers(dem_path, canopy=canopy_path) as layers:
        summary = sample_link(
            "g",
            tx,
            rx,
            FineProfileBuilder(layers=layers),
            free_space,
            run_seed=RUN_SEED,
            floor_m=0.0,
        )

    assert 0 < expected < 16
    assert summary.refusals == {**NO_REFUSALS, "dem_gap": expected}
    assert summary.median("fspl_db") is not None


def building_west_of_tx() -> BuildingPixels:
    mask = np.zeros((2000, 4100), dtype=bool)
    tx_col = int(TX.centre_xy_m[0] - ORIGIN_X_M)
    mask[:, :tx_col] = True
    return BuildingPixels(mask=mask, transform=from_origin(ORIGIN_X_M, ORIGIN_Y_M, 1.0, 1.0))


def test_building_draws_count_the_ends_that_land_on_a_building_pixel() -> None:
    seed = draw_seed(RUN_SEED, "link-b")
    on_roof = sum(
        tx[0] < TX.centre_xy_m[0] for tx, _ in draw_endpoints(TX, RX, 16, seed, floor_m=0.0)
    )

    summary = sample_link(
        "link-b",
        TX,
        RX,
        flat_profiles,
        free_space,
        run_seed=RUN_SEED,
        buildings=building_west_of_tx(),
        floor_m=0.0,
    )
    without = sample_link(
        "link-b", TX, RX, flat_profiles, free_space, run_seed=RUN_SEED, floor_m=0.0
    )

    assert 0 < on_roof < 16
    assert summary.tx_building_draws == on_roof
    assert summary.rx_building_draws == 0
    assert without.tx_building_draws is None
    assert without.rx_building_draws is None


def test_building_draws_count_refused_draws_too() -> None:
    seed = draw_seed(RUN_SEED, "link-b")
    on_roof = sum(
        tx[0] < TX.centre_xy_m[0] for tx, _ in draw_endpoints(TX, RX, 16, seed, floor_m=0.0)
    )
    builder = tx_east_of(-math.inf)

    summary = sample_link(
        "link-b",
        TX,
        RX,
        builder,
        free_space,
        run_seed=RUN_SEED,
        buildings=building_west_of_tx(),
        floor_m=0.0,
    )

    assert summary.refused_draws == 16
    assert summary.tx_building_draws == on_roof


def test_building_pixels_outside_their_grid_leave_the_extent() -> None:
    buildings = building_west_of_tx()

    with pytest.raises(OutOfExtentError):
        buildings.contains((ORIGIN_X_M - 1.0, ORIGIN_Y_M - 1.0))


def test_fine_builder_puts_both_profiles_on_the_1_m_ground_at_the_ends(tmp_path: Path) -> None:
    dem = np.full((60, 400), 100.0, dtype=np.float32)
    dem[25, 15] = 120.0  # the tx pixel; its 10 m cell averages 100.2 m
    paths = {
        "dem": write_dem(tmp_path / "dem.tif", dem),
        "dsm": write_dem(tmp_path / "dsm.tif", dem + 4.0),
    }
    tx, rx = centre_xy_m(25, 15), centre_xy_m(25, 315)

    with open_layers(paths["dem"], dsm=paths["dsm"]) as layers:
        draw = FineProfileBuilder(layers=layers)(tx, rx)

    assert draw.p1812 is not None and draw.p1812.clutter_m is not None
    assert draw.ground.step_m == pytest.approx(1.0)
    assert draw.p1812.step_m == pytest.approx(10.0)
    assert draw.ground.ground_m[0] == pytest.approx(120.0)
    assert draw.p1812.ground_m[0] == draw.ground.ground_m[0]
    assert draw.p1812.ground_m[-1] == draw.ground.ground_m[-1]
    assert draw.p1812.clutter_m[1] == pytest.approx(4.0)
    assert draw.ground.clutter_m is None
    assert draw.dsm_filled_pixels == 0


def test_dsm_filled_pixels_are_summarised_per_link(tmp_path: Path) -> None:
    dem = np.full((60, 400), 100.0, dtype=np.float32)
    dsm = dem + 4.0
    dsm[22, 100:103] = -999999.0
    paths = {
        "dem": write_dem(tmp_path / "dem.tif", dem),
        "dsm": write_dem(tmp_path / "dsm.tif", dsm),
    }
    tx = EndpointBox(centre_xy_m=centre_xy_m(25, 15), half_width_m=0.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(25, 315), half_width_m=0.0)

    with open_layers(paths["dem"], dsm=paths["dsm"]) as layers:
        summary = sample_link(
            "f", tx, rx, FineProfileBuilder(layers=layers), free_space, run_seed=1, floor_m=0.0
        )

    assert summary.dsm_filled_pixels_median == 3.0


def test_metro_builder_has_no_p1812_profile(tmp_path: Path) -> None:
    with rasterio.open(rough_metro_dem(tmp_path)) as dem:
        draw = MetroProfileBuilder(dem=dem)(centre_xy_m(40, 20, 10.0), centre_xy_m(30, 370, 10.0))

    assert draw.p1812 is None
    assert draw.dsm_filled_pixels is None
    assert draw.ground.step_m == pytest.approx(10.0, abs=0.1)


def test_a_draw_that_leaves_the_dem_fails_the_link(tmp_path: Path) -> None:
    tx = EndpointBox(centre_xy_m=centre_xy_m(1, 1, 10.0), half_width_m=200.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(30, 370, 10.0), half_width_m=0.0)

    with rasterio.open(rough_metro_dem(tmp_path)) as dem, pytest.raises(OutOfExtentError):
        sample_link(
            "o", tx, rx, MetroProfileBuilder(dem=dem), free_space, run_seed=RUN_SEED, floor_m=0.0
        )


def test_a_zero_length_link_raises_for_the_feature_asset_to_catch() -> None:
    point = EndpointBox(centre_xy_m=(721_000.0, 4_289_000.0), half_width_m=0.0)

    with pytest.raises(ZeroLengthPathError):
        sample_link("z", point, point, flat_profiles, free_space, run_seed=RUN_SEED, floor_m=0.0)


def test_a_nan_quantity_is_a_bug_not_a_refusal() -> None:
    def broken(draw: DrawProfiles) -> DrawQuantities:
        return DrawQuantities(values={"fspl_db": math.nan})

    with pytest.raises(ValueError, match="fspl_db"):
        sample_link("x", TX, RX, flat_profiles, broken, run_seed=RUN_SEED, floor_m=0.0)


def test_every_draw_must_report_the_same_quantities() -> None:
    def uneven(draw: DrawProfiles) -> DrawQuantities:
        if draw.ground.tx_xy_m[0] > TX.centre_xy_m[0]:
            return DrawQuantities(values={"a": 1.0})
        return DrawQuantities(values={"b": 1.0})

    with pytest.raises(ValueError, match="same quantities"):
        sample_link("x", TX, RX, flat_profiles, uneven, run_seed=RUN_SEED, floor_m=0.0)


def test_boxes_and_draw_counts_are_checked() -> None:
    with pytest.raises(ValueError, match="half_width_m"):
        EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=-1.0)
    with pytest.raises(ValueError, match="half_width_m"):
        EndpointBox(centre_xy_m=(0.0, 0.0), half_width_m=math.nan)
    with pytest.raises(ValueError, match="n_draws"):
        draw_endpoints(TX, RX, n_draws=0, seed=1, floor_m=0.0)


def test_summary_type_is_exported() -> None:
    summary = sample_link(
        "t", TX, RX, flat_profiles, free_space, run_seed=RUN_SEED, n_draws=4, floor_m=0.0
    )

    assert isinstance(summary, DrawSummary)
    assert summary.endpoint_draws == 4
    assert summary.refusals == NO_REFUSALS


def test_a_name_no_kept_draw_reported_is_a_key_error() -> None:
    summary = sample_link(
        "t", TX, RX, flat_profiles, geometry, run_seed=RUN_SEED, n_draws=4, floor_m=0.0
    )

    with pytest.raises(KeyError, match="not-a-quantity"):
        summary.median("not-a-quantity")
    with pytest.raises(KeyError):
        summary.iqr("los")
    with pytest.raises(KeyError):
        summary.share("fspl_db")


def test_first_draw_is_pinned_for_a_fixed_seed() -> None:
    # numpy's PCG64 stream and the draw order (tx easting, tx northing, rx
    # easting, rx northing) together decide every draw; a change to either
    # changes every published median.
    first = draw_endpoints(TX, RX, n_draws=16, seed=20261108, floor_m=0.0)[0]

    assert first == (
        (721024.9386824487, 4288971.692172184),
        (723995.9430615588, 4289495.067429672),
    )


def test_metro_builder_refuses_an_interior_dem_gap_for_every_model(tmp_path: Path) -> None:
    heights = np.full((80, 400), 150.0, dtype=np.float32)
    heights[30, 200] = -999999.0
    dem_path = write_dem(tmp_path / "metro.tif", heights, resolution_m=10.0)
    tx = EndpointBox(centre_xy_m=centre_xy_m(30, 20, 10.0), half_width_m=0.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(30, 370, 10.0), half_width_m=0.0)

    with rasterio.open(dem_path) as dem:
        summary = sample_link(
            "m", tx, rx, MetroProfileBuilder(dem=dem), free_space, run_seed=1, floor_m=0.0
        )

    assert summary.refusals == {**NO_REFUSALS, "dem_gap": 16}
    assert summary.median("fspl_db") is None


def test_fine_builder_refuses_a_gap_on_the_1_m_line_as_dem_gap(tmp_path: Path) -> None:
    dem = np.full((60, 400), 100.0, dtype=np.float32)
    dem[25, 200] = -999999.0  # on the 1 m line, far from both ends
    paths = {
        "dem": write_dem(tmp_path / "dem.tif", dem),
        "dsm": write_dem(tmp_path / "dsm.tif", dem),
    }

    with (
        open_layers(paths["dem"], dsm=paths["dsm"]) as layers,
        pytest.raises(RefusedDrawError) as caught,
    ):
        FineProfileBuilder(layers=layers)(centre_xy_m(25, 15), centre_xy_m(25, 315))

    # The 10 m profile would also refuse this cell, but as empty_cell: the
    # reason shows the ground profile was checked first.
    assert caught.value.reason == "dem_gap"


def test_building_mask_must_share_the_builder_dem_grid(tmp_path: Path) -> None:
    paths = flat_fine_layers(tmp_path)
    tx = EndpointBox(centre_xy_m=centre_xy_m(100, 50), half_width_m=0.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(100, 1050), half_width_m=0.0)

    with open_layers(paths["dem"], dsm=paths["dsm"]) as layers:
        builder = FineProfileBuilder(layers=layers)
        on_grid = BuildingPixels(
            mask=np.zeros((200, 1100), dtype=bool), transform=layers.dem.transform
        )
        off_grid = BuildingPixels(
            mask=np.zeros((200, 1200), dtype=bool), transform=layers.dem.transform
        )
        summary = sample_link(
            "b", tx, rx, builder, free_space, run_seed=1, buildings=on_grid, floor_m=0.0
        )
        with pytest.raises(ValueError, match="grid"):
            sample_link(
                "b", tx, rx, builder, free_space, run_seed=1, buildings=off_grid, floor_m=0.0
            )

    assert summary.tx_building_draws == 0


def test_metro_builder_takes_no_building_mask(tmp_path: Path) -> None:
    with rasterio.open(rough_metro_dem(tmp_path)) as dem:
        # A mask rasterised on the metro DEM's own 10 m grid, every pixel a building.
        mask = BuildingPixels(mask=np.ones((80, 400), dtype=bool), transform=dem.transform)
        tx = EndpointBox(centre_xy_m=centre_xy_m(40, 20, 10.0), half_width_m=0.0)
        rx = EndpointBox(centre_xy_m=centre_xy_m(30, 370, 10.0), half_width_m=0.0)
        with pytest.raises(ValueError, match="building draws need the 1 m layers"):
            sample_link(
                "m",
                tx,
                rx,
                MetroProfileBuilder(dem=dem),
                free_space,
                run_seed=1,
                buildings=mask,
                floor_m=0.0,
            )


def test_building_lookup_takes_the_pixel_holding_the_point() -> None:
    mask = np.zeros((1000, 2000), dtype=bool)
    mask[:500, :1000] = True
    buildings = BuildingPixels(mask=mask, transform=from_origin(ORIGIN_X_M, ORIGIN_Y_M, 1.0, 1.0))

    # 0.1 m inside the last building pixel on each axis: rounding would step out.
    assert buildings.contains((ORIGIN_X_M + 999.9, ORIGIN_Y_M - 10.0))
    assert buildings.contains((ORIGIN_X_M + 10.0, ORIGIN_Y_M - 499.9))
    assert not buildings.contains((ORIGIN_X_M + 1000.1, ORIGIN_Y_M - 10.0))
    assert not buildings.contains((ORIGIN_X_M + 10.0, ORIGIN_Y_M - 500.1))


def test_building_mask_must_be_a_north_up_grid() -> None:
    north_up = from_origin(ORIGIN_X_M, ORIGIN_Y_M, 1.0, 1.0)
    south_up = north_up @ north_up.scale(1.0, -1.0)

    with pytest.raises(ValueError, match="2-D"):
        BuildingPixels(mask=np.zeros(10, dtype=bool), transform=north_up)
    with pytest.raises(ValueError, match="north-up"):
        BuildingPixels(mask=np.zeros((10, 10), dtype=bool), transform=south_up)
