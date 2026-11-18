{{ config(enabled=reads_raw()) }}

-- lnk_06 in staging: an SNR that is not a number lands as missing, so no
-- stg_packet row holds NaN. Returns each row that does.

select delivery_key
from {{ ref('stg_packet') }}
where rx_snr = 'NaN'::real
