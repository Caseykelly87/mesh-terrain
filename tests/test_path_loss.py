"""path_loss_db over the five models, and the adapter that feeds sample_link (ADR 0006).

Expected values are the ones the wrappers' own tests hold: the closed-form free
space loss, the README's knife-edge figure, the hand-worked Hata cases, ITM's
151.76 dB and P.1812's 149.48 dB on the flat 10 km path. Paths are in
EPSG:26915 near St. Louis, so P.1812 reads a real latitude and longitude.

ITM needs libitm.so, which exists only in the Dagster image. Tests marked by
the `itm_library` fixture skip without it, which includes CI until
`make check-itm` runs the ITM tests inside the image (docs/STATUS.md); the
others replace `itm_loss` with a stub. P.1812 runs for real on constant maps
or the committed synthetic map snapshot.
"""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast, get_args

import numpy as np
import numpy.typing as npt
import pytest
from pyproj import Transformer
from terrain_dem import centre_xy_m, open_layers, write_dem

from pipeline.common.settings import get_settings
from terrain.antenna import AntennaProfile
from terrain.baselines import fspl_db
from terrain.diffraction import knife_edge_db
from terrain.draws import (
    DrawProfiles,
    EndpointBox,
    FineProfileBuilder,
    draw_endpoints,
    draw_seed,
    sample_link,
)
from terrain.errors import RefusedDrawError, ZeroLengthPathError
from terrain.itm import DEFAULT_SETTINGS, ItmLibrary, ItmSettings, load_itm_library
from terrain.p1812 import P1812Maps
from terrain.path_loss import DrawModels, Model, path_loss_db
from terrain.profile import XY, PathProfile
from terrain.result import PathLossResult

FloatArray = npt.NDArray[np.float64]

FREQUENCY_MHZ = 915.0
RUN_SEED = 20261108
MODELS: tuple[Model, ...] = get_args(Model)
NODE = AntennaProfile(
    name="mesh_node", height_m=2.0, gain_dbi=0.0, polarisation="vertical", transmit_power_dbm=22.0
)
TX_XY_M: XY = Transformer.from_crs("EPSG:4326", "EPSG:26915", always_xy=True).transform(
    -90.25, 38.63
)
STUB_LIBRARY = ItmLibrary(path=Path("libitm-stub.so"), handle=cast(Any, None))
ST_LOUIS_DN, ST_LOUIS_N0 = 39.126, 324.511


def antenna(height_m: float, polarisation: str = "vertical") -> AntennaProfile:
    return AntennaProfile(
        name=f"test_{height_m}", height_m=height_m, gain_dbi=0.0, polarisation=polarisation
    )


def flat(
    length_m: float = 10_000.0,
    *,
    step_m: float = 10.0,
    ground_m: float = 150.0,
    clutter_m: float | None = None,
) -> PathProfile:
    """Flat ground due east of TX_XY_M; clutter, when given, everywhere but the two ends."""
    points = int(round(length_m / step_m)) + 1
    clutter = None
    if clutter_m is not None:
        clutter = np.full(points, clutter_m)
        clutter[0] = clutter[-1] = 0.0
    return PathProfile(
        distance_m=np.linspace(0.0, length_m, points),
        ground_m=np.full(points, ground_m),
        clutter_m=clutter,
        tx_xy_m=TX_XY_M,
        rx_xy_m=(TX_XY_M[0] + length_m, TX_XY_M[1]),
    )


def constant_maps(dn: float = ST_LOUIS_DN, n0: float = ST_LOUIS_N0) -> P1812Maps:
    return P1812Maps(
        snapshot_id="in-memory-constants",
        manifest_sha256="0" * 64,
        dn50=np.full((121, 241), dn),
        n050=np.full((121, 241), n0),
    )


