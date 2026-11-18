{#
  The cell arithmetic of pos_01 and lnk_02, as SQL expressions. Each takes
  column names or expressions as text. Units: latitude, longitude, widths
  and edges in 1e-7 degrees (bigint), half-widths in metres.
  assert_pos_01_quality_and_width_by_precision and
  assert_pos_01_half_widths_formula test them against hand-worked values.
#}

{# usable for 15 to 32 bits, coarse for 12 to 14, unusable otherwise, null and negatives included. #}
{% macro position_quality(precision_bits) -%}
    case
        when {{ precision_bits }} between 15 and 32 then 'usable'
        when {{ precision_bits }} between 12 and 14 then 'coarse'
        else 'unusable'
    end
{%- endmacro %}

{# 2^(32 - precision_bits) as a 64-bit integer; null at 0 bits and outside 0 to 32. #}
{% macro cell_width_i(precision_bits) -%}
    case
        when {{ precision_bits }} between 1 and 32
            then (1::bigint << (32 - {{ precision_bits }})::integer)
    end
{%- endmacro %}

{#
  The low edge of a cell on one axis: the stored value minus half the width
  for 1 to 31 bits, where the firmware sends the cell centre, or the stored
  value itself at 32 bits (lnk_02). The cell is [low, low + width).
#}
{% macro cell_low_i(value_i, precision_bits, width_i) -%}
    case
        when {{ precision_bits }} = 32 then {{ value_i }}::bigint
        else {{ value_i }}::bigint - {{ width_i }} / 2
    end
{%- endmacro %}

{# WGS84 first eccentricity squared, e^2 = f(2 - f) with f = 1/298.257223563. #}
{% macro wgs84_e2() -%}
    ((1.0 / 298.257223563) * (2.0 - 1.0 / 298.257223563))::double precision
{%- endmacro %}

{# 1 - e^2 sin^2(phi), phi being the latitude in 1e-7 degrees. #}
{% macro wgs84_term(latitude_i) -%}
    (1.0 - {{ wgs84_e2() }} * power(sin(radians({{ latitude_i }} * 1e-7::double precision)), 2))
{%- endmacro %}

{# Half the width as an angle in radians: width x 0.5 x 1e-7 x pi / 180. #}
{% macro half_angle(width_i) -%}
    ({{ width_i }}::double precision * 0.5e-7::double precision * pi() / 180.0)
{%- endmacro %}

{# Half the cell's north-south extent: the half angle times M = a(1 - e^2) / term^(3/2). #}
{% macro half_width_ns_m(width_i, latitude_i) -%}
    (
        {{ half_angle(width_i) }}
        * 6378137.0 * (1.0 - {{ wgs84_e2() }})
        / power({{ wgs84_term(latitude_i) }}, 1.5)
    )
{%- endmacro %}

{# Half the cell's east-west extent: the half angle times N cos(phi), N = a / term^(1/2). #}
{% macro half_width_ew_m(width_i, latitude_i) -%}
    (
        {{ half_angle(width_i) }}
        * 6378137.0 / sqrt({{ wgs84_term(latitude_i) }})
        * cos(radians({{ latitude_i }} * 1e-7::double precision))
    )
{%- endmacro %}

{#
  pos_04's precise flag for one position that carries coordinates: carried by
  the MeshSTL broker, and either 20 bits or more, or 0 bits, Position or
  MapReport alike. Firmware older than the precision field sends 0 bits with
  full-precision coordinates, and the rule is about privacy, not the message
  type (Casey, issue #55 comment 6989099568, item 2; PR #71's pos_04: "or a position with precision_bits 0 that carries coordinates, Position or MapReport, is flagged precise_source = true exactly when seen_meshstl is true on its delivery").
  False, never null, for a position without precision_bits.
#}
{% macro precise_flag(precision_bits, seen_meshstl) -%}
    coalesce(
        {{ seen_meshstl }}
        and ({{ precision_bits }} >= 20 or {{ precision_bits }} = 0),
        false
    )
{%- endmacro %}
