{#
  The link rules' constants and tests, as SQL expressions. Edges are bigint in
  1e-7 degrees and half-open, [south_i, north_i) and [west_i, east_i) (lnk_02).
  Each macro takes column names or expressions as text.
#}

{# STATIONARY_FLOOR_M: two positions within 40 m of each other, inclusive, are the same place (lnk_02; Casey, issue #55, D4). #}
{% macro stationary_floor_m() -%}
    40.0
{%- endmacro %}

{# The 30-minute fallback and the 7-day stationarity window, both inclusive (lnk_02, Q5). #}
{% macro fallback_window() -%}
    interval '30 minutes'
{%- endmacro %}

{% macro stationary_window() -%}
    interval '7 days'
{%- endmacro %}

{# lnk_01's look-back for candidates and NeighborInfo reports, both edges included (Q2). #}
{% macro candidate_window() -%}
    interval '24 hours'
{%- endmacro %}

{# The clock hour of a receipt in UTC, half-open, so a receipt on the hour belongs to the later hour (lnk_04). #}
{% macro link_hour(received_at) -%}
    date_trunc('hour', {{ received_at }}, 'UTC')
{%- endmacro %}

{#
  Two boxes overlap when, on both axes, the larger low is less than the
  smaller high; boxes that only touch do not overlap (lnk_02). a and b are
  column prefixes, so boxes_overlap('p.', 'q.') reads p.south_i and q.south_i.
#}
{% macro boxes_overlap(a, b) -%}
    (
        greatest({{ a }}south_i, {{ b }}south_i) < least({{ a }}north_i, {{ b }}north_i)
        and greatest({{ a }}west_i, {{ b }}west_i) < least({{ a }}east_i, {{ b }}east_i)
    )
{%- endmacro %}

{# Whether a box holds a stored value on both axes, low <= value < high (lnk_02). #}
{% macro box_holds(box, latitude_i, longitude_i) -%}
    (
        {{ box }}south_i <= {{ latitude_i }}
        and {{ latitude_i }} < {{ box }}north_i
        and {{ box }}west_i <= {{ longitude_i }}
        and {{ longitude_i }} < {{ box }}east_i
    )
{%- endmacro %}

{# A stored value in 1e-7 degrees as a WGS84 geography point. #}
{% macro stored_point(latitude_i, longitude_i) -%}
    st_setsrid(
        st_point(({{ longitude_i }} * 1e-7)::double precision, ({{ latitude_i }} * 1e-7)::double precision),
        4326
    )
{%- endmacro %}

{# A box's centre, the midpoint of its edges, as a SRID 4326 point (lnk_02). #}
{% macro box_centre(box) -%}
    st_setsrid(
        st_point(
            (({{ box }}west_i + {{ box }}east_i) * 0.5e-7)::double precision,
            (({{ box }}south_i + {{ box }}north_i) * 0.5e-7)::double precision
        ),
        4326
    )
{%- endmacro %}

{#
  Whether a position's cell holds a finer position the rule reads in the
  window from window_start to window_end, both included, so the box sets the
  cell aside (lnk_02, D3'). finer_received_at is the position's array of the
  times its node sent the finer positions its cell holds (int_usable_position).
#}
{% macro holds_finer_between(finer_received_at, window_start, window_end) -%}
    exists (
        select 1
        from unnest({{ finer_received_at }}) as finer (received_at)
        where finer.received_at between {{ window_start }} and {{ window_end }}
    )
{%- endmacro %}

{#
  One box per group from the cells it covers: the smallest latitude-longitude
  rectangle around them (lnk_02, D3'). merged is true when it covers two or
  more cells. precision_bits is the most among them. A single cell keeps
  pos_01's half-widths; a merged rectangle takes them from its own extent by
  pos_01's formula at its midpoint's latitude (PLAN, Data model).
  precise_source is true when every covered cell has any_precise (D7).
#}
{% macro box_from_cells(covered, group_columns) -%}
    select
        {% for column in group_columns -%}
        {{ column }},
        {% endfor -%}
        min(south_i) as south_i,
        max(north_i) as north_i,
        min(west_i) as west_i,
        max(east_i) as east_i,
        count(*) > 1 as merged,
        max(precision_bits) as precision_bits,
        case
            when count(*) = 1 then max(half_width_ns_m)
            else {{ half_width_ns_m('(max(north_i) - min(south_i))', '((min(south_i) + max(north_i)) / 2.0)') }}
        end as half_width_ns_m,
        case
            when count(*) = 1 then max(half_width_ew_m)
            else {{ half_width_ew_m('(max(east_i) - min(west_i))', '((min(south_i) + max(north_i)) / 2.0)') }}
        end as half_width_ew_m,
        bool_and(any_precise) as precise_source
    from {{ covered }}
    group by {{ group_columns | join(', ') }}
{%- endmacro %}
