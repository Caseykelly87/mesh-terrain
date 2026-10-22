# Status

This file says where the project actually stands. It is updated by the agent that finishes each unit of work and checked by the reviewer on every PR. It describes the present; history lives in git and `docs/adr/`.

## Current phase and last unit

Phase 0, in progress. Last unit completed: terrain-rf-eng ITM spike and draft ADR 0002, PR #1.

## Deviations from the plan still in effect

None. Note for every agent: the plan was revised on 2026-10-21 after verifying two public-broker behaviours (hop_limit rewritten to 0; positions limited to 10 to 16 bits). The link is relayer to gateway, the 1 m DEM is out of scope, and ADR 0003 is pending the Phase 1 spike. Read the section "Two constraints of the public broker" in docs/PLAN.md before any Phase 1 or Phase 2 work.

## Known issues and skipped steps

None.

## Environment and configuration notes

- Git identity: set `user.name` and `user.email` to Casey's before the first commit.
- Automatic co-author trailers in the coding tool are off.
- No `.env` exists yet; `platform-eng` creates `.env.example` in Phase 0.
- ITM spike images: `docker build --target itm -t itm-spike docs/adr/0002-itm-spike` then `docker run --rm itm-spike` prints the flat-path loss; `--target splat` builds the SPLAT comparison image. Both need network access to apt and to codeload.github.com.

## Interfaces agreed between agents, not yet in code

- terrain-rf-eng to platform-eng: per ADR 0002 (accepted), the Dagster image gets a build stage that compiles NTIA ITM commit `183ad95b` into `/usr/local/lib/libitm.so` (recipe in `docs/adr/0002-itm-spike/Dockerfile`) and copies only that file into the runtime stage. The 151.76 dB spike figure came from base image `python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70` and `g++ 12.2.0-14+deb12u1`. `terrain/itm.py` will load it from that path.

## Next three units of work

1. platform-eng: Phase 0 foundation (uv, Makefile, Compose, dbt, Dagster, CI).
2. ingest-eng: Phase 1 spike (48-hour capture, relay_node and precision measurement, regional broker search) and draft of `docs/adr/0003-direct-link-and-position-source.md`, after Phase 0 exit criteria pass.
3. platform-eng: add the `libitm.so` build stage to the Dagster image per ADR 0002.
