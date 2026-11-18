# ADR 0008: Draw endpoints inside the lnk_02 rectangle, not its inscribed square

Date: 2026-11-18
Status: proposed
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

The engine places each endpoint of a pair at random inside its position box, 16 times per pair (a draw each time), and keeps the median and interquartile range of every model's result. Today the box it reads is `box_half_width_m`, a square in EPSG:26915 (UTM zone 15N, metres) with sides along the grid axes. `ingester/engine_inputs.py` writes the smaller of the box's two half-widths, the square inscribed in the real box (Casey, issue #44 comment 6933558042, item 1 (b)). That item queues his (c): "drawing in the actual rectangle with the 1.6 degree rotation handled, as a unit before any corridor run". On issue #55 (2026-11-18, item 1) he asked for it as an INTERFACE change.

The real box is the `lnk_02` box of `docs/data-quality-contract.md`: a rectangle in latitude and longitude, one cell or the rectangle around merged fixes, with edges stored as whole numbers in 1e-7 degrees. Two things make the square wrong:

- The square trims the long side. A 16-bit cell at the test tile (x72y429, latitude 38.69) has half-widths 363.8 m north-south and 285.1 m east-west; the square drops 78.7 m from each north and south end. For a merged rectangle the trim can be almost all of it: two 32-bit fixes 30 m apart on one parallel make a box about 30 m by 1 cm, whose inscribed square is 1 cm across (reviewer, PR #70).
- The rectangle is rotated in EPSG:26915. Grid north and true north differ by the meridian convergence: 1.58 degrees at the tile's southwest corner, 1.66 at its northeast, 1.62 at its centre (pyproj `get_factors`). A cell drawn as an unrotated grid rectangle of the right size puts its corners 13 m from the real ones at 16 bits, 52 m at 14 bits and 209 m at 12 bits.

## Options

### Question 1: what the input layers carry

1. The four box edges, `box_south_i`, `box_north_i`, `box_west_i`, `box_east_i`: whole numbers in 1e-7 degrees (WGS84), exactly the `lnk_02` edges, half-open. The engine draws a point uniformly between the edges, in the box's own latitude and longitude, and converts each draw to EPSG:26915 with pyproj, so every draw lies inside the box by construction and the rotation comes from the conversion. Advantage: the box the contract defines, carried without loss; no rotation set by hand. Cost: choosing a point between two edges is arithmetic on degrees, which the boundary rule ("convert at the boundary; never do arithmetic on degrees") reads strictly as forbidden; I read it as part of the conversion, since the box is defined in those units and nothing else is computed before the point is converted. Uniform in degrees is not exactly uniform in area: across a 16-bit cell the area per degree changes by about 1e-4 of itself.
2. The same four edges, with the four corners converted once at the boundary and each draw placed in the converted corners in metres (a bilinear blend of the corners). Advantage: no arithmetic on degrees at all. Cost: the converted box edges are slightly curved and the blend's are straight, so a draw near the north or south edge can fall outside the real box by up to 5 mm on a 16-bit cell and 22 mm on a 1.2 km merged rectangle; a test that every draw lies in the box then needs a tolerance.
3. Two metre half-widths, `box_half_width_ns_m` and `box_half_width_ew_m`, with the rectangle turned by the convergence at its centre. Advantage: metres, like today's column, and readable by a caller with no cells. Cost: the half-widths are ellipsoid metres and the grid is scaled by 1.0002 here, so without that factor the draws leave a strip of about 0.08 m at the north and south edges of a 16-bit cell unreached, and the converted box is not exactly a rectangle; the engine must convert the centre back to degrees to find the convergence, and a sign error in the turn is easy to make and hard to see.

Recommend option 1. It is exact, the code is the shortest, and the only angle arithmetic picks a point inside one box.

Under every option a layer may still carry `box_half_width_m` instead, for callers with no cells and for the committed fixtures, but never both forms in one layer.

### Question 2: versions

1. `engine_version` 0.1.0 to 0.2.0; `schema_version` and `manifest_version` unchanged. The prediction columns do not change, and each input layer's sha256 in the manifest already pins its boxes. A square layer draws exactly as it does today (the same four random numbers per draw, in the same order), so the committed fixture run keeps its `content_sha256` values.
2. As 1, plus a manifest field naming each input layer's box form (manifest 1.2.0). Advantage: a reader sees the form without opening the layer. Cost: a field that repeats what the layer's hash already fixes, and 1.2.0 is the number the export-rule unit expects to take for the positions marker.

Recommend option 1.

### Question 3: `position_uncertainty_m` for the rectangle

`lnk_05` stores `position_uncertainty_m` beside each link-hour; Phase 4 uses it as a covariate or weight. Until this unit it is the sum, over the two ends, of the larger of each box's two half-widths. Worked numbers at the tile for two 16-bit ends, then for a 30 m by 1 cm merged end with a 16-bit end:

1. The sum of each end's half-diagonal, the square root of (`half_width_ns_m` squared plus `half_width_ew_m` squared), from `pos_01`'s WGS84 half-widths of the box each end used. 924.4 m; 477.2 m. Advantage: every draw lies within this distance of its box centre, so by the triangle inequality the true distance differs from `distance_m` by at most this sum; one meaning for squares, rectangles and merged boxes alike. Cost: it is a worst case, larger than a typical error.
2. Keep the sum of the larger half-widths. 727.6 m; 378.8 m. Advantage: no change anywhere. Cost: it is no bound on anything once the corners count, which is what the rectangle draws make them do.
3. The sum of each box's half-extent along the path, `half_width_ns_m` times the absolute cosine of the bearing plus `half_width_ew_m` times its absolute sine. Between 285.1 m and 462.2 m per 16-bit end depending on bearing. Advantage: the first-order bound on the distance error, tighter than 1. Cost: needs each end's geodesic bearing in SQL and in `ingester/link_rules.py`, and two implementations must agree on it to 1e-6 m.

Recommend option 1.

## Decision

Pending Casey. Recommended: question 1, option 1; question 2, option 1; question 3, option 1.

## Consequences

If the recommendations are accepted:

- `docs/INTERFACE.md` (lead's edit): the `tx_sites` and `rx_points` rows name the optional box as either `box_half_width_m` or the four edges; a new paragraph defines the edge box, its centre (the midpoint of the edges, converted from EPSG:4326 to EPSG:26915 with pyproj, `always_xy`), the draw (uniform in latitude and longitude between the edges, half-open, converted the same way) and its extent (the bounding box of its four converted corners); the refusal list adds a layer with both forms, a non-integer edge, a row with only some of its four edges null, south not below north or west not below east, a latitude outside -90 to 90 or a longitude outside -180 to 180 degrees, and a row whose geometry, after conversion to EPSG:26915, lies more than 1 mm from its box centre; `endpoint_draws` applies when either end has a `box_half_width_m` above 0 or edges; the run extent sentence says corners for an edge box; "box half-widths included" becomes "boxes included". A row with all four edges null is an exact point, so `engine.prepare` can append its synthetic grid to the real receivers.
- terrain-rf-eng, engine side, first: an `EdgeBox` type in `terrain.draws` with `centre_xy_m` and `corners_xy_m`, accepted by `draw_endpoints`, `engine.points`, `engine.grid.run_grid` and `engine.predict`; `engine.prepare` carries the edges through and its `crossing.md` column shows the larger half-diagonal of the two ends; `EndpointBox` stays.
- ingest-eng, after the engine side merges: `engine_inputs` writes the four edges and no `box_half_width_m`, takes its geometry from `EdgeBox.centre_xy_m`, and its DEM edge check passes the edges to the engine's `run_grid`. The check then uses the box's corners rather than the inscribed square, so it gets stricter and a rerun can write fewer sites than the x72y429 run of 2026-11-17.
- Question 3, option 1, is a separate unit: `lnk_05`'s wording (lead, with Casey), one line in `ingester/link_rules.py`, and 40 expected values in 33 cases of the conformance fixture (warehouse-eng with ingest-eng). It does not depend on the draws.
- The deviation in `docs/STATUS.md` on `box_half_width_m` closes when the ingest-eng side merges.
