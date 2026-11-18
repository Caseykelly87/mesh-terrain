-- pos_01: position_quality and cell_width_i for every precision_bits from -2
-- to 34, against a table written out from the rule. usable is 15 to 32, coarse
-- 12 to 14, anything else unusable, negatives included; cell_width_i is
-- 2^(32 - precision_bits), and null at 0 and outside 0 to 32. Returns each
-- precision whose computed values differ.

with expected (precision_bits, position_quality, cell_width_i) as (
    values
        (-2::bigint, 'unusable', null::bigint),
        (-1, 'unusable', null),
        (0, 'unusable', null),
        (1, 'unusable', 2147483648),
        (2, 'unusable', 1073741824),
        (3, 'unusable', 536870912),
        (4, 'unusable', 268435456),
        (5, 'unusable', 134217728),
        (6, 'unusable', 67108864),
        (7, 'unusable', 33554432),
        (8, 'unusable', 16777216),
        (9, 'unusable', 8388608),
        (10, 'unusable', 4194304),
        (11, 'unusable', 2097152),
        (12, 'coarse', 1048576),
        (13, 'coarse', 524288),
        (14, 'coarse', 262144),
        (15, 'usable', 131072),
        (16, 'usable', 65536),
        (17, 'usable', 32768),
        (18, 'usable', 16384),
        (19, 'usable', 8192),
        (20, 'usable', 4096),
        (21, 'usable', 2048),
        (22, 'usable', 1024),
        (23, 'usable', 512),
        (24, 'usable', 256),
        (25, 'usable', 128),
        (26, 'usable', 64),
        (27, 'usable', 32),
        (28, 'usable', 16),
        (29, 'usable', 8),
        (30, 'usable', 4),
        (31, 'usable', 2),
        (32, 'usable', 1),
        (33, 'unusable', null),
        (34, 'unusable', null),
        (9223372036854775807, 'unusable', null)
),

computed as (
    select
        precision_bits,
        {{ position_quality('precision_bits') }} as position_quality,
        {{ cell_width_i('precision_bits') }} as cell_width_i
    from expected
)

select
    expected.precision_bits,
    expected.position_quality as expected_quality,
    computed.position_quality as computed_quality,
    expected.cell_width_i as expected_width,
    computed.cell_width_i as computed_width
from expected
inner join computed
    on computed.precision_bits = expected.precision_bits
where
    computed.position_quality is distinct from expected.position_quality
    or computed.cell_width_i is distinct from expected.cell_width_i