def stub_itm(monkeypatch: pytest.MonkeyPatch, offset_db: float = 40.0) -> list[dict[str, Any]]:
    """Replace ITM with free space plus a fixed excess, recording each call."""
    calls: list[dict[str, Any]] = []

    def itm_loss(library: ItmLibrary, profile: PathProfile, **kwargs: Any) -> PathLossResult:
        calls.append({"library": library, "profile": profile, **kwargs})
        loss_db = fspl_db(profile.length_m, kwargs["frequency_mhz"]) + offset_db
        return PathLossResult(loss_db=loss_db, itm_warning_flags=0)

    monkeypatch.setattr("terrain.path_loss.itm_loss", itm_loss)
    return calls


@pytest.fixture(scope="module")
def itm_library() -> ItmLibrary:
    path = get_settings().itm_library_path
    if not Path(path).is_file():
        pytest.skip(f"no ITM library at {path}; it is built into the Dagster image")
    return load_itm_library(path)


# Dispatch: each model gives the value its own tests hold


def test_fspl_is_the_closed_form() -> None:
    result = path_loss_db("fspl", flat(), FREQUENCY_MHZ, NODE, NODE)
    assert result == PathLossResult(loss_db=fspl_db(10_000.0, FREQUENCY_MHZ))
    assert result.loss_db == pytest.approx(111.68, abs=0.01)


def test_knife_edge_is_free_space_plus_the_bullington_loss() -> None:
    """terrain/README.md: 12.1 dB on top of 111.7 dB free space on the flat 10 km path."""
    profile = flat()
    result = path_loss_db("knife_edge", profile, FREQUENCY_MHZ, NODE, NODE)
    expected = fspl_db(10_000.0, FREQUENCY_MHZ) + knife_edge_db(profile, FREQUENCY_MHZ, 2.0, 2.0)
    assert result == PathLossResult(loss_db=expected)
    assert result.loss_db == pytest.approx(123.8, abs=0.05)


@pytest.mark.parametrize(("variant", "expected_db"), [("urban", 149.933), ("suburban", 139.947)])
def test_hata_takes_its_variant_from_the_caller(variant: str, expected_db: float) -> None:
    """Hand-worked case 2 of tests/test_terrain_baselines.py: 5 km, 915 MHz, 30 m and 2 m."""
    result = path_loss_db(
        "hata", flat(5_000.0), FREQUENCY_MHZ, antenna(30.0), NODE, hata_variant=variant
    )
    assert result.loss_db == pytest.approx(expected_db, abs=0.01)
    assert result.troposcatter_loss_db is None and result.itm_warning_flags is None


def test_itm_gets_the_library_settings_heights_and_polarisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = stub_itm(monkeypatch)
    settings = ItmSettings(mdvar=1)
    profile = flat()
    result = path_loss_db(
        "itm",
        profile,
        FREQUENCY_MHZ,
        antenna(10.0),
        NODE,
        itm_library=STUB_LIBRARY,
        itm_settings=settings,
    )
    (call,) = calls
    assert call == {
        "library": STUB_LIBRARY,
        "profile": profile,
        "tx_height_m": 10.0,
        "rx_height_m": 2.0,
        "frequency_mhz": FREQUENCY_MHZ,
        "polarisation": "vertical",
        "settings": settings,
    }
    assert result.itm_warning_flags == 0


def test_itm_defaults_to_the_adr_0002_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_itm(monkeypatch)
    path_loss_db("itm", flat(), FREQUENCY_MHZ, NODE, NODE, itm_library=STUB_LIBRARY)
    assert calls[0]["settings"] is DEFAULT_SETTINGS


def test_p1812_reproduces_the_measured_figure_and_fills_lbs() -> None:
    """ADR 0006 spike: 149.48 dB, Lbs 160.17 dB on the flat 10 km path with St. Louis DN and N0."""
    result = path_loss_db(
        "p1812", flat(clutter_m=0.0), FREQUENCY_MHZ, NODE, NODE, p1812_maps=constant_maps()
    )
    assert result.loss_db == pytest.approx(149.48, abs=0.01)
    assert result.troposcatter_loss_db == pytest.approx(160.17, abs=0.01)
    assert result.at_troposcatter_cap is False


