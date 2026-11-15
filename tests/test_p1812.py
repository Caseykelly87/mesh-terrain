"""The P.1812 wrapper: placeholder import, map snapshot, refusals and one call (ADR 0006).

The synthetic map snapshot under tests/fixtures/snapshots holds made-up
constants (DN 40, N0 325), never the ITU maps. Paths are in EPSG:26915.
"""

import hashlib
import inspect
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pytest
from pyproj import Transformer
from structlog.testing import capture_logs

from terrain import p1812
from terrain.errors import RefusedDrawError
from terrain.p1812 import (
    P1812,
    BtLossInputs,
    P1812Maps,
    call_bt_loss,
    check_import_preconditions,
    load_maps,
    p1812_loss,
    troposcatter_loss_from_log,
)
from terrain.profile import PathProfile
from terrain.result import PathLossResult

FloatArray = npt.NDArray[np.float64]

FIXTURE = Path(__file__).parent / "fixtures" / "snapshots" / "p1812-maps-synthetic"
# sha256 of the real ITU files (docs/adr/0006-spike/README.md); the fixture must differ.
ITU_DN50_SHA256 = "4847bf9542b775850daab6f0fb1bdc933a4d9a28aff65549bcfc45e141cd230d"
ITU_N050_SHA256 = "d27590ce5c7cf7ff7b4457ce31b9bf3591f0b97e367732195de0f1a98bd35f2a"
TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:26915", always_xy=True)
TX_XY_M: tuple[float, float] = TO_UTM.transform(-90.25, 38.63)
STEP_M = 10.0


def flat_path(length_m: float = 10_000.0) -> tuple[FloatArray, FloatArray, tuple[float, float]]:
    """Distances, ground at 150 m, and the receiver due east in EPSG:26915."""
    points = int(round(length_m / STEP_M)) + 1
    distance_m = np.linspace(0.0, length_m, points)
    return distance_m, np.full(points, 150.0), (TX_XY_M[0] + length_m, TX_XY_M[1])


def constant_maps(dn: float, n0: float) -> P1812Maps:
    return P1812Maps(
        snapshot_id="in-memory-constants",
        manifest_sha256="0" * 64,
        dn50=np.full((121, 241), dn),
        n050=np.full((121, 241), n0),
    )


PROFILE_FIELDS = frozenset({"distance_m", "ground_m", "clutter_m", "tx_xy_m", "rx_xy_m"})


def call_flat(**overrides: Any) -> PathLossResult:
    """p1812_loss on the 10 km flat path with R 0; overrides go to the profile or the call."""
    distance_m, ground_m, rx_xy_m = flat_path()
    fields: dict[str, Any] = {
        "distance_m": distance_m,
        "ground_m": ground_m,
        "clutter_m": np.zeros_like(distance_m),
        "tx_xy_m": TX_XY_M,
        "rx_xy_m": rx_xy_m,
    }
    options: dict[str, Any] = {
        "tx_height_m": 2.0,
        "rx_height_m": 2.0,
        "frequency_mhz": 915.0,
        "maps": load_maps(FIXTURE),
        "polarisation": "vertical",
    }
    for name, value in overrides.items():
        (fields if name in PROFILE_FIELDS else options)[name] = value
    return p1812_loss(PathProfile(**fields), **options)


def flat_inputs(clutter_m: FloatArray) -> BtLossInputs:
    """bt_loss inputs for the 10 km flat path at 150 m, given in degrees, DN 40 and N0 325."""
    return BtLossInputs(
        distance_km=np.linspace(0.0, 10.0, 1001),
        ground_m=np.full(1001, 150.0),
        clutter_m=clutter_m,
        tx_height_m=2.0,
        rx_height_m=2.0,
        frequency_ghz=0.915,
        polarisation_code=2,
        tx_lat_deg=38.63,
        tx_lon_deg=-90.25,
        rx_lat_deg=38.63,
        rx_lon_deg=-90.135,
        dn=40.0,
        n0=325.0,
    )


