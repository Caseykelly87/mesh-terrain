# Status

This file says where the project actually stands. It is updated by the agent that finishes each unit of work and checked by the reviewer on every PR. It describes the present; history lives in git and `docs/adr/`.

## Current phase and last unit

Phase 0, not started. No units completed.

## Deviations from the plan still in effect

None. Note for every agent: the plan was revised on 2026-10-21 after verifying two public-broker behaviours (hop_limit rewritten to 0; positions limited to 10 to 16 bits). The link is relayer to gateway, the 1 m DEM is out of scope, and ADR 0003 is pending the Phase 1 spike. Read the section "Two constraints of the public broker" in docs/PLAN.md before any Phase 1 or Phase 2 work.

## Known issues and skipped steps

None.

## Environment and configuration notes

- Git identity: set `user.name` and `user.email` to Casey's before the first commit.
- Automatic co-author trailers in the coding tool are off.
- No `.env` exists yet; `platform-eng` creates `.env.example` in Phase 0.

## Interfaces agreed between agents, not yet in code

None.

## Next three units of work

1. platform-eng: Phase 0 foundation (uv, Makefile, Compose, dbt, Dagster, CI).
2. terrain-rf-eng: ITM spike and draft of `docs/adr/0002-propagation-model.md`.
3. ingest-eng: Phase 1 spike (48-hour capture, relay_node and precision measurement, regional broker search) and draft of `docs/adr/0003-direct-link-and-position-source.md`, after Phase 0 exit criteria pass.
