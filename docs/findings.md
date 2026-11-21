# Findings

Every number on this page is a count or a fixed setting; no node id, position, place, time or measured value from the real capture appears here, by the project's rule that real-data outcomes are not stated publicly beyond what Casey approves (issue #55 comment 7051712349).

## The question

The plan asked how much of the variation in Meshtastic link quality across the St. Louis metro is explained by terrain-based propagation models, and what explains the rest. A Meshtastic node is a small LoRa radio; a gateway is a node that also forwards what it hears to an internet MQTT broker, where anyone can read it. Each forwarded packet carries the gateway's received signal strength (RSSI, in dBm) and signal-to-noise ratio (SNR, in dB) for the last radio hop. The idea was to pair those measurements with terrain profiles between the two radios and ask which propagation model predicts them best.

The short answer: passive public mesh data cannot rank propagation models. It can rule out the crudest one on a single link, and it can show why a ranking needs a controlled measurement instead.

## What was done

1. One capture, 2026-10-22 to 2026-10-25, from the public Meshtastic broker and the MeshSTL community broker, reduced to an immutable snapshot with every node id replaced by a keyed hash (ADR 0005).
2. Link rules written once as a contract (`docs/data-quality-contract.md`) and implemented twice, in Python and in SQL, both tested against the same hand-worked conformance fixture of 56 cases.
3. A terrain-aware path-loss engine (`docs/INTERFACE.md`) that predicts loss with five models: free space, knife-edge diffraction over the terrain profile, Okumura-Hata, the Longley-Rice Irregular Terrain Model (ITM) and ITU-R P.1812, over 16 random draws of each endpoint inside its position box.
4. A comparison rule written down before any prediction was seen (STATUS, "Method for the strict x72y429 run's real link-hour"; PR #92).

## The data funnel

A link-hour is one directed radio link (the node that last transmitted a packet, to the gateway that heard it) in one clock hour, with the median RSSI and SNR of the packets counted in it.

| Stage | Count |
| --- | --- |
| Deliveries in the snapshot (one gateway's copy of one packet) | 898 791 |
| Receipts entering link resolution (first copy at a gateway, heard over radio) | 463 271 |
| Receipts left after the link rules for relayer, gateway and signal values (`lnk_01`, `lnk_03`, `lnk_06`) | 296 248 |
| Receipts with a usable position box at both ends (`lnk_02`) | 1 767 |
| Receipts counted, ends 500 m to 60 km apart (`lnk_05`) | 1 287 |
| Link-hours formed | 218 |
| Link-hours kept, at least 3 receipts (`lnk_04`) | 124 |
| Node pairs with a kept link-hour | 8 |
| Pairs with both ends in the terrain tile built at 1 m | 1 |
| Link-hours checked against predictions | 1 |

The positions explain most of the drop between the third and fourth rows:

| Position rows | Count |
| --- | --- |
| All position rows | 321 147 |
| Coarser than 15 bits of precision, rejected (`pos_01`) | 225 962 |
| Outside the St. Louis metro area (`pos_02`) | 91 078 |
| A coordinate missing, or at 0, 0 (`pos_01`, `pos_03`) | 1 451 |
| Usable | 2 656 |
| Usable, after merging copies of one broadcast heard at several gateways | 1 939, from 64 nodes |
| Of those, precise enough to count as a point | 85 |

The counts come from the Python implementation of the link rules, the code that chose the engine's inputs.

## Why the data cannot rank models

Four things stand between a gateway's reading and a model's prediction, and passive data controls none of them.

Position blur. The public broker forwards positions at 10 to 16 bits of precision; 16 bits is about plus or minus 364 m. The link rules accept 15 bits or finer, and 225 962 of 321 147 position rows were coarser. An endpoint is a box, not a point, so the engine draws 16 endpoint pairs inside the boxes and reports the spread. On a link of a few kilometres, a few hundred metres of blur can move the terrain profile enough to change which hills block the path.

Unknown transmitters and antennas. Nobody records a node's transmit power, antenna gain or cable loss. Taking published ranges for each (17 to 30 dBm transmit power, antenna gains from minus 5 to plus 8 dBi, up to 4 dB of cable loss), the predicted RSSI for a single fixed path loss spans about 38 dB. A model would have to be wrong by more than that before the data could show it.

Survivorship bias. A packet too weak to decode never reaches the broker, so it is never counted. Weak links are seen only on their good minutes, and the measured signal leans strong. The bias is largest exactly where models disagree most, on long or obstructed paths.

Too few pairs. After the link rules, about 8 node pairs in the capture have a usable link with both ends positioned. A ranking needs many independent paths across different terrain; 8 pairs in one metro cannot separate model error from the luck of each location.

A further open point: LoRa decodes below the noise floor, and many receipts have negative SNR. Whether such a receipt's RSSI is the signal or the signal plus noise depends on the radio chip, which the data does not record. The comparison rule brackets both readings rather than choosing.

## The one-link check

One link-hour lay inside the terrain tile built at 1 m resolution, with both ends positioned and enough packets to pass the rules. The engine predicted its path loss with all five models, and the measurement was compared with each under the rule fixed in advance: a model is consistent when the measured RSSI interval overlaps its predicted RSSI range, which spans the transmitter, antenna and cable uncertainty above.

Under the rule fixed in advance, free-space loss was inconsistent with the measured link: the link lost more than open air would explain. Knife-edge, Okumura-Hata, ITM and P.1812 were not ruled out, and one link-hour of four packets cannot separate them.

That is what one link can say. "Not ruled out" is weak: the predicted range is about 38 dB wide before the spread from the position draws, so it takes a gross error to fail.

## Two implementations

The link rules exist twice, in Python (`ingester/link_rules.py`) and in SQL (the dbt models), written independently from the contract and checked against the same hand-worked fixture. On the real capture, with one failing staging test excluded, both implementations keep the same 124 link-hours: every key (relayer, gateway, clock hour) is in both, and none is in only one. They agree at every link level:

| Level | SQL | Python |
| --- | --- | --- |
| Link-hours formed | 218 | 218 |
| Link-hours kept | 124 | 124 |
| Node pairs | 8 | 8 |
| Pairs with both ends in the 1 m tile, and their kept link-hours | 1 and 1 | 1 and 1 |

"With one failing staging test excluded": the first real build stopped when the staging range test `dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000` found 1 position row with a latitude outside plus or minus 90 degrees, which skipped every model after it. The comparison comes from a second build with only that test excluded:

```
uv run --frozen dbt build --project-dir dbt --profiles-dir dbt --target prod --vars '{real_build: true}' --exclude dbt_utils_accepted_range_stg_position_latitude_i__900000000___900000000
```

At the receipt level they differ. Python excludes 265 more receipts under `lnk_06` (18 388 against 18 123), yet keeps 311 more overall (296 248 against 295 937), so SQL drops about 576 more under `lnk_01` and `lnk_03`. The difference does not reach the kept link-hours. It is recorded here and in `docs/ROADMAP.md`, and was not investigated in this phase.

## What would answer the question

A controlled measurement: radios with known transmit power, antennas and heights, driven along many paths across different terrain, logging every packet sent so that missing packets count too. That removes the transmitter unknowns and the survivorship bias, replaces position boxes with GPS points, and gives hundreds of paths instead of 8. The plan for it is the first item in `docs/ROADMAP.md`. The engine built here is ready for that data: it takes exact points as well as boxes, and every run is traceable to its code and inputs through its manifest.

## Limits of this page

One capture of four days, one metro, one checked link-hour. Antenna heights are assumed at 2 m. The comparison rule's allowances (5 dB for RSSI calibration, the transmitter and antenna ranges) are judgement, with sources in the method record. Nothing here measures how well any model works in general.
