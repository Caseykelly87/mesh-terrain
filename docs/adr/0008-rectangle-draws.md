# ADR 0008: Draw endpoints inside the lnk_02 rectangle, not its inscribed square

Date: 2026-11-18
Status: proposed
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

The engine places each endpoint at random inside its position box 16 times per pair (draws) and keeps each model's median and interquartile range ([`docs/PLAN.md`, Phase 3](../PLAN.md#phase-3-terrain-and-the-engine), Tasks: "uniform in each box"). Today it reads `box_half_width_m`, a square in EPSG:26915 (UTM zone 15N, metres) with sides along the grid axes, which `ingester/engine_inputs.py` writes as the square inscribed in the real box (Casey, issue #44 comment 6933558042, item 1 (b)). That item queues his (c), "drawing in the actual rectangle with the 1.6 degree rotation handled", started on issue #55 (2026-11-18, item 1) as an INTERFACE change.

The real box is the `lnk_02` box of `docs/data-quality-contract.md`: a rectangle in latitude and longitude, one cell or the rectangle around merged fixes, with edges stored as whole numbers in 1e-7 degrees and half-widths `half_width_ns_m` and `half_width_ew_m` (PLAN, Data model). Two things make the square wrong:

- The square trims the long side. A 16-bit cell at the test tile (x72y429, latitude 38.69) has half-widths 363.8 m north-south and 285.1 m east-west, so the square drops 78.7 m from each north and south end. Two 32-bit fixes 30 m apart on one parallel merge into a box about 30 m by 1 cm, whose square is 1 cm across (reviewer, PR #70).
- The rectangle is turned in EPSG:26915. East of the zone's central meridian (93 degrees west), true north lies west of grid north by the meridian convergence: 1.58 degrees at the tile's southwest corner, 1.66 at its northeast, 1.62 at its centre (pyproj `get_factors`). A cell drawn as an unrotated grid rectangle of the right size puts its corners 13 m from the real ones at 16 bits and 26 m at 15 bits, the coarsest cell `lnk_02` reads.

## Options

### Question 1: what the input layers carry

1. The four box edges, `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`: whole numbers in 1e-7 degrees (WGS84), exactly the `lnk_02` edges, half-open. The engine draws a point uniformly between the edges, in the box's own latitude and longitude, and converts each draw to EPSG:26915 with pyproj, so every draw lies inside the box and the turn comes from the conversion. Advantage: the contract's box, carried without loss; no rotation set by hand. Cost: an exception, for Casey to grant, to CLAUDE.md's "store SRID 4326, compute in EPSG:26915" and to ADR 0006's "No arithmetic on degrees", as he granted one for `lnk_05`'s geodesic (issue #55, Q4): one interpolation in degrees between a box's own edges, beside the midpoint in degrees that `lnk_02` already takes for the box centre. Uniform in degrees is not uniform in area, by about 1e-4 across a 16-bit cell.
2. The same four edges, with the four corners converted once at the boundary and each draw placed between the converted corners in metres (a bilinear blend). Advantage: no arithmetic on degrees. Cost: converted parallels bow away from the pole, so a draw near the north edge can fall outside the box by up to 5 mm on a 16-bit cell and 20 mm on a 15-bit cell, the largest box `lnk_02` produces.
3. Two metre half-widths, `box_half_width_ns_m` and `box_half_width_ew_m`, the rectangle turned by the convergence at its centre. Advantage: metres, which a caller without cells can supply. Cost: on a grid scaled by 1.0002, a 0.08 m strip at a 16-bit cell's north and south edges is never reached, and a sign error in the turn is easy to make and hard to see.

Recommend option 1: it is exact and the code is the shortest.

Under every option `box_half_width_m` and its `EndpointBox` stay, for callers with no cells and the fixtures, never both forms in one layer.

Under every option the `engine_inputs` DEM edge check tests the box's corners, not the square, so a rerun can pass fewer sites than on 2026-11-17: a result change this answer accepts; ingest-eng reports the count, counts only.

### Question 2: versions

Casey (PR #73, item 5): "if the input layers or the manifest change shape, the version that describes them bumps too, not only engine_version." No version names the input point layers today: `schema_version` fixes the artifact columns only, not the input layers. The edges add four optional input columns; no artifact column changes. The versions sit only in the manifest and square layers draw as today, so the fixture run keeps its `content_sha256` values.

1. `schema_version` 1.2.0 to 1.3.0, its scope widened to the input point layers' columns (minor: four optional columns added); `manifest_version` stays 1.1.0 (no field added), so 1.2.0 stays free for the export-rule unit; `engine_version` 0.1.0 to 0.2.0 (edge layers draw differently). Advantage: one version for every column the engine reads or writes. Cost: a `schema_version` bump with `predictions.parquet` unchanged.
2. A new manifest field `input_schema_version`, 1.0.0 for today's layers and 1.1.0 with the edges, and a field naming each input layer's box form: `manifest_version` 1.1.0 to 1.2.0 (two fields added); `schema_version` stays 1.2.0; `engine_version` 0.1.0 to 0.2.0. Advantage: a reader sees the box form without opening the layer. Cost: 1.2.0 cannot stay free; the export-rule unit takes 1.3.0.

Recommend option 1.

### Question 3: `position_uncertainty_m` for the rectangle

`lnk_05` stores `position_uncertainty_m` beside each link-hour; Phase 4 reads it as a covariate or weight. Casey (PR #73, item 6) asks the contract to state that it "is an upper bound on the error in link distance from the position boxes (both ends at opposite corners), and that the statistical spread comes from the endpoint draws". Only option 1 is such a bound; options 2 and 3 would need that sentence replaced by what they are. Options 1 and 3 change contract rule `lnk_05` and the stored values; option 1 changes 40 expected values in 33 of 51 conformance cases, for ingest-eng and warehouse-eng to review. Under every option the INTERFACE edit rewords `lnk_05`'s "until the rectangle-draws unit", so its timing does not hang on which unit lands first. Numbers at latitude 38.69, summed before rounding: two 16-bit ends; a 30 m by 1 cm merged end with a 16-bit end.

1. The sum of each end's half-diagonal, the square root of `half_width_ns_m` squared plus `half_width_ew_m` squared (PLAN, Data model). 924.3 m (924.1 to 924.6 across the tile); 477.2 m. Advantage: the farthest a point of each box lies from its centre, to within 2 cm per end for usable cells (the east-west half-width is taken at the box's middle latitude, and the box is wider at its southern edge: 3.9 mm short at 16 bits, 15.7 mm at 15 bits); by the triangle inequality the distance between any two points of the boxes differs from `distance_m` by at most this sum, the worst case being both ends at opposite corners. Cost: a worst case.
2. Keep the sum of the larger half-widths. 727.5 m; 378.8 m. Advantage: stored values and fixture unchanged. Cost: a per-axis size, not a bound: with ends at opposite corners on a long path along their diagonals the error exceeds it by up to 196.8 m.
3. The sum of each box's half-extent along the path, `half_width_ns_m` times the absolute cosine of the bearing plus `half_width_ew_m` times its absolute sine; 285.1 m to 462.2 m per 16-bit end. Advantage: tighter than 1 on long paths. Cost: only a first-order bound: on a 3 km east-west path the opposite corners give an error of 643.6 m against 570.2 m; and each end's bearing must agree between SQL and `ingester/link_rules.py` to 1e-6 m.

Recommend option 1.

## Decision

Pending Casey. Recommended: question 1, option 1; question 2, option 1; question 3, option 1.

## Consequences

If the recommendations are accepted (exact wording in PR #73):

- `docs/INTERFACE.md` (lead's edit): the point layer rows name the box as `box_half_width_m` or the four edges; a new paragraph defines the edge box, its centre, the draw, the pyproj operation and the extent; the refusal list, `endpoint_draws`, the run extent sentence, "boxes included" and the scope of `schema_version` change; the versions as question 2 decides.
- `docs/data-quality-contract.md` (lead, with Casey): `lnk_05`'s sentence, first in the INTERFACE edit, then as question 3 decides. `pos_01` and `lnk_02` need no change: the engine reads edges, not `pos_01`'s half-widths, which `lnk_05` reads unchanged under question 3, option 1; `lnk_02` already defines the box.
- `docs/PLAN.md` (lead, with Casey): Phase 3 Tasks, "uniform in each box" becomes uniform in latitude and longitude between the `lnk_02` edges. The Data model names `position_uncertainty_m` without defining it and needs no change.
- terrain-rf-eng, engine side, first: `terrain.draws.EdgeBox` with `centre_xy_m` and `corners_xy_m`, read by the draws, `engine.points`, `engine.grid` and `engine.predict`; `engine.prepare` carries the edges through, with null edges on its grid rows.
- ingest-eng, after: `engine_inputs` writes the four edges, takes its geometry from `EdgeBox.centre_xy_m`, and checks the DEM edge with the engine's `run_grid`.
- Question 3 is a separate unit, independent of the draws: one line in `ingester/link_rules.py` and the fixture values. The `docs/STATUS.md` deviation on `box_half_width_m` closes when the ingest-eng side merges.
