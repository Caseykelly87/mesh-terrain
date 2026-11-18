{{ config(enabled=reads_raw()) }}

-- lnk_06 in staging, on any data: no stg_packet reading lies outside the range
-- the radio chips can report (Casey, PR #72: SNR -32 to 31.75 dB, RSSI -189
-- to +98 dBm, inclusive), and no SNR is NaN. Returns each row that breaks it.

select delivery_key
from {{ ref('stg_packet') }}
where
    rx_snr = 'NaN'::real
    or rx_snr not between -32 and 31.75
    or rx_rssi not between -189 and 98
