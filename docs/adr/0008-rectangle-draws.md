# ADR 0008: Draw endpoints inside the lnk_02 rectangle, not its inscribed square

Date: 2026-11-18
Status: accepted
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

The engine places each endpoint at random inside its position box 16 times per pair (draws) and keeps each model's median and interquartile range ([`docs/PLAN.md`, Phase 3](../PLAN.md#phase-3-terrain-and-the-engine), Tasks: "uniform in each box"). It read `box_half_width_m`, a square in EPSG:26915 (UTM zone 15N, metres) with sides along the grid axes, which `ingester/engine_inputs.py` writes as the square inscribed in the real box (Casey, issue #44 comment 6933558042, item 1 (b)). That item queues his (c), "drawing in the actual rectangle with the 1.6 degree rotation handled", started on issue #55 (2026-11-18, item 1) as an INTERFACE change.

The real box is the `lnk_02` box of `docs/data-quality-contract.md`: a rectangle in latitude and longitude, one cell or the rectangle around merged fixes, with edges stored as whole numbers in 1e-7 degrees and half-widths `half_width_ns_m` and `half_width_ew_m` (PLAN, Data model). Two things make the square wrong:

- The square trims the long side. A 16-bit cell at the test tile (x72y429, latitude 38.69) has half-widths 363.8 m north-south and 285.1 m east-west, so the square drops 78.7 m from each north and south end. Two 32-bit fixes 30 m apart on one parallel merge into a box about 30 m by 1 cm, whose square is 1 cm across (reviewer, PR #70).
- The rectangle is turned in EPSG:26915. East of the zone's central meridian (93 degrees west), true north lies west of grid north by the meridian convergence: 1.58 degrees at the tile's southwest corner, 1.66 at its northeast, 1.62 at its centre (pyproj `get_factors`). An unrotated grid rectangle of the right size puts a cell's corners 13 m from the real ones at 16 bits and 26 m at 15 bits, the coarsest cell `lnk_02` reads.

## Options

### Question 1: what the input layers carry

1. The four box edges, `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`: whole numbers in 1e-7 degrees (WGS84), exactly the `lnk_02` edges, half-open. The engine draws a point uniformly between the edges in latitude and longitude and converts each draw to EPSG:26915 with pyproj, so every draw lies inside the box and the turn comes from the conversion. Advantage: the contract's box, carried without loss; no rotation set by hand. Cost: an exception to CLAUDE.md's "store SRID 4326, compute in EPSG:26915" and ADR 0006's "No arithmetic on degrees", like the one for `lnk_05`'s geodesic (issue #55, Q4): one interpolation between a box's own edges, beside the midpoint in degrees `lnk_02` already takes. Uniform in degrees is not uniform in area, by about 1e-4 across a 16-bit cell.
2. The same edges, with the corners converted once and each draw a bilinear blend of them in metres. Advantage: no arithmetic on degrees. Cost: converted parallels bow away from the pole, so a draw near the north edge can fall outside the box by up to 5 mm on a 16-bit cell and 20 mm on a 15-bit cell, the largest box `lnk_02` produces.
3. Two metre half-widths, the rectangle turned by the convergence at its centre. Advantage: metres, which a caller without cells can supply. Cost: on a grid scaled by 1.0002, a 0.08 m strip at a 16-bit cell's north and south edges is never reached, and a sign error in the turn is easy to make and hard to see.

Recommended option 1. Under every option `box_half_width_m` and its `EndpointBox` stay, for callers with no cells and the fixtures, never both forms in one layer; and the `engine_inputs` DEM edge check tests the box's corners, not the square, so a rerun can pass fewer sites than on 2026-11-17.

### Question 2: versions

Casey (PR #73, item 5): "if the input layers or the manifest change shape, the version that describes them bumps too, not only engine_version." No version named the input point layers: `schema_version` fixes the artifact columns only. The edges add four optional input columns; no artifact column changes.

1. `schema_version` 1.2.0 to 1.3.0, widened to the input point layers; `manifest_version` stays 1.1.0; `engine_version` 0.1.0 to 0.2.0. Advantage: one version for every column the engine reads or writes. Cost: a `schema_version` bump with `predictions.parquet` unchanged.
2. `manifest_version` 1.1.0 to 1.2.0, recording each point layer's `input_schema_version` (1.0.0 without the edges, 1.1.0 with them) and box form; `schema_version` stays 1.2.0; `engine_version` 0.1.0 to 0.2.0. Advantage: a reader sees the box form without opening the layer. Cost: the export-rule unit moves to manifest 1.3.0.

Recommended option 1.

### Question 3: `position_uncertainty_m` for the rectangle

`lnk_05` stores `position_uncertainty_m` beside each link-hour; Phase 4 reads it as a covariate or weight. Casey (PR #73, item 6) asks the contract to state that it "is an upper bound on the error in link distance from the position boxes (both ends at opposite corners), and that the statistical spread comes from the endpoint draws". Only option 1 is such a bound. Options 1 and 3 change contract rule `lnk_05` and the stored values; option 1 changes 40 expected values in 33 of 51 conformance cases. Numbers at latitude 38.69, summed before rounding: two 16-bit ends; a 30 m by 1 cm merged end with a 16-bit end.

1. The sum of each end's half-diagonal, the square root of `half_width_ns_m` squared plus `half_width_ew_m` squared. 924.3 m (924.1 to 924.6 across the tile); 477.2 m. Advantage: the farthest a point of each box lies from its centre, to within 2 cm per end for usable cells (3.9 mm short at 16 bits, 15.7 mm at 15 bits, since the east-west half-width is taken at the middle latitude); by the triangle inequality the distance between any two points of the boxes differs from `distance_m` by at most this sum. Cost: a worst case.
2. Keep the sum of the larger half-widths. 727.5 m; 378.8 m. Advantage: stored values and fixture unchanged. Cost: not a bound: with ends at opposite corners on a long path along their diagonals the error exceeds it by up to 196.8 m.
3. The sum of each box's half-extent along the path; 285.1 m to 462.2 m per 16-bit end. Advantage: tighter than 1 on long paths. Cost: a first-order bound only (643.6 m of error against 570.2 m on a 3 km east-west path), and each end's bearing must agree between SQL and Python to 1e-6 m.

Recommended option 1.

## Decision

Casey, PR #73 comment 6995479266, verbatim:

> Q1: (a), the four lnk_02 edges, drawn evenly in latitude and longitude then converted to EPSG:26915. I grant the narrow exception to "compute in EPSG:26915" and ADR 0006 for drawing inside a box, as for lnk_05's geodesic.
> Q2: (b). manifest_version to 1.2.0, recording each input layer's schema version and box form, engine_version to 0.2.0; the export-rule unit takes manifest 1.3.0. Reason: a run's manifest must say whether its draws used rectangles or squares without opening the layers.
> Q3: (a), the sum of each end's half-diagonal, with my item 6 sentence in lnk_05. Built as its own unit, with the 40 expected values reviewed by ingest-eng and warehouse-eng as you described.
> The stricter DEM edge check is accepted.

Question 2 is option 2, not the recommended option 1.

## Consequences

- PR #73 builds the engine side: `terrain.boxes.EdgeBox` (centre, corners, extent, draws), read by `engine.points` with either box form and checked to 1 mm against its centre, used by `engine.grid` and `engine.prepare`; manifest 1.2.0 records each point layer's `input_schema_version` (1.0.0 without the edges, 1.1.0 with them) and `box_form` (`rectangle`, `square` or `point`); the calibration observations layer has no box and no point schema, so it records neither; engine 0.2.0. Square layers draw as before, so the committed fixture run's content hashes do not change.
- PR #73 carries Casey's INTERFACE and PLAN wording and the interim `lnk_05` clause, "until the `position_uncertainty_m` unit (ADR 0008, question 3)", as the proposal he answered set out; the lead co-assigned these lead-only edits to terrain-rf-eng for PR #73. `pos_01` and `lnk_02` need no change.
- ingest-eng, after PR #73 merges: `engine_inputs` writes the four edges, takes its geometry from `EdgeBox.centre_xy_m`, and checks the DEM edge with the engine's `run_grid`. The `docs/STATUS.md` deviation on `box_half_width_m` closes then.
- Question 3 is its own unit: `lnk_05`'s final wording with Casey's item 6 sentence, one line in `ingester/link_rules.py`, and the 40 fixture values, reviewed by ingest-eng and warehouse-eng.
- The export-rule unit takes manifest 1.3.0.
