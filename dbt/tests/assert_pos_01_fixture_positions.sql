{{ config(enabled=fixture_build()) }}

-- pos_01 and pos_03 on the synthetic fixtures: every raw position row has
-- exactly one stg_position row, with the cell, quality, half-widths, 0, 0
-- mapping and precise flag the seed expects (tests/fixture_positions.py
-- computes the seed apart from this SQL). Cell edges are compared through
-- cell_low_i, the lnk_02 edge the link models will read. Half-widths may
-- differ by 1e-6 m, the fixture's tolerance. Returns each row that is
-- missing on either side or differs.

with expected as (
    select *
    from {{ ref('expected_fixture_positions') }}
),

staged as (
    select
        snapshot_id,
        delivery_key,
        node_pseudonym,
        position_source,
        position_zero_mapped,
        latitude_i,
        longitude_i,
        precision_bits,
        position_quality,
        cell_width_i,
        {{ cell_low_i('latitude_i', 'precision_bits', 'cell_width_i') }} as south_i,
        {{ cell_low_i('longitude_i', 'precision_bits', 'cell_width_i') }} as west_i,
        half_width_ns_m,
        half_width_ew_m,
        precise_source
    from {{ ref('stg_position') }}
),

compared as (
    select
        coalesce(expected.snapshot_id, staged.snapshot_id) as snapshot_id,
        coalesce(expected.delivery_key, staged.delivery_key) as delivery_key,
        expected.delivery_key is null as missing_from_seed,
        staged.delivery_key is null as missing_from_staging,
        (
            staged.node_pseudonym is distinct from expected.node_pseudonym
            or staged.position_source is distinct from expected.position_source
            or staged.position_zero_mapped is distinct from expected.position_zero_mapped
            or staged.latitude_i is distinct from expected.latitude_i
            or staged.longitude_i is distinct from expected.longitude_i
            or staged.precision_bits is distinct from expected.precision_bits
            or staged.position_quality is distinct from expected.position_quality
            or staged.cell_width_i is distinct from expected.cell_width_i
            or staged.south_i is distinct from expected.south_i
            or staged.west_i is distinct from expected.west_i
            or staged.south_i + staged.cell_width_i is distinct from expected.north_i
            or staged.west_i + staged.cell_width_i is distinct from expected.east_i
            or staged.precise_source is distinct from expected.precise_source
            or (staged.half_width_ns_m is null) <> (expected.half_width_ns_m is null)
            or (staged.half_width_ew_m is null) <> (expected.half_width_ew_m is null)
            or abs(staged.half_width_ns_m - expected.half_width_ns_m) > 1e-6
            or abs(staged.half_width_ew_m - expected.half_width_ew_m) > 1e-6
        ) as values_differ
    from expected
    full outer join staged
        on
            staged.snapshot_id = expected.snapshot_id
            and staged.delivery_key = expected.delivery_key
)

select *
from compared
where
    missing_from_seed
    or missing_from_staging
    or values_differ
