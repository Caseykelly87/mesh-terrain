# Staging cases

warehouse-eng's synthetic cases for what the link-rules fixture (`tests/fixtures/link_rules/`) cannot hold, because that fixture is one snapshot and the Python side reads one:

| Case | What it tests | Rules |
| --- | --- | --- |
| `s01_one_packet_in_two_snapshots` | one packet in two snapshots: `stg_packet` keeps the first copy by `received_at`, and the later snapshot's copy wins when it is earlier (issue #55 case 35); a packet without `packet_id`; core keeps one row per position delivery | ing_01, ing_06, pos_02 |
| `s02_msa_edge_32_bit_stored_value` | two 32-bit positions whose stored value and cell centre lie on opposite sides of the MSA edge: core reads the stored value (Casey, PR #66, Q3) | pos_02 |
| `s03_precisions_the_link_rules_fixture_does_not_use` | a 12-bit coarse cell, a position at 0 bits, a precise MapReport | pos_01, pos_02, pos_04 |

The files use the link-rules case format (`case_id`, `title`, `rules`, `reasoning`, `messages`) with one addition: every message names its own `snapshot_id`, `staging-fixture-a` or `staging-fixture-b`. A message absent from a column is null, and raw's other not-null columns take the link-rules README's defaults. Pseudonyms start with the case number (`s01-r`), so they never meet a link-rules node.

The expected values are in two places. `assert_ing_06_first_copy_kept` lists the `stg_packet` rows of s01 and c40 by hand. `tests/fixture_positions.py` computes the expected staging and core values of every position in both fixtures into `dbt/seeds/fixtures/expected_fixture_positions.csv`, and `tests/test_fixture_positions.py` checks that file is current and agrees with the boxes worked by hand in the link-rules cases. `tests/test_dbt_fixture_build.py` loads both fixtures into a throwaway schema and runs dbt on them.
