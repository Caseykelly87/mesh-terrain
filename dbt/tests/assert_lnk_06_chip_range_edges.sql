{{ config(enabled=fixture_build()) }}

-- lnk_06 in staging, at both edges of the range the radio chips can report
-- (Casey, PR #72: SNR -32 to 31.75 dB, RSSI -189 to +98 dBm, inclusive): the
-- hand-worked stg_packet readings of staging case s13. Returns each row whose
-- readings differ or that is missing.

with expected (delivery_key, rx_snr, rx_rssi) as (
    values
        ('s13-m001', -32.0::real, -189),
        ('s13-m002', null::real, null::integer),
        ('s13-m003', 31.75::real, 98),
        ('s13-m004', null::real, null::integer),
        ('s13-m005', 5.0::real, null::integer),
        ('s13-m006', null::real, -90)
)

select expected.delivery_key
from expected
left join {{ ref('stg_packet') }} as packets
    on packets.delivery_key = expected.delivery_key
where
    packets.delivery_key is null
    or packets.rx_snr is distinct from expected.rx_snr
    or packets.rx_rssi is distinct from expected.rx_rssi
