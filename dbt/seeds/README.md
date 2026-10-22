# Seeds

Small reference tables committed as CSV and loaded by `dbt seed` into the `staging` schema. Seeds are for data that changes rarely and must be reproducible from the repository alone; anything measured belongs in `raw`.

## msa_boundary

The St. Louis, MO-IL metropolitan statistical area boundary, the polygon every later model clips positions against (`docs/data-quality-contract.md`, rule `pos_02`). A metropolitan statistical area (MSA) is a Census grouping of counties around an urban core; the Census identifies each by a five-digit core-based statistical area (CBSA) code, and St. Louis is 41180.

Source: Census TIGER/Line 2025, the national CBSA shapefile.

- URL: `https://www2.census.gov/geo/tiger/TIGER2025/CBSA/tl_2025_us_cbsa.zip`
- File: `tl_2025_us_cbsa.zip`, 34,964,000 bytes
- sha256: `8b0424f077280caaa5e09442fd0b0aec32b3e8d7d5fa909988304663bc0a761d`
- Downloaded 2026-10-22

What the feature looks like once loaded: one MultiPolygon of one polygon, 8,269 vertices, valid as parsed (`stg_msa_boundary` has no repair step, so an invalid vintage fails `assert_msa_boundary_is_valid`), 20,943.9 km2 in EPSG:26915 (20,932.1 km2 measured on the sphere with `::geography`).

### Why the WKT is split across rows

The WKT for this polygon is 179,994 characters. dbt reads seed files through Python's `csv` module, whose default limit on one field is 131,072 characters, and dbt does not expose a way to raise it, so the seed failed to load with `field larger than field limit (131072)`. The two ways out were to simplify the polygon until it fit (a Douglas-Peucker tolerance of 0.000001 degrees, about 0.1 m and the resolution of the file's own coordinates, brings it to 125,809 characters with 4 percent to spare and changes the area covered by about 0.01 km2 in total) or to split the text. The text is split so that the committed geometry is exactly the TIGER feature: the `geometry_wkt` column holds chunks of at most 100,000 characters, cut after a coordinate pair, with `part` giving their order. `stg_msa_boundary` joins them back with `string_agg(geometry_wkt, '' order by part)`. To read the CSV directly, concatenate the chunks in `part` order.

### Recipe

The conversion is not project code; it ran once in a throwaway environment and is kept here so the seed can be regenerated for a new vintage. It needs `pyshp` and `shapely`, installed by `uv run --with`, not added to `pyproject.toml`.

```bash
# In a scratch directory outside the repository.
curl -fsSL -o tl_2025_us_cbsa.zip \
  https://www2.census.gov/geo/tiger/TIGER2025/CBSA/tl_2025_us_cbsa.zip
sha256sum tl_2025_us_cbsa.zip
uv run --no-project --with pyshp --with shapely python -I extract_msa.py \
  tl_2025_us_cbsa.zip 41180 "tl_2025_us_cbsa.zip (TIGER/Line 2025)" msa_boundary.csv
cp msa_boundary.csv <repo>/dbt/seeds/msa_boundary.csv
```

`extract_msa.py`:

```python
"""Extract one CBSA from a TIGER/Line CBSA zip as a dbt seed CSV.

The WKT is written in ordered chunks of at most CHUNK_CHARS characters because
dbt reads seeds through Python's csv module, which caps a field at 131072
characters. Chunks split after a coordinate pair so each row reads cleanly.
"""

import csv
import io
import sys
import zipfile
from pathlib import Path

import shapefile
from shapely.geometry import shape
from shapely.geometry.multipolygon import MultiPolygon

CHUNK_CHARS = 100_000

zip_path, geoid, source, out_path = sys.argv[1:5]

with zipfile.ZipFile(zip_path) as archive:
    stem = next(n[:-4] for n in archive.namelist() if n.endswith(".shp"))
    parts = {ext: io.BytesIO(archive.read(f"{stem}.{ext}")) for ext in ("shp", "shx", "dbf")}

reader = shapefile.Reader(**parts)
field_names = [f[0] for f in reader.fields[1:]]
record = next(
    r for r in reader.iterShapeRecords() if r.record[field_names.index("GEOID")] == geoid
)
name = record.record[field_names.index("NAME")]
geom = shape(record.shape.__geo_interface__)
if geom.geom_type == "Polygon":
    geom = MultiPolygon([geom])
wkt = geom.wkt

chunks: list[str] = []
rest = wkt
while len(rest) > CHUNK_CHARS:
    cut = rest.rfind(", ", 0, CHUNK_CHARS) + 2
    chunks.append(rest[:cut])
    rest = rest[cut:]
chunks.append(rest)

with Path(out_path).open("w", newline="") as handle:
    writer = csv.writer(handle, lineterminator="\n")
    writer.writerow(["geoid", "name", "source", "part", "geometry_wkt"])
    for part, chunk in enumerate(chunks, start=1):
        writer.writerow([geoid, name, source, part, chunk])

print("name:", name)
print("geom_type:", geom.geom_type, "polygons:", len(geom.geoms))
print("valid:", geom.is_valid)
print("wkt_length:", len(wkt))
print("vertices:", sum(len(p.exterior.coords) for p in geom.geoms))
print("parts:", len(chunks), "max_part_chars:", max(len(c) for c in chunks))
```

Output for the 2025 vintage: `name: St. Louis, MO-IL`, `MultiPolygon polygons: 1`, `valid: True`, `wkt_length: 179994`, `vertices: 8269`, `parts: 2 max_part_chars: 99979`. The seed CSV is 180,166 bytes, sha256 `711054e1cfc2962a26db8e6c4b385468d759ff81172fd905f57c60e916925aea`.

Shapefile rings are oriented clockwise for shells and counter-clockwise for holes; `shapely.geometry.shape` reads that from pyshp's `__geo_interface__`, which is why the script does not build the polygon by hand.

To change the vintage: replace the URL, re-run, update the `source` string, this README and the checksum, then `make dbt-build`; the singular tests under `dbt/tests/assert_msa_boundary_*.sql` check the result is still the St. Louis polygon.
