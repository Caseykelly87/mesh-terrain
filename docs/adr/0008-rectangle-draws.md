# ADR 0008: Draw endpoints inside the lnk_02 rectangle, not its inscribed square

Date: 2026-11-18
Status: proposed
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

The engine places each endpoint of a pair at random inside its position box, 16 times per pair (a draw each time), and keeps the median and interquartile range of every model's result ([`docs/PLAN.md`, Phase 3](../PLAN.md#phase-3-terrain-and-the-engine), Tasks: "endpoint sampling (N draws, default 16, uniform in each box; median and interquartile range per quantity)"). Today it reads `box_half_width_m`, a square in EPSG:26915 (UTM zone 15N, metres) with sides along the grid axes, which `ingester/engine_inputs.py` writes as the square inscribed in the real box (Casey, issue #44 comment 6933558042, item 1 (b)). That item queues his (c), "drawing in the actual rectangle with the 1.6 degree rotation handled, as a unit before any corridor run", and on issue #55 (2026-11-18, item 1) he asked for it as an INTERFACE change.

The real box is the `lnk_02` box of `docs/data-quality-contract.md`: a rectangle in latitude and longitude, one cell or the rectangle around merged fixes, with edges stored as whole numbers in 1e-7 degrees and half-widths `half_width_ns_m` and `half_width_ew_m` (PLAN, Data model). Two things make the square wrong:

