{{ config(enabled=fixture_build()) }}

-- pos_02 on the synthetic fixtures: core.fct_node_position holds exactly the
-- position deliveries the seed puts in core, those whose stored value lies
-- inside the MSA (a 32-bit position's stored value, not its cell centre;
-- Casey, PR #66, Q3), one row per delivery from its first copy by
-- received_at. That covers c27 to c32 of the link-rules fixture and the
-- staging cases s01 to s03. Each kept row carries its staging values, and
-- the precise flag the hand-written rows give core, which reads seen_meshstl
-- on any copy of the delivery (s01-m009; awaiting Casey on PR #69).
-- Returns each row that is missing on either side or differs.

with expected as (
    select *
    from {{ ref('expected_fixture_positions') }}
    where in_core
),

core_rows as (
    select *
    from {{ ref('fct_node_position') }}
),

compared as (
    select
        coalesce(expected.delivery_key, core_rows.delivery_key) as delivery_key,
        expected.snapshot_id as expected_snapshot_id,
        core_rows.snapshot_id as core_snapshot_id,
        (
            core_rows.snapshot_id is distinct from expected.snapshot_id
            or core_rows.node_pseudonym is distinct from expected.node_pseudonym
            or core_rows.latitude_i is distinct from expected.latitude_i
            or core_rows.longitude_i is distinct from expected.longitude_i
            or core_rows.precision_bits is distinct from expected.precision_bits
            or core_rows.position_quality is distinct from expected.position_quality
            or core_rows.cell_width_i is distinct from expected.cell_width_i
            or core_rows.precise_source is distinct from expected.core_precise_source
            or (core_rows.half_width_ns_m is null) <> (expected.half_width_ns_m is null)
            or (core_rows.half_width_ew_m is null) <> (expected.half_width_ew_m is null)
            or abs(core_rows.half_width_ns_m - expected.half_width_ns_m) > 1e-6
            or abs(core_rows.half_width_ew_m - expected.half_width_ew_m) > 1e-6
        ) as values_differ
    from expected
    full outer join core_rows
        on core_rows.delivery_key = expected.delivery_key
)

select *
from compared
where
    expected_snapshot_id is null
    or core_snapshot_id is null
    or values_differ
