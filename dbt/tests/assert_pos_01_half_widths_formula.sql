-- pos_01: both metre half-widths against cells worked out by hand in the
-- link-rules fixture (tests/fixtures/link_rules/cases, boxes g16, r20, the
-- 15-bit and southern 16-bit cells and a 32-bit fix), given there to six
-- decimals. Each row is the cell's width and stored latitude in 1e-7 degrees.
-- Returns each cell whose computed half-width is more than 1e-6 m off.

with expected (cell_width_i, latitude_i, half_width_ns_m, half_width_ew_m) as (
    values
        (65536::bigint, 385974272::integer, 363.750397::double precision, 285.458876::double precision),
        (65536, 380469248, 363.716165, 287.625919),
        (131072, 385941504, 727.500385, 570.943707),
        (4096, 386000896, 22.734410, 17.840521),
        (4096, 386226176, 22.734498, 17.834942),
        (1, 385999949, 0.005550, 0.004356)
),

computed as (
    select
        cell_width_i,
        latitude_i,
        half_width_ns_m as expected_ns_m,
        half_width_ew_m as expected_ew_m,
        {{ half_width_ns_m('cell_width_i', 'latitude_i') }} as computed_ns_m,
        {{ half_width_ew_m('cell_width_i', 'latitude_i') }} as computed_ew_m
    from expected
)

select *
from computed
where
    not abs(computed_ns_m - expected_ns_m) <= 1e-6
    or not abs(computed_ew_m - expected_ew_m) <= 1e-6