- The square trims the long side. A 16-bit cell at the test tile (x72y429, latitude 38.69) has half-widths 363.8 m north-south and 285.1 m east-west, so the square drops 78.7 m from each north and south end. For a merged rectangle the trim can be almost all of it: two 32-bit fixes 30 m apart on one parallel make a box about 30 m by 1 cm, whose inscribed square is 1 cm across (reviewer, PR #70).
- The rectangle is turned in EPSG:26915. East of the zone's central meridian (93 degrees west), true north lies west of grid north by the meridian convergence: 1.58 degrees at the tile's southwest corner, 1.66 at its northeast, 1.62 at its centre (pyproj `get_factors`). A cell drawn as an unrotated grid rectangle of the right size puts its corners 13 m from the real ones at 16 bits and 26 m at 15 bits, the coarsest cell `lnk_02` reads.

## Options

### Question 1: what the input layers carry

1. The four box edges, `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`: whole numbers in 1e-7 degrees (WGS84), exactly the `lnk_02` edges, half-open. The engine draws a point uniformly between the edges, in the box's own latitude and longitude, and converts each draw to EPSG:26915 with pyproj, so every draw lies inside the box and the turn comes from the conversion. Advantage: the contract's box, carried without loss; no rotation set by hand. Cost: an exception, for Casey to grant, to CLAUDE.md's "store SRID 4326, compute in EPSG:26915" and to ADR 0006's "No arithmetic on degrees", as he granted one for `lnk_05`'s geodesic (issue #55, Q4): one interpolation in degrees between a box's own edges, beside the midpoint in degrees that `lnk_02` already takes for the box centre. Uniform in degrees is not exactly uniform in area: across a 16-bit cell the area per degree changes by about 1e-4 of itself.
2. The same four edges, with the four corners converted once at the boundary and each draw placed between the converted corners in metres (a bilinear blend). Advantage: no arithmetic on degrees. Cost: converted parallels bow away from the pole, so the blend's straight north edge lies outside the real box: a draw near the north edge can fall outside it by up to 5 mm on a 16-bit cell and 20 mm on a 15-bit cell, the largest box `lnk_02` produces. Tests then need a tolerance.
3. Two metre half-widths, `box_half_width_ns_m` and `box_half_width_ew_m`, with the rectangle turned by the convergence at its centre. Advantage: metres, usable by a caller with no cells. Cost: the half-widths are ellipsoid metres on a grid scaled by 1.0002, so a strip of about 0.08 m at the north and south edges of a 16-bit cell is never reached; the engine must convert the centre back to degrees for the convergence, and a sign error in the turn is easy to make and hard to see.

Recommend option 1: it is exact and the code is the shortest.

Under every option a layer may carry `box_half_width_m` instead, for callers with no cells and for the committed fixtures, but never both forms in one layer; `box_half_width_m` and its `EndpointBox` stay.

Under every option the `engine_inputs` DEM edge check tests the box's corners instead of the inscribed square, so a rerun can pass fewer sites than the x72y429 run of 2026-11-17. That is a result change Casey's answer to this question accepts; ingest-eng reports the new count, counts only, when its writer switches.

### Question 2: versions

1. `engine_version` 0.1.0 to 0.2.0; `schema_version` and `manifest_version` unchanged. The prediction columns do not change, and each input layer's sha256 in the manifest already pins its boxes. A square layer draws exactly as today (the same four random numbers per draw, in the same order), so the committed fixture run keeps its `content_sha256` values.
2. As 1, plus a manifest field naming each input layer's box form (manifest 1.2.0). Cost: it repeats what the layer's hash fixes, and takes the 1.2.0 the export-rule unit expects.

Recommend option 1.

### Question 3: `position_uncertainty_m` for the rectangle

`lnk_05` stores `position_uncertainty_m` beside each link-hour; Phase 4 reads it as a covariate or weight. Until this unit it is the sum, over the two ends, of the larger of each box's two half-widths. Options 1 and 3 change contract rule `lnk_05` and the stored values of that column; under option 1 all 40 expected values in 33 of the 51 conformance cases change, and that fixture change needs review by both ingest-eng and warehouse-eng. Under every option `lnk_05`'s "until the rectangle-draws unit" is reworded. Worked numbers at latitude 38.69 for two 16-bit ends, then for a 30 m by 1 cm merged end with a 16-bit end, summed before rounding:

1. The sum of each end's half-diagonal, the square root of `half_width_ns_m` squared plus `half_width_ew_m` squared, from the box's half-widths (PLAN, Data model). 924.3 m (924.1 to 924.6 across the tile); 477.2 m. Advantage: one meaning for cells and merged boxes, the farthest a point of the box lies from its centre, to within 2 cm per end for usable cells (the east-west half-width is taken at the box's middle latitude and the box is a little wider at its southern edge: 3.9 mm short at 16 bits, 15.7 mm at 15 bits). By the triangle inequality, the geodesic between any two points of the two boxes differs from `distance_m` by at most this sum, to the same 2 cm per end. Cost: a worst case, larger than a typical error.
2. Keep the sum of the larger half-widths. 727.5 m; 378.8 m. Advantage: the stored values and the fixture stay as they are. Cost: it was never a bound on the distance error, only a per-axis size; `lnk_05`'s "until the rectangle-draws unit" still needs rewording.
3. The sum of each box's half-extent along the path, `half_width_ns_m` times the absolute cosine of the bearing plus `half_width_ew_m` times its absolute sine. Between 285.1 m and 462.2 m per 16-bit end, depending on bearing. Advantage: the first-order bound on the distance error, tighter than 1. Cost: each end's geodesic bearing, in SQL and in `ingester/link_rules.py`, agreeing to 1e-6 m.

Recommend option 1.

## Decision

Pending Casey. Recommended: question 1, option 1; question 2, option 1; question 3, option 1.

## Consequences

If the recommendations are accepted (the exact wording for each document is in PR #73):

- `docs/INTERFACE.md` (lead's edit): the point layer rows name the box as `box_half_width_m` or the four edges; a new paragraph defines the edge box, its centre, the draw, the pyproj operation and the extent; the refusal list, `endpoint_draws`, the run extent sentence and "boxes included" change; `engine_version` 0.2.0.
- `docs/data-quality-contract.md` (lead, with Casey): `lnk_05`'s `position_uncertainty_m` sentence as question 3 decides. `pos_01` and `lnk_02` need no change: `pos_01` defines a cell's half-widths, which the engine does not read, and `lnk_02` already defines the box and its edges.
- `docs/PLAN.md` (lead, with Casey): Phase 3 Tasks, "uniform in each box" becomes uniform in latitude and longitude between the `lnk_02` edges. The Data model names `position_uncertainty_m` without defining it and needs no change.
- terrain-rf-eng, engine side, first: an `EdgeBox` type in `terrain.draws` with `centre_xy_m` and `corners_xy_m`, read by `draw_endpoints`, `engine.points`, `engine.grid.run_grid` and `engine.predict`; `engine.prepare` carries the edges through, writes its grid rows with null edges, and its `crossing.md` column shows the larger half-diagonal of the two ends.
- ingest-eng, after the engine side merges: `engine_inputs` writes the four edges and no `box_half_width_m`, takes its geometry from `EdgeBox.centre_xy_m`, and passes the edges to the engine's `run_grid` for its DEM edge check.
- Question 3 is a separate unit, independent of the draws: the `lnk_05` wording, one line in `ingester/link_rules.py`, and the fixture values. The `docs/STATUS.md` deviation on `box_half_width_m` closes when the ingest-eng side merges.
