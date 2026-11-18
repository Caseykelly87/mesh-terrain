-- The database role the tests and the development dbt builds connect as
-- (Casey, issue #55 comment 6961809084, item 10, and PR #64 comment
-- 6968763718, item 5). It has no privilege at all on raw, raw_dev_wh or
-- raw_dev_ta, so any write there fails whatever SQL a test sends, and it owns
-- the schemas it must build in: the throwaway test_load_ and test_fixture_
-- schemas it creates, and the development build schemas.
--
-- `make test-role` pipes this file to psql inside the warehouse container as
-- the owner role, on every `make up` and `make check`, so it reaches an
-- existing volume as well as a fresh one; it is not in initdb/, which runs
-- only on a fresh volume. Every statement converges, so a second run changes
-- nothing. The name and password come from the container environment through
-- \getenv, never from the command line, so ps and the shell history never
-- show the password.

\set ON_ERROR_STOP on
-- The notices say only that an existing schema was kept.
set client_min_messages to warning;
\getenv test_role WAREHOUSE_TEST_USER
\getenv test_password WAREHOUSE_TEST_PASSWORD

begin;

-- The DO blocks below cannot read psql variables, so the name goes through a
-- setting that lasts until the commit.
select set_config('mesh_terrain.test_role', :'test_role', true) as test_role_setting \gset

-- A test role equal to the owner, or one that is already a superuser, would
-- turn every refusal below into a no-op while the tests still pass.
do $$
declare
    test_role text := current_setting('mesh_terrain.test_role');
begin
    if test_role = '' or test_role = current_user then
        raise exception 'WAREHOUSE_TEST_USER must name a role other than the owner %', current_user;
    end if;
    if exists (select from pg_roles where rolname = test_role and rolsuper) then
        raise exception 'role % is a superuser; the test role never is', test_role;
    end if;
end
$$;

select format('create role %I', :'test_role')
where not exists (select from pg_roles where rolname = :'test_role')
\gexec

select format(
    'alter role %I with login nosuperuser nocreatedb nocreaterole noreplication nobypassrls password %L',
    :'test_role',
    :'test_password'
)
\gexec

-- A membership would hand the role its parent's privileges, such as
-- pg_write_all_data or the owner's, so the role is a member of nothing.
select format('revoke %I from %I cascade', parent.rolname, :'test_role')
from pg_auth_members as membership
join pg_roles as parent on parent.oid = membership.roleid
where membership.member = (select oid from pg_roles where rolname = :'test_role')
\gexec

-- CREATE on the database lets the role make its throwaway schemas and lets
-- dbt's on-run-start hook run `create schema if not exists`, which checks
-- this privilege even when the schema exists.
select format('grant connect, create on database %I to %I', current_database(), :'test_role')
\gexec

-- The raw schemas are created here by the owner, so the dbt hook running as
-- the test role never creates one and so never owns one: a schema's owner can
-- create tables in it and drop any table in it. raw comes from initdb.
create schema if not exists raw_dev_wh;
create schema if not exists raw_dev_ta;

do $$
declare
    test_role text := current_setting('mesh_terrain.test_role');
    raw_schemas text[] := array['raw', 'raw_dev_wh', 'raw_dev_ta'];
    build_schemas text[] := array[
        'staging_dev_wh', 'core_dev_wh', 'analytics_dev_wh',
        'staging_dev_ta', 'core_dev_ta', 'analytics_dev_ta'
    ];
    test_oid oid := (select oid from pg_roles where rolname = current_setting('mesh_terrain.test_role'));
    schema_name text;
    relation record;
begin
    foreach schema_name in array raw_schemas loop
        execute format('alter schema %I owner to %I', schema_name, current_user);
        -- Anything the test role owns in a raw schema, from a run before
        -- this file existed, goes to the owner, since owning a table is
        -- every privilege on it.
        for relation in
            select c.relname, c.relkind
            from pg_class as c
            join pg_namespace as n on n.oid = c.relnamespace
            where n.nspname = schema_name
                and c.relowner = test_oid
                and c.relkind in ('r', 'p', 'v', 'm', 'f')
        loop
            execute format(
                'alter %s %I.%I owner to %I',
                case relation.relkind when 'v' then 'view' when 'm' then 'materialized view'
                    when 'f' then 'foreign table' else 'table' end,
                schema_name, relation.relname, current_user
            );
        end loop;
        -- Only a grant the role holds is revoked: revoking from an object
        -- with the default privileges would rewrite its ACL to an explicit
        -- copy of the same thing, a needless change to the real raw schema.
        -- REVOKE ALL on a table also revokes every column grant on it.
        if exists (
            select from pg_namespace as n, aclexplode(n.nspacl) as acl
            where n.nspname = schema_name and acl.grantee = test_oid
        ) then
            execute format('revoke all on schema %I from %I', schema_name, test_role);
        end if;
        for relation in
            select distinct c.relname, c.relkind
            from pg_class as c
            join pg_namespace as n on n.oid = c.relnamespace
            left join pg_attribute as a on a.attrelid = c.oid and a.attacl is not null
            where n.nspname = schema_name
                and c.relkind in ('r', 'p', 'v', 'm', 'f', 'S')
                and (
                    exists (select from aclexplode(c.relacl) as acl where acl.grantee = test_oid)
                    or exists (select from aclexplode(a.attacl) as acl where acl.grantee = test_oid)
                )
        loop
            execute format(
                'revoke all on %s %I.%I from %I',
                case relation.relkind when 'S' then 'sequence' else 'table' end,
                schema_name, relation.relname, test_role
            );
        end loop;
        for relation in
            select p.oid::regprocedure as signature
            from pg_proc as p
            join pg_namespace as n on n.oid = p.pronamespace
            where n.nspname = schema_name
                and exists (select from aclexplode(p.proacl) as acl where acl.grantee = test_oid)
        loop
            execute format('revoke all on routine %s from %I', relation.signature, test_role);
        end loop;
    end loop;

    -- dbt replaces a view or table by dropping or renaming it, which only its
    -- owner may do, so the development build schemas and what is in them
    -- belong to the test role. Builds run as the owner before this role
    -- existed left them owned by the owner; a later build as the owner, such
    -- as Dagster's dbt asset, does the same, and the next make test-role
    -- hands them back.
    foreach schema_name in array build_schemas loop
        execute format('create schema if not exists %I authorization %I', schema_name, test_role);
        execute format('alter schema %I owner to %I', schema_name, test_role);
        for relation in
            select c.relname, c.relkind
            from pg_class as c
            join pg_namespace as n on n.oid = c.relnamespace
            where n.nspname = schema_name
                and c.relowner <> test_oid
                and c.relkind in ('r', 'p', 'v', 'm', 'f')
        loop
            execute format(
                'alter %s %I.%I owner to %I',
                case relation.relkind when 'v' then 'view' when 'm' then 'materialized view'
                    when 'f' then 'foreign table' else 'table' end,
                schema_name, relation.relname, test_role
            );
        end loop;
    end loop;
end
$$;

commit;
