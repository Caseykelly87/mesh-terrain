"""Turn one engine coverage GeoTIFF into the paletted PNG the README shows.

The coverage file holds predicted basic transmission loss in dB from one site
on 10 m cells, EPSG:26915, nodata where the engine has no value
(`docs/INTERFACE.md`). Each cell becomes one of seven 10 dB classes coloured
with seven-class viridis, lightest for the least loss, and nodata becomes a
transparent palette entry. The image is enlarged by whole pixels so a 1 km
fixture reads at README width. The PNG carries no text: the legend is the
caption table `legend_markdown` returns, which `web/README.md` holds.

Before writing anything the script checks that the run is synthetic: the run
folder has a manifest, the config given is the one the run used, and every
point layer the config names is unchanged since the run and marked
`synthetic`. This repeats the lead's manual check; it does not replace it.

It does not draw site markers, scale bars, a basemap or a hillshade, and it
never combines sites or models.

Run from the repository root:
    uv run --frozen python -I web/figures/coverage_png.py <coverage.tif> <config.yaml> <out.png>
"""

import argparse
import hashlib
import json
import math
import sys
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import rasterio
import structlog
import yaml
from rasterio.errors import NotGeoreferencedWarning

from engine.positions_marker import SYNTHETIC, positions_marker
from pipeline.common.logging import configure_logging

log = structlog.get_logger(component="web.figures.coverage_png")

FloatArray = npt.NDArray[np.float64]
IndexArray = npt.NDArray[np.uint8]
Rgba = tuple[int, int, int, int]

EDGES_DB = (60.0, 70.0, 80.0, 90.0, 100.0, 110.0)
# Seven-class viridis, light to dark: colour-blind safe and readable in grey.
COLOURS = ("#fde725", "#90d743", "#35b779", "#21918c", "#31688e", "#443983", "#440154")
NODATA_INDEX = 0
TARGET_WIDTH_PX = 600
POINT_LAYER_FIELDS = ("tx_sites", "rx_points")
EXIT_REFUSED = 2


class FigureRefusedError(Exception):
    """The run is not one whose coverage may become a public figure."""


def class_indices(values_db: FloatArray, edges_db: Sequence[float] = EDGES_DB) -> IndexArray:
    """Palette index of each cell: 0 for no value, then one class per dB interval.

    Args:
        values_db: Path loss in dB; NaN or infinite where there is no value.
        edges_db: Increasing class edges in dB. A value equal to an edge falls
            in the class above it.

    Returns:
        uint8 indices of the same shape: 1 below the first edge, len(edges_db)
        + 1 at or above the last, 0 for no value.

    Raises:
        ValueError: if the edges are empty, not finite or not strictly increasing.
    """
    edges = np.asarray(edges_db, dtype=np.float64)
    if edges.size == 0 or not np.isfinite(edges).all() or not (np.diff(edges) > 0).all():
        raise ValueError(f"class edges must be finite and strictly increasing: {list(edges_db)}")
    finite = np.isfinite(values_db)
    classes = np.digitize(np.where(finite, values_db, 0.0), edges) + 1
    return np.where(finite, classes, NODATA_INDEX).astype(np.uint8)


def colour_table(colours: Sequence[str] = COLOURS) -> dict[int, Rgba]:
    """The PNG palette: index 0 transparent, index i the opaque colour of class i."""
    table: dict[int, Rgba] = {NODATA_INDEX: (0, 0, 0, 0)}
    for index, colour in enumerate(colours, start=1):
        red, green, blue = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
        table[index] = (red, green, blue, 255)
    return table


def class_labels(edges_db: Sequence[float] = EDGES_DB) -> list[str]:
    """One plain label per class, in dB, lowest loss first."""
    edges = [f"{edge:g}" for edge in edges_db]
    middle = [f"{low} to {high}" for low, high in zip(edges, edges[1:], strict=False)]
    return [f"below {edges[0]}", *middle, f"{edges[-1]} or more"]


def legend_markdown(edges_db: Sequence[float] = EDGES_DB, colours: Sequence[str] = COLOURS) -> str:
    """The figure's legend as a Markdown table, one row per class and one for no value."""
    rows = ["| Colour | Path loss, dB |", "| --- | --- |"]
    rows += [
        f"| `{c}` | {label} |" for c, label in zip(colours, class_labels(edges_db), strict=True)
    ]
    rows.append("| none (transparent) | no value |")
    return "\n".join(rows)


def scale_factor(width_px: int, target_px: int = TARGET_WIDTH_PX) -> int:
    """The smallest whole enlargement that makes an image at least target_px wide."""
    return max(1, math.ceil(target_px / width_px))


def upscale(indices: IndexArray, factor: int) -> IndexArray:
    """Enlarge by repeating each cell as a factor by factor block, so classes stay exact.

    Raises:
        ValueError: if factor is below 1.
    """
    if factor < 1:
        raise ValueError(f"scale factor must be at least 1: {factor}")
    return np.repeat(np.repeat(indices, factor, axis=0), factor, axis=1)


