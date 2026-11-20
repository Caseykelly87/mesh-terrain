-- Sets the owner role's password to WAREHOUSE_PASSWORD from .env. Postgres
-- reads POSTGRES_PASSWORD only when it creates a fresh volume, so a new value
-- in .env does not reach an existing warehouse without this.
--
-- `make rotate-owner-password` pipes this file to psql inside the warehouse
-- container over the local socket, which the image trusts without a password
-- (the healthcheck logs in the same way), so the rotation does not depend on
-- the old password and cannot lock the owner out. The value comes from the
-- container environment through \getenv, never from the command line. The
-- one statement that holds it outside a DO block is the set_config that hands
-- it over; the DO block raises only a fixed message, psql shows no CONTEXT,
-- and the transaction logs no statement text, CONTEXT, slow or sampled
-- statement. While that set_config statement runs and until the next one
-- starts, pg_stat_activity.query holds the value, visible to superusers and
-- to members of pg_read_all_stats or pg_monitor; no log keeps it.
--
-- ALTER ROLE is one statement in one transaction: a failure leaves the old
-- password in place, and a run that never reaches the commit changes nothing.

\set ON_ERROR_STOP on
\set VERBOSITY terse
\set SHOW_CONTEXT never
set client_min_messages to warning;
\getenv owner_password WAREHOUSE_PASSWORD

-- The server decides when a transaction starts whether to log all of its
-- statements, so sampling is turned off for this session first.
set log_transaction_sample_rate to 0;

begin;

-- The server logs a failed statement's text, its CONTEXT, a slow statement
-- and a sampled one, and the statements here hold the password, so none of
-- these is logged in this transaction. Each setting needs a superuser, which
-- the owner is, and ends with the transaction.
set local log_statement to 'none';
set local log_min_error_statement to panic;
set local log_min_duration_statement to -1;
set local log_min_duration_sample to -1;
set local log_error_verbosity to terse;

-- The DO block cannot read psql variables, so the value goes through a
-- setting that lasts until the commit.
select set_config('mesh_terrain.owner_password', :'owner_password', true) as owner_setting \gset

do $$
begin
    if current_setting('mesh_terrain.owner_password') = '' then
        raise exception 'WAREHOUSE_PASSWORD is empty; nothing was changed';
    end if;
    execute format(
        'alter role %I with password %L',
        current_user, current_setting('mesh_terrain.owner_password')
    );
exception
    when raise_exception then
        raise;
    when others then
        raise exception 'make rotate-owner-password stopped, password unchanged: SQLSTATE %', sqlstate;
end
$$;

commit;
