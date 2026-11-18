{{ config(enabled=fixture_build()) }}

-- ing_06 on the synthetic fixtures: of several copies of one packet at one
-- gateway, stg_packet keeps the first by received_at, within a snapshot and
-- across snapshots (issue #55 case 35). The expected rows are the hand-worked
-- ones: link-rules case c40 (two copies under two broker roots, c40-m006
-- dropped) and staging case s01 (one packet in two snapshots, where the later
-- snapshot's copy wins when it is earlier, a packet heard at two gateways
-- kept at each, and two deliveries without packet_id kept apart, each
-- collapsing only with its own copy in the other snapshot). Returns each row missing from stg_packet or
-- kept there unexpectedly, for the senders of those two cases.

with expected (snapshot_id, delivery_key) as (
    values
        ('link-rules-fixture', 'c40-m001'),
        ('link-rules-fixture', 'c40-m002'),
        ('link-rules-fixture', 'c40-m003'),
        ('link-rules-fixture', 'c40-m004'),
        ('link-rules-fixture', 'c40-m005'),
        ('staging-fixture-b', 's01-m001'),
        ('staging-fixture-a', 's01-m002'),
        ('staging-fixture-b', 's01-m004'),
        ('staging-fixture-a', 's01-m005'),
        ('staging-fixture-a', 's01-m006'),
        ('staging-fixture-a', 's01-m007'),
        ('staging-fixture-a', 's01-m008'),
        ('staging-fixture-a', 's01-m009')
),

kept as (
    select
        snapshot_id,
        delivery_key
    from {{ ref('stg_packet') }}
    where origin_pseudonym in ('c40-g', 'c40-r', 's01-g', 's01-r')
)

select
    coalesce(expected.snapshot_id, kept.snapshot_id) as snapshot_id,
    coalesce(expected.delivery_key, kept.delivery_key) as delivery_key,
    kept.delivery_key is null as missing_from_stg_packet
from expected
full outer join kept
    on
        kept.snapshot_id = expected.snapshot_id
        and kept.delivery_key = expected.delivery_key
where
    expected.delivery_key is null
    or kept.delivery_key is null