def file_sha256(path: Path) -> str:
    """sha256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_manifest(run: Path) -> dict[str, Any]:
    """The run's manifest; a folder without one is not a complete run."""
    path = run / "manifest.json"
    if not path.is_file():
        raise FigureRefusedError(f"no manifest.json in {run}: not a complete engine run")
    manifest: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return manifest


def check_listed(entries: list[dict[str, Any]], name: str, path: Path) -> None:
    """Refuse a file the manifest does not list under name, or lists with another sha256."""
    listed = {entry["path"]: entry["sha256"] for entry in entries}
    if name not in listed:
        raise FigureRefusedError(f"{name} is not listed in the run's manifest")
    if listed[name] != file_sha256(path):
        raise FigureRefusedError(f"{name} has changed since the run")


def point_layer_names(config: Path) -> dict[str, str]:
    """The tx_sites and rx_points paths as the config writes them, the names the manifest lists."""
    fields = yaml.safe_load(config.read_text(encoding="utf-8"))
    return {field: str(fields[field]) for field in POINT_LAYER_FIELDS}


def check_synthetic_run(coverage: Path, config: Path) -> None:
    """Refuse unless the coverage file comes from a complete run on synthetic positions only.

    Args:
        coverage: A coverage GeoTIFF inside an engine run folder.
        config: The config file that run used; point layer paths are relative to it.

    Raises:
        FigureRefusedError: if the run has no manifest, the config or a point layer
            differs from what the manifest records, the coverage file is not
            listed with its sha256, or a point layer is not marked synthetic.
    """
    manifest = read_manifest(coverage.parent)
    if manifest.get("config_sha256") != file_sha256(config):
        raise FigureRefusedError(f"config {config.name} is not the config this run used")
    check_listed(manifest.get("artifacts", []), coverage.name, coverage)
    for name in point_layer_names(config).values():
        layer = config.parent / name
        check_listed(manifest.get("inputs", []), name, layer)
        marker = positions_marker(layer)
        if marker != SYNTHETIC:
            raise FigureRefusedError(f"{name} is marked {marker!r}, not {SYNTHETIC!r}")


def read_coverage(path: Path) -> FloatArray:
    """Path loss in dB from a north-up coverage GeoTIFF with square cells; NaN for nodata.

    Raises:
        FigureRefusedError: unless columns run west to east and rows north to
            south with square cells and no rotation; any other raster would come
            out mirrored, turned or stretched.
    """
    with rasterio.open(path) as source:
        transform = source.transform
        north_up = transform.a > 0 and transform.e < 0 and transform.b == 0 and transform.d == 0
        if not north_up or transform.a != -transform.e:
            raise FigureRefusedError(f"{path.name} is not north-up with square cells")
        values: FloatArray = source.read(1, masked=True).astype(np.float64).filled(np.nan)
    return values


def write_png(indices: IndexArray, path: Path) -> None:
    """Write a one-band paletted PNG with no georeferencing and no sidecar file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "PNG", "width": indices.shape[1], "height": indices.shape[0]}
    # A README image needs no georeferencing; rasterio warns when it is absent.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", NotGeoreferencedWarning)
        with (
            rasterio.Env(GDAL_PAM_ENABLED="NO"),
            rasterio.open(path, "w", count=1, dtype="uint8", **profile) as png,
        ):
            png.write(indices, 1)
            png.write_colormap(1, colour_table())


def render(coverage: Path, config: Path, out: Path) -> None:
    """Check the run, classify the coverage, enlarge it and write the PNG."""
    started = time.perf_counter()
    log.info("coverage_png_started", source=coverage.name, config=config.name, path=str(out))
    check_synthetic_run(coverage, config)
    indices = class_indices(read_coverage(coverage))
    counts = np.bincount(indices.ravel(), minlength=len(EDGES_DB) + 2)
    factor = scale_factor(indices.shape[1])
    write_png(upscale(indices, factor), out)
    log.info(
        "coverage_png_written",
        source=coverage.name,
        path=str(out),
        bytes=out.stat().st_size,
        cells=int(indices.size),
        nodata_cells=int(counts[NODATA_INDEX]),
        class_cells=[int(count) for count in counts[1:]],
        scale=factor,
        duration_s=round(time.perf_counter() - started, 3),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Command line entry; returns 0 when written and 2 when the run is refused."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "coverage", type=Path, help="coverage_<site_id>_<model>.tif in a run folder"
    )
    parser.add_argument("config", type=Path, help="the config file the run used")
    parser.add_argument("out", type=Path, help="PNG to write")
    args = parser.parse_args(argv)
    configure_logging("console")
    try:
        render(args.coverage, args.config, args.out)
    except FigureRefusedError as err:
        log.error("coverage_png_refused", reason=str(err))
        return EXIT_REFUSED
    return 0


if __name__ == "__main__":
    sys.exit(main())