def spy_on(monkeypatch: pytest.MonkeyPatch, name: str) -> list[Any]:
    """Record every positional first argument passed to p1812.<name>, then call it."""
    seen: list[Any] = []
    real: Callable[..., Any] = getattr(p1812, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(args[0] if args else kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(p1812, name, spy)
    return seen


def write_snapshot(folder: Path, files: dict[str, bytes], snapshot_id: str | None = None) -> Path:
    """A snapshot folder in the pipeline.common.snapshot manifest layout."""
    folder.mkdir(parents=True)
    entries = []
    for name, data in sorted(files.items()):
        (folder / name).write_bytes(data)
        entries.append(
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        )
    manifest = {"snapshot_id": snapshot_id or folder.name, "files": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def fixture_bytes() -> dict[str, bytes]:
    return {name: (FIXTURE / name).read_bytes() for name in ("DN50.TXT", "N050.TXT")}


# The synthetic fixture


def test_fixture_holds_constants_not_itu_data() -> None:
    dn50 = np.loadtxt(FIXTURE / "DN50.TXT")
    n050 = np.loadtxt(FIXTURE / "N050.TXT")
    assert dn50.shape == n050.shape == (121, 241)
    assert np.all(dn50 == 40.0) and np.all(n050 == 325.0)
    hashes = {hashlib.sha256(data).hexdigest() for data in fixture_bytes().values()}
    assert hashes.isdisjoint({ITU_DN50_SHA256, ITU_N050_SHA256})
    manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["synthetic"] is True and manifest["snapshot_id"] == FIXTURE.name


# Map snapshot loading


def test_load_maps_reads_the_verified_fixture() -> None:
    maps = load_maps(FIXTURE)
    manifest_sha256 = hashlib.sha256((FIXTURE / "manifest.json").read_bytes()).hexdigest()
    assert maps.snapshot_id == "p1812-maps-synthetic"
    assert maps.manifest_sha256 == manifest_sha256
    assert maps.dn50.shape == (121, 241) and float(maps.dn50[60, 180]) == 40.0
    assert float(maps.n050[0, 0]) == 325.0


def test_load_maps_logs_its_start_and_end() -> None:
    with capture_logs() as logs:
        maps = load_maps(FIXTURE)
    events = {entry["event"]: entry for entry in logs}
    assert events["p1812_maps_load_begin"]["path"] == str(FIXTURE)
    end = events["p1812_maps_load_end"]
    assert end["path"] == str(FIXTURE) and end["duration_s"] >= 0.0
    assert end["snapshot_id"] == maps.snapshot_id
    assert end["manifest_sha256"] == maps.manifest_sha256


def test_load_maps_refuses_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(tmp_path / "itu-r-p1812-8-202509")


def test_load_maps_refuses_a_folder_without_manifest(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    folder.mkdir()
    for name, data in fixture_bytes().items():
        (folder / name).write_bytes(data)
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(folder)


@pytest.mark.parametrize("missing", ["DN50.TXT", "N050.TXT"])
def test_load_maps_refuses_a_missing_map_file(tmp_path: Path, missing: str) -> None:
    folder = write_snapshot(tmp_path / "maps", fixture_bytes())
    (folder / missing).unlink()
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(folder)


def test_load_maps_refuses_a_map_absent_from_the_manifest(tmp_path: Path) -> None:
    files = fixture_bytes()
    folder = write_snapshot(tmp_path / "maps", {"DN50.TXT": files["DN50.TXT"]})
    (folder / "N050.TXT").write_bytes(files["N050.TXT"])
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(folder)


def test_load_maps_refuses_a_changed_map_file(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    shutil.copytree(FIXTURE, folder)
    text = (folder / "N050.TXT").read_bytes().replace(b"325.0", b"326.0", 1)
    (folder / "N050.TXT").write_bytes(text)
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(folder)


def test_load_maps_refuses_a_manifest_naming_another_snapshot(tmp_path: Path) -> None:
    folder = write_snapshot(tmp_path / "maps", fixture_bytes(), snapshot_id="other")
    with pytest.raises(RuntimeError, match="make p1812-maps"):
        load_maps(folder)


def test_load_maps_refuses_a_grid_of_the_wrong_shape(tmp_path: Path) -> None:
    small = b"40.0 40.0\n40.0 40.0\n"
    folder = write_snapshot(tmp_path / "maps", {"DN50.TXT": small, "N050.TXT": small})
    with pytest.raises(RuntimeError, match="121 by 241"):
        load_maps(folder)


# The placeholder import


def test_import_refuses_when_py1812_p1812_is_already_loaded(tmp_path: Path) -> None:
    script = (
        "import sys, types\n"
        "sys.modules['Py1812.P1812'] = types.ModuleType('Py1812.P1812')\n"
        "import terrain.p1812\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr and "already imported" in result.stderr


def test_preconditions_refuse_an_npz_in_the_package(tmp_path: Path) -> None:
    (tmp_path / "P1812.npz").write_bytes(b"")
    with pytest.raises(RuntimeError, match=r"P1812\.npz"):
        check_import_preconditions(frozenset(), tmp_path)


def test_preconditions_refuse_an_earlier_import(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="already imported"):
        check_import_preconditions(frozenset({"Py1812.P1812"}), tmp_path)


def test_preconditions_pass_on_a_clean_package(tmp_path: Path) -> None:
    check_import_preconditions(frozenset({"Py1812"}), tmp_path)


def test_placeholder_holds_nan_and_nothing_lands_in_the_package() -> None:
    assert P1812.DigitalMaps["DN50"].shape == (1, 1)
    assert np.isnan(P1812.DigitalMaps["N050"]).all()
    assert not (Path(P1812.__file__).parent / "P1812.npz").exists()


def test_placeholder_fails_with_index_error_when_dn_and_n0_are_omitted() -> None:
    distance_km = np.linspace(0.0, 10.0, 1001)
    flat = np.full(1001, 150.0)
    with pytest.raises(IndexError):
        P1812.bt_loss(
            0.915, 50, distance_km, flat, np.zeros(1001), np.full(1001, 4),
            2.0, 2.0, 2, 38.63, 38.63, -90.25, -90.135,
        )  # fmt: skip


# Reading Lbs from the debug log


LBS_LINE = "Lbs (dB),Eq (44),,160.1700001,\n"


def test_lbs_reader_returns_the_one_logged_value() -> None:
    log_text = "Lbam (dB),Eq (62),,150.0,\n" + LBS_LINE + "Lbc (dB),Eq (63),,149.9,\n"
    assert troposcatter_loss_from_log(log_text) == pytest.approx(160.1700001, abs=1e-9)


@pytest.mark.parametrize(
    "log_text",
    [
        "Lbam (dB),Eq (62),,150.0,\n",
        LBS_LINE + LBS_LINE,
        "Lbs (dB),Eq (44),,nan,\n",
        "Lbs (dB),Eq (44),,inf,\n",
        "Lbs (dB),Eq (44),,-inf,\n",
        "Lbs (dB),Eq (44),,not a number,\n",
        "Lbs (dB),Eq (44),\n",
    ],
    ids=["none", "two", "nan", "inf", "minus inf", "text", "no value field"],
)
def test_lbs_reader_refuses_an_unusable_log(log_text: str) -> None:
    with pytest.raises(RuntimeError, match="Lbs"):
        troposcatter_loss_from_log(log_text)


# One call


def test_flat_path_matches_the_measured_figure() -> None:
    """10 km flat at 915 MHz, 2 m antennas, St. Louis DN and N0: 149.48 dB (ADR 0006 spike)."""
    result = call_flat(maps=constant_maps(39.126, 324.511))
    assert result.loss_db == pytest.approx(149.48, abs=0.01)
    assert result.troposcatter_loss_db == pytest.approx(160.17, abs=0.01)


def test_call_on_synthetic_maps_passes_dn_and_n0_from_the_maps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = spy_on(monkeypatch, "call_bt_loss")
    result = call_flat()
    (inputs,) = seen
    assert isinstance(inputs, BtLossInputs)
    assert (inputs.dn, inputs.n0) == (40.0, 325.0)
    assert np.isfinite(result.loss_db)
    assert result.troposcatter_loss_db is not None


def test_call_converts_units_and_coordinates_at_the_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = spy_on(monkeypatch, "call_bt_loss")
    call_flat(polarisation="horizontal")
    (inputs,) = seen
    assert inputs.frequency_ghz == pytest.approx(0.915)
    assert inputs.distance_km[-1] == pytest.approx(10.0)
    assert inputs.polarisation_code == 1
    assert inputs.tx_lon_deg == pytest.approx(-90.25, abs=1e-9)
    assert inputs.tx_lat_deg == pytest.approx(38.63, abs=1e-9)
    assert inputs.rx_lon_deg > inputs.tx_lon_deg


def test_vertical_maps_to_pythons_code_2(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = spy_on(monkeypatch, "call_bt_loss")
    call_flat(polarisation="vertical")
    assert seen[0].polarisation_code == 2


def test_polarisation_is_a_required_keyword() -> None:
    parameter = inspect.signature(p1812_loss).parameters["polarisation"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_result_agrees_with_the_logged_call() -> None:
    maps = constant_maps(39.126, 324.511)
    result = call_flat(clutter_m=np.full(1001, 10.0), maps=maps)
    assert result.troposcatter_loss_db is not None
    assert result.loss_db == pytest.approx(result.troposcatter_loss_db, abs=0.1)


def test_call_writes_no_file_into_the_working_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    call_flat()
    assert list(tmp_path.iterdir()) == []


def test_non_finite_loss_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def nan_loss(*args: Any, **kwargs: Any) -> tuple[float, float]:
        kwargs["fid_log"].write("Lbs (dB),Eq (44),,160.0,\n")
        return float("nan"), 0.0

    monkeypatch.setattr(P1812, "bt_loss", nan_loss)
    with pytest.raises(RuntimeError, match="not finite"):
        call_flat()


# Refused draws: NaN never reaches bt_loss (ADR 0006 decision 6 and item 11)


def one_obstacle() -> FloatArray:
    clutter = np.zeros(1001)
    clutter[500] = 30.0
    return clutter


NAN_CASES = {
    "first interior point": (np.full(1001, 10.0), 1),
    "interior point": (np.full(1001, 10.0), 300),
    "obstacle that sets the loss": (one_obstacle(), 500),
    "last interior point": (np.full(1001, 10.0), 999),
}


@pytest.mark.parametrize("case", NAN_CASES, ids=list(NAN_CASES))
def test_nan_in_clutter_is_refused_before_bt_loss(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clutter, index = NAN_CASES[case]
    clutter = clutter.copy()
    clutter[index] = np.nan
    calls: list[object] = []
    monkeypatch.setattr(P1812, "bt_loss", lambda *a, **k: calls.append(a))
    with pytest.raises(RefusedDrawError, match="clutter") as refusal:
        call_flat(clutter_m=clutter)
    assert refusal.value.reason == "clutter_nan"
    assert calls == []


def test_nan_in_ground_is_refused_before_bt_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    _, ground_m, _ = flat_path()
    ground_m[400] = np.nan
    calls: list[object] = []
    monkeypatch.setattr(P1812, "bt_loss", lambda *a, **k: calls.append(a))
    with pytest.raises(RefusedDrawError, match="ground") as refusal:
        call_flat(ground_m=ground_m)
    assert refusal.value.reason == "dem_gap"
    assert calls == []


def test_call_bt_loss_refuses_nan_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(P1812, "bt_loss", lambda *a, **k: calls.append(a))
    clutter = np.zeros(1001)
    clutter[1] = np.nan
    with pytest.raises(RefusedDrawError):
        call_bt_loss(flat_inputs(clutter))
    assert calls == []


def test_refused_draw_is_not_a_value_error() -> None:
    assert not issubclass(RefusedDrawError, ValueError)


# Profiles the wrapper rejects at the boundary


def test_coinciding_endpoints_are_a_zero_length_path() -> None:
    with pytest.raises(ValueError, match="zero-length"):
        call_flat(rx_xy_m=TX_XY_M)


def test_fewer_than_five_samples_is_a_value_error() -> None:
    distance_m = np.linspace(0.0, 10_000.0, 4)
    with pytest.raises(ValueError, match="five"):
        call_flat(distance_m=distance_m, ground_m=np.full(4, 150.0), clutter_m=np.zeros(4))


def test_profile_must_end_at_the_receiver() -> None:
    distance_m, ground_m, _ = flat_path(9_000.0)
    with pytest.raises(ValueError, match="receiver"):
        call_flat(distance_m=distance_m, ground_m=ground_m, clutter_m=np.zeros_like(ground_m))


def test_profile_without_clutter_is_a_value_error() -> None:
    """P.1812 is null where only the metro DEM exists (decision 4); path_loss_db skips the call."""
    with pytest.raises(ValueError, match="clutter"):
        call_flat(clutter_m=None)


def test_call_bt_loss_returns_the_log_text() -> None:
    loss_db, log_text = call_bt_loss(flat_inputs(np.zeros(1001)))
    assert isinstance(log_text, str) and "Lbs (dB),Eq (44)" in log_text
    assert np.isfinite(loss_db)