@pytest.mark.parametrize("model", ["fspl", "knife_edge", "hata"])
def test_models_without_diagnostics_leave_them_empty(model: Model) -> None:
    result = path_loss_db(model, flat(), FREQUENCY_MHZ, NODE, NODE, hata_variant="urban")
    assert result.troposcatter_loss_db is None
    assert result.itm_warning_flags is None
    assert result.at_troposcatter_cap is None


def test_itm_on_the_real_library_reproduces_the_adr_0002_figure(
    itm_library: ItmLibrary,
) -> None:
    """10 km flat at sea level, 10 m step, 915 MHz, 2 m antennas: 151.76 dB."""
    result = path_loss_db(
        "itm", flat(ground_m=0.0), FREQUENCY_MHZ, NODE, NODE, itm_library=itm_library
    )
    assert result.loss_db == pytest.approx(151.76, abs=0.005)
    assert result.itm_warning_flags is not None


# The P.1812 import stays lazy

LAZY_IMPORT_SCRIPT = """
import sys
from types import SimpleNamespace

import numpy as np

from terrain.antenna import AntennaProfile
from terrain.draws import DrawProfiles
from terrain.path_loss import DrawModels, path_loss_db
from terrain.profile import PathProfile

def p1812_loaded():
    return {"Py1812.P1812", "terrain.p1812"} & set(sys.modules)

node = AntennaProfile(name="n", height_m=2.0, gain_dbi=0.0, polarisation="vertical")
distance = np.linspace(0.0, 1000.0, 101)
profile = PathProfile(
    distance_m=distance, ground_m=np.full(101, 150.0), clutter_m=np.zeros(101),
    tx_xy_m=(720050.5, 4289899.5), rx_xy_m=(721050.5, 4289899.5),
)
assert not p1812_loaded(), f"importing path_loss loaded {p1812_loaded()}"
for model in ("fspl", "knife_edge"):
    path_loss_db(model, profile, 906.875, node, node)
path_loss_db("hata", profile, 906.875, node, node, hata_variant="urban")
for model in ("itm", "p1812"):
    try:
        path_loss_db(model, profile, 906.875, node, node)
    except RuntimeError:
        continue
    raise SystemExit(f"{model} without its library or maps raised no RuntimeError")
models =DrawModels(models=("fspl", "knife_edge"), frequency_mhz=906.875, tx=node, rx=node)
models(DrawProfiles(ground=profile, p1812=profile, dsm_filled_pixels=None))
assert not p1812_loaded(), f"a call without p1812 loaded {p1812_loaded()}"
grid = np.full((121, 241), 40.0)
maps = SimpleNamespace(dn50=grid, n050=np.full((121, 241), 325.0))
path_loss_db("p1812", profile, 906.875, node, node, p1812_maps=maps)
assert p1812_loaded() == {"Py1812.P1812", "terrain.p1812"}
print("lazy")
"""


