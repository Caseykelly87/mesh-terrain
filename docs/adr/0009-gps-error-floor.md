# ADR 0009: A 5 m GPS error floor under the engine's endpoint draws

Date: 2026-11-20
Status: accepted
Proposed by: terrain-rf-eng
Decided by: Casey

## Context

The engine places each endpoint at random inside its position box 16 times per pair and keeps each model's median and interquartile range (ADR 0008). A fine position's box can be far smaller than the error of the receiver that made it: a 32-bit cell is about 1 cm across, and two 32-bit fixes merged by `lnk_02`'s stationary floor make a box about 30 m by 1 cm. Drawing inside such a box claims centimetre knowledge of where the node stood. Casey, issue #55 comment 6962700456, item 2, verbatim: "Queue a unit for terrain-rf-eng: the engine's endpoint draws never use a half-width below a GPS error floor (propose the value with its source; I expect about 5 m). This is an engine draw setting only. The pos_01 and int_link_window box columns stay as the firmware sent them; do not reopen #59 for it."

The value comes from the receiver class Meshtastic boards carry, a u-blox M8 GNSS module (the NEO-M8N on later T-Beams). u-blox NEO-M8 data sheet, UBX-15031086, revision R14 (30-Sept-2025), section 1.3 Performance, Table 1, page 6 of 29: horizontal position accuracy 2.5 m for GPS and GLONASS together and 2.5 m for GPS alone (2.0 m with SBAS), with footnote 3, "CEP, 50%, 24 hours static, -130 dBm, > 6 SVs". CEP is the radius of the circle holding half the fixes. For a circular normal error the 95 percent radius is 2.08 times the CEP (CEP = 1.1774 sigma, R95 = 2.4477 sigma), so 2.5 m CEP is 5.2 m at 95 percent. The copy read was the PDF at content.u-blox.com, sha256 `5dc7a6c044873a04e2dcc08a7f54e9e7374c5c69dcce694054ac4730c81a04a5`, read on 2026-11-20 and not kept in the repository.

## Options

The proposal on issue #55 gave four questions with options; the record of each is short here.

1. Value: 5 m, the 95 percent reading of 2.5 m CEP; or 3.7 m, a uniform square with the same per-axis spread as the fix (h = sigma x sqrt(3)). Recommended 5 m: it errs wide and is a minimum, since urban fixes are worse than open sky.
2. Where the setting lives: a required run-config field; an engine constant recorded in the manifest; or an environment setting. Recommended the config field: INTERFACE hides no default in code, and the sensitivity table can vary it.
3. Versions: manifest 1.3.0 recording the floor, with the export-rule unit moving to 1.4.0; or folding the field into the export-rule unit. Recommended the first.
4. Widening an edge box: (A) in latitude and longitude with `pos_01`'s WGS84 radii at the box centre, then converted as every edge-box draw is; or (B) in metres on the UTM grid, mixing two frames in one draw. Recommended (A).

## Decision

Casey, issue #55 comment 7042577618, verbatim:

> GPS floor, all four as recommended:
> 1. 5 m, with the exact datasheet page and revision quoted before building; stop if the figure differs. Describe it as a minimum covering about 95 percent of open-sky fixes.
> 2. A required run-config field position_floor_m (0 turns it off; prepare's template writes 5.0), an INTERFACE change.
> 3. Versions: manifest 1.3.0 recording position_floor_m, engine 0.3.0, export-rule unit to manifest 1.4.0.
> 4. (A), widen in latitude and longitude with pos_01's WGS84 radii at the box centre; I extend the narrow exception for degree arithmetic in draws to this.
> Note in the ADR that any future drive-test data uses this same floor value, not a separate one.

The figure in the data sheet matched the proposal, 2.5 m CEP, so the build went ahead.

## Consequences

- 5 m is a minimum covering about 95 percent of open-sky fixes of an M8-class receiver; it is not an estimate of a node's error, which in town is usually larger.
- The draws never use a half-width below `position_floor_m`. A square box (`box_half_width_m` above 0) draws with max(half-width, floor). An edge box is widened about its centre on each axis whose half-width, in metres by `pos_01`'s radii at the centre latitude, is below the floor; the other axis keeps its edges, and the draw is uniform in latitude and longitude and converted to EPSG:26915 as before. An exact point (half-width 0, or all four edges null) is a model point, not a fix, and stays exact. Draws can therefore fall outside an `lnk_02` box smaller than the floor, by design. Public 15-bit and 16-bit cells (half-widths of 285 m and more) and the committed fixture's 20 m boxes draw as before.
- `position_floor_m` is a required run-config field, a finite number of at least 0; 0 turns the floor off. engine.prepare's template and the committed fixture configs write 5.0, and prepare's extent check uses the floored boxes. A run folder prepared before this ADR lacks the field and is refused until it is prepared again.
- Manifest 1.3.0 records `position_floor_m`; engine 0.3.0; `schema_version` and the point layers' `input_schema_version` are unchanged. The export-rule unit takes manifest 1.4.0.
- `pos_01`, `lnk_05`'s `position_uncertainty_m` and the `int_link_window` box columns are unchanged, as Casey said.
- Any future drive-test data uses this same floor value, not a separate one (Casey, issue #55 comment 7042577618).
