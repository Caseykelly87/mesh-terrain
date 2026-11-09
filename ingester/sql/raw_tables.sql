-- Raw tables for MQTT snapshots (ADR 0005, contract rules ing_01 to ing_07,
-- prv_01 and snp_01). Applied by the snapshot loader before each load; every
-- statement is idempotent, so it runs against an existing warehouse volume as
-- well as a fresh one. Columns match ingester/allowlist.py for schema_version 1,
-- and a test compares the two. Rows are only ever inserted (ing_07).

create table if not exists raw.snapshot (
    snapshot_id text not null,
    capture_date date not null,
    source_url text not null,
    licence text not null,
    manifest_path text not null,
    manifest_sha256 text not null,
    registered_at timestamp with time zone not null,
    primary key (snapshot_id)
);

create table if not exists raw.mqtt_message (
    snapshot_id text not null references raw.snapshot (snapshot_id),
    delivery_key text not null,
    received_at timestamp with time zone not null,
    topic text not null,
    modem_preset text,
    seen_public boolean not null,
    seen_meshstl boolean not null,
    decode_status text not null check (decode_status in ('ok', 'unknown_port', 'decrypt_failed', 'stripped', 'malformed')),
    gateway_pseudonym text,
    gateway_low_byte smallint,
    origin_pseudonym text not null,
    origin_low_byte smallint not null,
    packet_id bigint not null,
    relay_node smallint not null,
    hop_start smallint not null,
    hop_limit smallint not null,
    via_mqtt boolean not null,
    rx_time bigint not null,
    rx_snr real not null,
    rx_rssi integer not null,
    channel bigint not null,
    portnum integer,
    payload_length integer not null,
    position_source text check (position_source in ('position', 'mapreport')),
    latitude_i integer,
    longitude_i integer,
    altitude_m integer,
    precision_bits smallint,
    position_time bigint,
    location_source text,
    altitude_source text,
    hw_model text,
    role text,
    neighbour_pseudonyms text[],
    neighbour_low_bytes smallint[],
    neighbour_snrs real[],
    neighbour_last_heard bigint[],
    channel_utilization real,
    air_util_tx real,
    uptime_s bigint,
    battery_level integer,
    decoder_version text not null,
    schema_version integer not null,
    primary key (snapshot_id, delivery_key)
);

create table if not exists raw.mqtt_copy_count (
    snapshot_id text not null references raw.snapshot (snapshot_id),
    hour timestamp with time zone not null,
    topic text not null,
    gateway_pseudonym text not null,
    gateway_low_byte smallint not null,
    copy_count bigint not null,
    decoder_version text not null,
    schema_version integer not null,
    primary key (snapshot_id, hour, topic, gateway_pseudonym)
);

create table if not exists raw.mqtt_dropped_count (
    snapshot_id text not null references raw.snapshot (snapshot_id),
    hour timestamp with time zone not null,
    topic_class text not null check (topic_class in ('e', 'map', 'json', 'stat', 'other')),
    reason text not null check (reason in ('json', 'stat', 'unparsed', 'other')),
    delivery_count bigint not null,
    decoder_version text not null,
    schema_version integer not null,
    primary key (snapshot_id, hour, topic_class, reason)
);
