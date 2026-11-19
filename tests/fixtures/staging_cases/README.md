# Staging cases

warehouse-eng's synthetic cases for what the link-rules fixture (`tests/fixtures/link_rules/`) cannot hold, because that fixture is one snapshot and the Python side reads one:

| Case | What it tests | Rules |
| --- | --- | --- |
| `s01_one_packet_in_two_snapshots` | one packet in two snapshots: `stg_packet` keeps the first copy by `received_at`, and the later snapshot's copy wins when it is earlier (issue #55 case 35); one packet at two gateways, kept at each; two deliveries without `packet_id`, kept apart; core keeps one row per position delivery, with the broker flags of every copy | ing_01, ing_06, pos_02, pos_04 |
| `s02_msa_edge_32_bit_stored_value` | two 32-bit positions whose stored value and cell centre lie on opposite sides of the MSA edge: core reads the stored value (Casey, PR #66, Q3) | pos_02 |
| `s03_precisions_the_link_rules_fixture_does_not_use` | a 12-bit coarse cell, a position at 0 bits, a precise MapReport, a 33-bit MeshSTL Position | pos_01, pos_02, pos_04 |
| `s04_meshstl_position_at_0_bits` | a 0-bit MeshSTL position with coordinates is precise, Position or MapReport; a 0-bit Position or MapReport whose later snapshot's copy is MeshSTL is precise in core; a 0-bit MeshSTL Position at 0, 0 carries no coordinates and is not flagged (Casey, issue #55 comment 6985802330, item 7, comment 6989099568, item 2, and PR #71 comment 6989275630, item 1) | pos_01, pos_02, pos_03, pos_04 |
| `s05_direct_hop_beside_a_byte_twin` | a direct hop at a gateway that also heard another node with the sender's byte: the direct-hop clause names the sender | lnk_01 |
| `s06_own_box_sets_aside_a_coarser_cell` | a receipt's own stationary box, on an end with no hour box, sets aside a coarser cell holding a finer position | lnk_02 |
| `s07_finer_fix_on_a_coarse_cell_upper_edge` | a 32-bit fix exactly on a 21-bit cell's north edge, and one on its east edge, is not held by it | lnk_02 |
| `s08_cells_touching_north_to_south` | two cells sharing a latitude edge do not overlap (c14 is the longitude axis) | lnk_02 |
| `s09_mixed_precise_cells_off_the_hour_box` | an end off the hour box covering a precise and an imprecise cell is not precise | lnk_02, pos_04 |
| `s10_hour_box_needs_every_receipt_stationary` | no hour box when the end is not stationary at one receipt | lnk_02 |
| `s11_hour_box_union_starts_7_days_before_the_first_receipt` | the hour box reads from 7 days before the first receipt | lnk_02 |
| `s12_one_broadcast_at_two_gateways_is_one_position` | one broadcast's copies at two gateways are one position, timed by the first copy, precise when any copy qualifies | lnk_02, pos_04 |
| `s13_readings_outside_the_chips_range` | readings outside the range the radio chips can report land as missing in staging, both edges kept, and the earlier RSSI limits now inside (Casey, PR #72, Q2 and his RSSI answer) | lnk_06 |
| `s14_fallback_tie_at_one_instant_goes_to_the_first_copys_key` | two broadcasts equally near at one instant: the fallback takes the one whose first copy has the smaller delivery key (Casey, PR #72, his answer to PR #79, Q1) | lnk_02 |

The files use the link-rules case format (`case_id`, `title`, `rules`, `reasoning`, `messages`) with two additions: an `expected_positions` list, and every message names its own `snapshot_id`, `staging-fixture-a` or `staging-fixture-b`. s05 to s12 and s14 test link-rule clauses the shared link-rules fixture does not reach (reviewer, PR #72), so they also carry that fixture's `boxes`, `expected_receipts` and `expected_link_hours`, written by hand in the same way and compared by the same dbt tests; each is in `staging-fixture-a`, and the link-rules README's counts across that fixture do not include them. A message absent from a column is null, and raw's other not-null columns take the link-rules README's defaults. Pseudonyms start with the case number (`s01-r`), so they never meet a link-rules node.

Every expected value here is worked out by hand from the contract, never by running B3 code (Casey's rule for the conformance fixture):

- `assert_ing_06_first_copy_kept` lists the `stg_packet` rows of s01 and c40.
- Each staging case's `expected_positions` gives, per position row, `position_quality`, `position_zero_mapped`, `precise_source`, `inside_msa`, `in_core` and the precise flag core gives the delivery (`core_precise_source`), with the reasoning beside them.
- `link_rules_positions.json` gives the same columns for every position row of the link-rules cases, which stay unchanged; its reasoning says how each was decided. New link-rules cases (B2b) get rows here first.

`tests/fixture_positions.py` copies those columns into `dbt/seeds/fixtures/expected_fixture_positions.csv` and adds the cells and half-widths, computed and anchored to the boxes worked by hand in the link-rules cases. It also computes the hand-written columns as a cross-check: `tests/test_fixture_positions.py` fails where the two disagree, and a disagreement is settled by reading the contract, not by editing the hand row to match. `tests/test_dbt_fixture_build.py` loads both fixtures into a throwaway schema and runs dbt on them.
