{{
    config(
        enabled=reads_raw(),
        severity='warn',
        warn_if='>= 0'
    )
}}

-- lnk_06's count, reported on every run (Casey, 2026-11-14): the receipts
-- that pass lnk_01 and lnk_03 and are kept out of fct_rx_observation because
-- a reading is missing or out of range. It warns with the count every time,
-- 0 included, and never fails a build; the log shows only the count.

select delivery_key
from {{ ref('int_rx_resolution') }}
where drop_rule = 'lnk_06'
