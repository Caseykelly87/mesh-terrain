# Findings

Every number on this page is a count or a fixed setting. No node id, position, place smaller than the metro, time, distance or measured value from the real capture appears here, under the project's rule that real-data outcomes are stated publicly only as Casey approves; Casey approved publishing these counts and the one sentence about the one-link check (issue #55 comment 7051712349).

## The question

The plan asked how much of the variation in Meshtastic link quality across the St. Louis metro is explained by terrain-based propagation models, and what explains the rest. A Meshtastic node is a small LoRa radio (a low-power, long-range radio in the 915 MHz band). A gateway is a node that also forwards what it hears to an internet MQTT broker, where anyone can read it. Each forwarded packet carries the gateway's received signal strength (RSSI, in dBm) and signal-to-noise ratio (SNR, in dB) for the last radio hop. Path loss is how much signal is lost between the two radios; a propagation model predicts it. The idea was to pair the measurements with terrain profiles between the radios and ask which model predicts them best.

The short answer: passive public mesh data cannot rank propagation models. This page shows why, and what a ranking would need instead.

## What was done

1. A single capture from 2026-10-22 to 2026-10-25, from the public Meshtastic broker and the MeshSTL community broker, reduced to an immutable snapshot with every node id replaced by a keyed hash (ADR 0005).
2. Link rules written once as a contract (`docs/data-quality-contract.md`) and implemented twice, in Python and in SQL. Both pass the same conformance fixture: 56 hand-worked cases, each with the outcome the contract requires.
3. A terrain-aware path-loss engine (`docs/INTERFACE.md`) with five models: free space; knife-edge, which adds the diffraction loss of the hills on the terrain profile; Okumura-Hata, an empirical formula from city measurements; the Longley-Rice Irregular Terrain Model (ITM); and ITU-R P.1812, which also reads the height of buildings and trees along the path. Every real position is a box, not a point, so each link is predicted over random draws of both endpoints inside their boxes, 16 draws in these runs, with each box at least 5 m either side of its centre (ADR 0009).
4. A comparison rule written down before any prediction was seen (PR #92).

## The data funnel

A link-hour is one directed radio link, from the relayer (the node that last transmitted a packet) to the gateway that heard it, in one clock hour, with the median RSSI and SNR of the packets counted in it. A pair is a directed (relayer, gateway) link.

| Stage | Count |
| --- | --- |
| Deliveries in the snapshot (one gateway's copy of one packet) | 898 791 |
| Receipts entering link resolution (the first copy at a gateway, `ing_06`, heard over radio) | 463 271 |
| Receipts left after the rules for relayer, gateway and signal values (`lnk_01`, `lnk_03`, `lnk_06`) | 296 248 |
| Receipts with a usable position box at both ends (`lnk_02`) | 1 767 |
| Receipts counted: ends 500 m to 60 km apart, boxes not overlapping (`lnk_05`) | 1 287 |
| Link-hours formed | 218 |
| Link-hours kept: at least 3 receipts (`lnk_04`), one box per end (`lnk_02`), median distance at least 500 m (`lnk_05`) | 124 |
| Pairs with a kept link-hour (none heard in both directions) | 8 |
| Pairs with both ends in the terrain tile built at 1 m | 1 |
| Link-hours checked against predictions | 1 |

Positions explain all of the drop between the third and fourth rows. A position row is judged in this order:

| Position rows | Count |
| --- | --- |
| All position rows | 321 147 |
| A coordinate missing (`pos_01`) | 1 393 |
| Precision outside 15 to 32 bits, or none given (`pos_01`; see Position blur) | 225 962 |
| At 0, 0 (`pos_03`) | 58 |
| Outside the St. Louis metro area (`pos_02`) | 91 078 |
| Usable | 2 656 |
| Usable, after merging copies of one broadcast heard at several gateways | 1 939, from 64 nodes |
| Of those, flagged precise under `pos_04` (20 bits or finer, with a copy from the MeshSTL broker) | 85 |

The receipt, link-hour and pair counts come from the Python link rules, called by a local script kept outside git exactly as the engine's input step calls them; the position split comes from the same tests as `read_position` in `ingester/link_rules.py`, applied by that script in the same order. The SQL counts in the comparison below come from the warehouse build described there.

## Why the data cannot rank models

Four things stand between a gateway's reading and a model's prediction, and passive data controls none of them.

Position blur. A position's precision is the number of bits of latitude and longitude the node keeps; fewer bits mean a bigger box. The public broker forwards positions at 10 to 16 bits, and 16 bits is about plus or minus 364 m north to south. The link rules accept 15 to 32 bits. On a link of a few kilometres, a few hundred metres of blur can move the terrain profile enough to change which hills block the path, so the engine reports the spread over its endpoint draws rather than one number.

Unknown transmitters and antennas. Nobody records a node's transmit power, antenna gain (dBi, gain relative to an ideal antenna that radiates equally in all directions) or cable loss. The comparison rule takes ranges for each: transmit power 17 to 30 dBm from the firmware and radio limits; transmitter antenna minus 5 to plus 3 dBi and gateway antenna minus 5 to plus 8 dBi, the upper ends from vendor and community antenna figures and the minus 5 dBi lower end judgement; cable loss up to 1 dB at the transmitter and 3 dB at the gateway, partly judgement. Together they spread the predicted RSSI for a single path loss over about 38 dB. With the rule's 5 dB allowance at each end, an error in a model's loss can hide anywhere inside a window about 48 dB wide, before the spread over the endpoint draws, and where the true settings sit inside it nobody recorded.

Survivorship bias. A packet too weak to decode never reaches the broker, so it is never counted. Weak links are seen only on their good minutes, and the measured signal leans strong. The bias is largest where models disagree most, on long or obstructed paths.

Too few pairs. After the link rules, 8 pairs in the capture have a kept link-hour with both ends positioned, none heard in both directions: 8 paths. A ranking needs many independent paths across different terrain; 8 in one metro cannot separate model error from the luck of each location.

A further open point: LoRa can decode packets below the noise floor (the background radio noise), where SNR is negative. Whether such a receipt's RSSI is the signal alone or the signal plus noise depends on the radio chip, which the data does not record. The comparison rule brackets both readings rather than choosing.

## The one-link check

One link-hour lay inside the terrain tile built at 1 m resolution, with both ends positioned and enough packets to pass the rules. The engine predicted its path loss with all five models. Each model's predicted RSSI range spans the transmitter, antenna and cable ranges above, plus half the interquartile range (the spread of the middle half) of its loss over the endpoint draws. The measured interval runs from a noise-corrected signal estimate minus 5 dB to the median RSSI plus 5 dB. A model is consistent when the two overlap.

Under the rule fixed in advance, free-space loss was inconsistent with the measured link: the link lost more than open air would explain. Knife-edge, Okumura-Hata, ITM and P.1812 were not ruled out, and one link-hour of four packets cannot separate them.

## Two implementations

The link rules exist twice, in Python (`ingester/link_rules.py`) and in SQL (models built with dbt, a tool that runs SQL transformations and their tests, in the PostGIS warehouse), written independently from the contract and checked against the same fixture. On the real capture, with one failing staging test excluded, both keep the same 124 link-hours: every key (relayer, gateway, clock hour) is in both, and none is in only one. They agree at every link level:

| Level | SQL | Python |
| --- | --- | --- |
| Link-hours formed | 218 | 218 |
| Link-hours kept | 124 | 124 |
| Pairs | 8 | 8 |
| Pairs with both ends in the 1 m tile, and their kept link-hours | 1 and 1 | 1 and 1 |

"With one failing staging test excluded": staging is the warehouse layer that cleans the raw rows. The first real build stopped when `pos_03`'s contract test on staging, `dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000`, found 1 position row with a latitude outside plus or minus 90 degrees, which skipped every model downstream of `stg_position`. The comparison comes from a second build with only that test excluded:

```
uv run --frozen dbt build --project-dir dbt --profiles-dir dbt --target prod --vars '{real_build: true}' --exclude dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000
```

At the receipt level they differ. Python excludes 265 more receipts under `lnk_06` (18 388 against 18 123), yet keeps 311 more overall (296 248 against 295 937), so SQL drops about 576 more under `lnk_01` and `lnk_03`. The difference does not reach the kept link-hours. It is recorded here and in `docs/ROADMAP.md`, and was not investigated in this phase.

## What would answer the question

A controlled measurement: radios with known transmit power, antennas and heights, driven along many paths across different terrain, logging every packet sent so that missing packets count too. That removes the transmitter unknowns and the survivorship bias, shrinks each position box to a GPS fix drawn under the same 5 m floor, and gives many more paths than 8. The plan for it is the first item in `docs/ROADMAP.md`. Every engine run is traceable to its code and inputs through its manifest.

## Limits of this page

A single capture from 2026-10-22 to 2026-10-25, one metro, one checked link-hour. Antenna heights are assumed at 2 m. The comparison rule's 5 dB allowance for RSSI calibration and parts of the antenna and cable ranges are judgement, with sources in the method record (PR #92). Nothing here measures how well any model works in general.