def test_py1812_is_imported_on_the_first_p1812_call_only(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", LAZY_IMPORT_SCRIPT],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "lazy"


# Refusals and errors


def short_profile(samples: int, length_m: float = 40.0) -> PathProfile:
    return PathProfile(
        distance_m=np.linspace(0.0, length_m, samples),
        ground_m=np.full(samples, 150.0),
        clutter_m=np.zeros(samples),
        tx_xy_m=TX_XY_M,
        rx_xy_m=(TX_XY_M[0] + length_m, TX_XY_M[1]),
    )


@pytest.mark.parametrize("model", MODELS)
def test_fewer_than_five_samples_refuse_the_draw_for_every_model(model: Model) -> None:
    """ADR 0006 decision 3 as amended by item 11: reason short_profile, not ValueError."""
    with pytest.raises(RefusedDrawError, match="five") as refusal:
        path_loss_db(model, short_profile(4), FREQUENCY_MHZ, NODE, NODE)
    assert refusal.value.reason == "short_profile"


def test_five_samples_are_enough() -> None:
    result = path_loss_db("fspl", short_profile(5), FREQUENCY_MHZ, NODE, NODE)
    assert result.loss_db == pytest.approx(fspl_db(40.0, FREQUENCY_MHZ))


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("samples", [3, 5])
def test_coinciding_endpoints_are_a_zero_length_path(model: Model, samples: int) -> None:
    """Checked before the sample count, so a short zero-length path is still per link."""
    profile = PathProfile(
        distance_m=np.linspace(0.0, 40.0, samples),
        ground_m=np.full(samples, 150.0),
        tx_xy_m=TX_XY_M,
        rx_xy_m=TX_XY_M,
    )
    with pytest.raises(ZeroLengthPathError, match="zero-length"):
        path_loss_db(model, profile, FREQUENCY_MHZ, NODE, NODE)


@pytest.mark.parametrize("model", MODELS)
def test_a_ground_gap_refuses_the_draw_for_every_model(model: Model) -> None:
    profile = flat(1_000.0, clutter_m=0.0)
    profile.ground_m[50] = np.nan
    with pytest.raises(RefusedDrawError) as refusal:
        path_loss_db(model, profile, FREQUENCY_MHZ, NODE, NODE, hata_variant="urban")
    assert refusal.value.reason == "dem_gap"


def test_hata_without_a_variant_is_a_plain_value_error() -> None:
    with pytest.raises(ValueError, match="hata_variant") as error:
        path_loss_db("hata", flat(), FREQUENCY_MHZ, NODE, NODE)
    assert type(error.value) is ValueError


def test_itm_without_the_library_names_the_install_step() -> None:
    with pytest.raises(RuntimeError, match="ITM_LIBRARY_PATH"):
        path_loss_db("itm", flat(), FREQUENCY_MHZ, NODE, NODE)


def test_p1812_without_maps_names_make_p1812_maps() -> None:
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        path_loss_db("p1812", flat(clutter_m=0.0), FREQUENCY_MHZ, NODE, NODE)


def test_p1812_without_clutter_fails_the_run() -> None:
    """The adapter leaves p1812 out where a draw has no P.1812 profile (decision 4)."""
    with pytest.raises(ValueError, match="clutter"):
        path_loss_db("p1812", flat(), FREQUENCY_MHZ, NODE, NODE, p1812_maps=constant_maps())


@pytest.mark.parametrize("model", ["itm", "p1812"])
def test_two_polarisations_on_one_link_are_refused(model: Model) -> None:
    with pytest.raises(ValueError, match="polarisation"):
        path_loss_db(
            model,
            flat(clutter_m=0.0),
            FREQUENCY_MHZ,
            NODE,
            antenna(2.0, "horizontal"),
            itm_library=STUB_LIBRARY,
            p1812_maps=constant_maps(),
        )


def test_an_unknown_model_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown model"):
        path_loss_db(cast(Model, "splat"), flat(), FREQUENCY_MHZ, NODE, NODE)


@pytest.mark.parametrize("frequency_mhz", [0.0, -915.0, float("nan")])
def test_a_frequency_that_is_not_positive_is_refused(frequency_mhz: float) -> None:
    with pytest.raises(ValueError, match="frequency_mhz"):
        path_loss_db("fspl", flat(), frequency_mhz, NODE, NODE)


def test_distances_that_miss_the_receiver_are_refused() -> None:
    profile = flat()
    shifted = PathProfile(
        distance_m=profile.distance_m,
        ground_m=profile.ground_m,
        tx_xy_m=profile.tx_xy_m,
        rx_xy_m=(profile.rx_xy_m[0] + 5.0, profile.rx_xy_m[1]),
    )
    with pytest.raises(ValueError, match="receiver"):
        path_loss_db("fspl", shifted, FREQUENCY_MHZ, NODE, NODE)


# The adapter: DrawProfiles in, DrawQuantities out


def all_models(**overrides: Any) -> DrawModels:
    fields: dict[str, Any] = {
        "models": MODELS,
        "frequency_mhz": FREQUENCY_MHZ,
        "tx": NODE,
        "rx": NODE,
        "hata_variant": "suburban",
        "itm_library": STUB_LIBRARY,
        "p1812_maps": constant_maps(),
    }
    return DrawModels(**{**fields, **overrides})


def test_adapter_reports_each_loss_and_the_cap_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = stub_itm(monkeypatch)
    ground = flat(1_000.0, step_m=1.0)
    cells = flat(1_000.0, clutter_m=10.0)

    quantities = all_models()(DrawProfiles(ground=ground, p1812=cells, dsm_filled_pixels=None))

    assert set(quantities.values) == {f"{model}_db" for model in MODELS}
    for model in ("fspl", "knife_edge", "hata"):
        expected = path_loss_db(model, ground, FREQUENCY_MHZ, NODE, NODE, hata_variant="suburban")
        assert quantities.values[f"{model}_db"] == expected.loss_db
    assert calls[0]["profile"] is ground
    p1812 = path_loss_db("p1812", cells, FREQUENCY_MHZ, NODE, NODE, p1812_maps=constant_maps())
    assert quantities.values["p1812_db"] == p1812.loss_db
    assert quantities.flags == {"p1812_at_cap": True}


def test_adapter_reports_a_draw_below_the_cap_as_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare flat 10 km path: Lb 149.48 dB against Lbs 160.17 dB, far from the cap."""
    stub_itm(monkeypatch)
    draw = DrawProfiles(ground=flat(), p1812=flat(clutter_m=0.0), dsm_filled_pixels=None)

    quantities = all_models()(draw)

    assert quantities.values["p1812_db"] == pytest.approx(149.48, abs=0.01)
    assert quantities.flags == {"p1812_at_cap": False}


def test_adapter_leaves_p1812_out_without_its_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_itm(monkeypatch)
    draw = DrawProfiles(ground=flat(), p1812=None, dsm_filled_pixels=None)

    quantities = all_models()(draw)

    assert "p1812_db" not in quantities.values
    assert quantities.flags == {}


def test_adapter_runs_only_the_listed_models() -> None:
    draw = DrawProfiles(ground=flat(), p1812=flat(clutter_m=0.0), dsm_filled_pixels=None)
    quantities = all_models(models=("fspl",), itm_library=None, p1812_maps=None)(draw)
    assert set(quantities.values) == {"fspl_db"}


@pytest.mark.parametrize(
    ("models", "message"),
    [((), "at least one"), (("fspl", "fspl"), "twice"), (("splat",), "splat")],
)
def test_adapter_refuses_an_empty_repeated_or_unknown_model_list(
    models: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        all_models(models=models)


def fine_layers_with_clutter(tmp_path: Path) -> dict[str, Path]:
    """Flat ground at 150 m with a 10 m surface everywhere: buildings or trees, 1 m pixels."""
    dem = np.full((200, 1100), 150.0, dtype=np.float32)
    return {
        "dem": write_dem(tmp_path / "dem.tif", dem),
        "dsm": write_dem(tmp_path / "dsm.tif", dem + 10.0),
        "canopy": write_dem(tmp_path / "canopy.tif", np.zeros_like(dem)),
    }


def test_sample_link_through_the_adapter_matches_direct_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_itm(monkeypatch)
    paths = fine_layers_with_clutter(tmp_path)
    tx = EndpointBox(centre_xy_m=centre_xy_m(100, 50), half_width_m=5.0)
    rx = EndpointBox(centre_xy_m=centre_xy_m(100, 1050), half_width_m=5.0)
    models = all_models()

    with open_layers(paths["dem"], dsm=paths["dsm"], canopy=paths["canopy"]) as layers:
        fine = FineProfileBuilder(layers=layers)

        def builder(tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
            """Clear the clutter on draws whose tx falls east of its box centre, off the cap."""
            draw = fine(tx_xy_m, rx_xy_m)
            if draw.p1812 is None or tx_xy_m[0] <= tx.centre_xy_m[0]:
                return draw
            bare = replace(draw.p1812, clutter_m=np.zeros_like(draw.p1812.distance_m))
            return replace(draw, p1812=bare)

        summary = sample_link("adapter", tx, rx, builder, models, run_seed=RUN_SEED)
        pairs = draw_endpoints(tx, rx, 16, draw_seed(RUN_SEED, "adapter"))
        draws = [builder(tx_xy_m, rx_xy_m) for tx_xy_m, rx_xy_m in pairs]

    assert summary.refused_draws == 0
    direct: dict[str, list[PathLossResult]] = {model: [] for model in MODELS}
    for draw in draws:
        for model in MODELS:
            profile = draw.p1812 if model == "p1812" else draw.ground
            assert profile is not None
            result = path_loss_db(
                model,
                profile,
                FREQUENCY_MHZ,
                NODE,
                NODE,
                hata_variant="suburban",
                itm_library=STUB_LIBRARY,
                p1812_maps=models.p1812_maps,
            )
            direct[model].append(result)
    for model, results in direct.items():
        losses = np.array([result.loss_db for result in results])
        assert summary.median(f"{model}_db") == pytest.approx(float(np.median(losses)), abs=1e-9)
        iqr = float(np.percentile(losses, 75) - np.percentile(losses, 25))
        assert summary.iqr(f"{model}_db") == pytest.approx(iqr, abs=1e-9)
    at_cap = [result.at_troposcatter_cap for result in direct["p1812"]]
    share = summary.share("p1812_at_cap")
    assert share == sum(bool(flag) for flag in at_cap) / 16
    assert share is not None and 0.0 < share < 1.0


def test_a_refusal_by_one_model_drops_the_draw_for_every_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tx = EndpointBox(centre_xy_m=TX_XY_M, half_width_m=40.0)
    rx = EndpointBox(centre_xy_m=(TX_XY_M[0] + 3_000.0, TX_XY_M[1]), half_width_m=25.0)

    def itm_refusing_east(library: ItmLibrary, profile: PathProfile, **_: Any) -> PathLossResult:
        if profile.tx_xy_m[0] > TX_XY_M[0]:
            raise RefusedDrawError("dem_gap", "stub refusal")
        return PathLossResult(loss_db=150.0, itm_warning_flags=0)

    def flat_builder(tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
        length_m = float(np.hypot(rx_xy_m[0] - tx_xy_m[0], rx_xy_m[1] - tx_xy_m[1]))
        points = int(np.ceil(length_m / 10.0)) + 1
        ground = PathProfile(
            distance_m=np.linspace(0.0, length_m, points),
            ground_m=np.full(points, 150.0),
            tx_xy_m=tx_xy_m,
            rx_xy_m=rx_xy_m,
        )
        return DrawProfiles(ground=ground, p1812=None, dsm_filled_pixels=None)

    monkeypatch.setattr("terrain.path_loss.itm_loss", itm_refusing_east)
    pairs = draw_endpoints(tx, rx, 16, draw_seed(RUN_SEED, "one-refuses"))
    kept = [
        fspl_db(float(np.hypot(r[0] - t[0], r[1] - t[1])), FREQUENCY_MHZ)
        for t, r in pairs
        if t[0] <= TX_XY_M[0]
    ]

    summary = sample_link("one-refuses", tx, rx, flat_builder, all_models(), run_seed=RUN_SEED)

    assert summary.refusals["dem_gap"] == 16 - len(kept)
    assert 0 < summary.refused_draws < 16
    assert summary.median("fspl_db") == pytest.approx(float(np.median(kept)), abs=1e-9)


def test_adapter_on_the_real_itm_library(itm_library: ItmLibrary) -> None:
    point = EndpointBox(centre_xy_m=TX_XY_M, half_width_m=0.0)
    receiver = EndpointBox(centre_xy_m=(TX_XY_M[0] + 10_000.0, TX_XY_M[1]), half_width_m=0.0)

    def sea_level(tx_xy_m: XY, rx_xy_m: XY) -> DrawProfiles:
        return DrawProfiles(ground=flat(ground_m=0.0), p1812=None, dsm_filled_pixels=None)

    models = all_models(models=("itm",), itm_library=itm_library)
    summary = sample_link("itm", point, receiver, sea_level, models, run_seed=RUN_SEED, n_draws=1)

    assert summary.median("itm_db") == pytest.approx(151.76, abs=0.005)
