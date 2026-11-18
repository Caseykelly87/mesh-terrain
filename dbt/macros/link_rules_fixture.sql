{#
  Comparisons of the link models with the link-rules conformance fixture's
  hand-worked results (tests/fixtures/link_rules/README.md, Loading and
  comparing), for the singular tests that run in the fixture build. The
  expected rows are the seeds tests/link_rules_expected.py copies from the
  link-rules cases and the staging cases s05 onwards. cases is a list of case
  prefixes such as ['c16', 'c18'], or none for every case in the seeds. Tolerances are the README's: distances 1e-4 m, half-widths and
  position_uncertainty_m 1e-6 m, everything else exact.
#}

{% macro case_filter(column, cases) -%}
    {%- if cases is none -%}
        split_part({{ column }}, '-', 1) in (
            select split_part(delivery_key, '-', 1) from {{ ref('expected_link_receipts') }}
        )
    {%- else -%}
        ({% for case in cases %}{{ column }} like '{{ case }}-%'{{ ' or ' if not loop.last }}{% endfor %})
    {%- endif -%}
{%- endmacro %}

{# The link-rules fixture's own cases, for the counts its README states across that fixture. #}
{% macro link_rules_cases_only(column) -%}
    {{ column }} like 'c__-%'
{%- endmacro %}

{% macro boxes_differ(actual, expected, ends=['relayer', 'gateway']) -%}
    (
        {% for end in ends -%}
        {% for column in ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits'] -%}
        {{ actual }}.{{ end }}_{{ column }} is distinct from {{ expected }}.{{ end }}_{{ column }} or
        {% endfor -%}
        {% for column in ['half_width_ns_m', 'half_width_ew_m'] -%}
        coalesce(abs({{ actual }}.{{ end }}_{{ column }} - {{ expected }}.{{ end }}_{{ column }}) > 1e-6, true) or
        {% endfor -%}
        {% endfor -%}
        false
    )
{%- endmacro %}

{# Each expected receipt whose outcome, rule, relayer, link_source, boxes or distance differ, and each counted receipt the fixture does not count. #}
{% macro link_rules_receipt_mismatches(cases=none) -%}
    with expected as (
        select *
        from {{ ref('expected_link_receipts') }}
        where {{ case_filter('delivery_key', cases) }}
    ),

    kept_copies as (
        select delivery_key
        from {{ ref('stg_packet') }}
    ),

    actual as (
        select
            expected.delivery_key,
            kept_copies.delivery_key is not null as kept_copy,
            resolution.drop_rule as resolution_rule,
            resolution.relayer_pseudonym,
            resolution.link_source,
            linked.drop_rule,
            linked.distance_m,
            {% for end in ['relayer', 'gateway'] -%}
            {% for column in ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits', 'half_width_ns_m', 'half_width_ew_m'] -%}
            linked.{{ end }}_{{ column }}{{ ',' if not (loop.last and end == 'gateway') }}
            {% endfor -%}
            {% endfor %}
        from expected
        left join kept_copies
            on kept_copies.delivery_key = expected.delivery_key
        left join {{ ref('int_rx_resolution') }} as resolution
            on resolution.delivery_key = expected.delivery_key
        left join {{ ref('int_link_receipt') }} as linked
            on linked.delivery_key = expected.delivery_key
    ),

    decided as (
        select
            actual.*,
            case
                when not actual.kept_copy then 'ing_06'
                else coalesce(actual.resolution_rule, actual.drop_rule)
            end as rule
        from actual
    )

    select
        expected.delivery_key as item,
        'expected receipt differs' as mismatch
    from expected
    inner join decided
        on decided.delivery_key = expected.delivery_key
    where
        decided.rule is distinct from expected.rule
        or (decided.rule is null) <> (expected.outcome = 'counted')
        or decided.relayer_pseudonym is distinct from expected.relayer
        or decided.link_source is distinct from expected.link_source
        or (
            expected.relayer_south_i is not null
            and (
                {{ boxes_differ('decided', 'expected') }}
                or coalesce(abs(decided.distance_m - expected.distance_m) > 1e-4, true)
            )
        )

    {% if cases is none %}
        union all

        select
            linked.delivery_key as item,
            'counted but not expected counted' as mismatch
        from {{ ref('int_link_receipt') }} as linked
        left join {{ ref('expected_link_receipts') }} as expected
            on
                expected.delivery_key = linked.delivery_key
                and expected.outcome = 'counted'
        where
            linked.drop_rule is null
            and {{ case_filter('linked.delivery_key', none) }}
            and expected.delivery_key is null
    {% endif %}
{%- endmacro %}

{# Each expected link-hour missing, unexpected or differing in outcome, rule, receipts, medians, distance, boxes, precise flags or position_uncertainty_m. #}
{% macro link_rules_hour_mismatches(cases=none) -%}
    with expected as (
        select *
        from {{ ref('expected_link_hours') }}
        where {{ case_filter('relayer', cases) }}
    ),

    counted_receipts as (
        select
            tx_node_hash,
            gateway_node_hash,
            hour,
            string_agg(delivery_key, ' ' order by delivery_key) as receipts
        from {{ ref('int_link_receipt') }}
        where drop_rule is null
        group by tx_node_hash, gateway_node_hash, hour
    ),

    actual as (
        select
            hours.tx_node_hash,
            hours.gateway_node_hash,
            hours.hour,
            hours.packet_count,
            hours.drop_rule,
            counted_receipts.receipts,
            windows.tx_node_hash is not null as in_window,
            windows.median_snr_db,
            windows.median_rssi_dbm,
            windows.distance_m,
            windows.position_uncertainty_m,
            {% for end in ['relayer', 'gateway'] -%}
            {% for column in ['south_i', 'north_i', 'west_i', 'east_i', 'merged', 'precision_bits', 'half_width_ns_m', 'half_width_ew_m', 'precise_source'] -%}
            windows.{{ end }}_{{ column }},
            {% endfor -%}
            {% endfor -%}
            true as present
        from {{ ref('int_link_hour') }} as hours
        inner join counted_receipts
            on
                counted_receipts.tx_node_hash = hours.tx_node_hash
                and counted_receipts.gateway_node_hash = hours.gateway_node_hash
                and counted_receipts.hour = hours.hour
        left join {{ ref('int_link_window') }} as windows
            on
                windows.tx_node_hash = hours.tx_node_hash
                and windows.gateway_node_hash = hours.gateway_node_hash
                and windows.hour = hours.hour
        where {{ case_filter('hours.tx_node_hash', cases) }}
    )

    select
        concat_ws(
            ' ',
            coalesce(expected.relayer, actual.tx_node_hash),
            coalesce(expected.gateway, actual.gateway_node_hash),
            coalesce(expected.hour, actual.hour)
        ) as item,
        'link-hour differs' as mismatch
    from expected
    full outer join actual
        on
            actual.tx_node_hash = expected.relayer
            and actual.gateway_node_hash = expected.gateway
            and actual.hour = expected.hour
    where
        expected.relayer is null
        or actual.present is null
        or actual.drop_rule is distinct from expected.rule
        or actual.in_window <> (expected.outcome = 'kept')
        or actual.packet_count <> expected.packets
        or actual.receipts <> expected.receipts
        or (
            expected.outcome = 'kept'
            and (
                actual.median_snr_db is distinct from expected.median_snr_db
                or actual.median_rssi_dbm is distinct from expected.median_rssi_dbm
                or coalesce(abs(actual.distance_m - expected.distance_m) > 1e-4, true)
                or {{ boxes_differ('actual', 'expected') }}
                or actual.relayer_precise_source is distinct from expected.relayer_precise_source
                or actual.gateway_precise_source is distinct from expected.gateway_precise_source
                or coalesce(
                    abs(actual.position_uncertainty_m - expected.position_uncertainty_m) > 1e-6,
                    true
                )
            )
        )
{%- endmacro %}


{# Receipt and link-hour mismatches together, for a named test that covers a few cases. #}
{% macro link_rules_mismatches(cases=none) -%}
    select *
    from ({{ link_rules_receipt_mismatches(cases) }}) as receipts
    union all
    select *
    from ({{ link_rules_hour_mismatches(cases) }}) as link_hours
{%- endmacro %}
