# Contributing and how this repository was built

## Who did what

This project was designed, specified and reviewed by Casey, the repository owner. The implementation was carried out by a team of AI coding agents, one per engineering specialty, running inside Claude Code with Casey as the human in the loop.

The agents are defined in `.claude/agents/`:

| Name | Specialty |
| --- | --- |
| `platform-eng` | build, CI, containers, infrastructure |
| `ingest-eng` | MQTT ingestion and raw landing |
| `warehouse-eng` | PostGIS, dbt models and tests |
| `terrain-rf-eng` | terrain, line of sight, diffraction, propagation models |
| `analytics-eng` | validation statistics and spatial analysis |
| `maps-eng` | exports, web map, ArcGIS deliverable |
| `reviewer` | independent review of every pull request |

Every commit is authored by Casey and carries one trailer, `Agent: <name>`, recording which specialty did the work; `git log --format='%h %(trailers:key=Agent,valueonly)'` lists it. Pull requests open with `Author: <agent>. Reviewer: reviewer.` Agent names also appear in the "proposed by" line of architecture decision records and as owners in `docs/STATUS.md`. Nothing else in the code, comments or docs identifies the author, by design: the output is held to the standard of an experienced engineer writing for a colleague, and the account of the process lives here.

## How work flowed

1. Casey wrote the plan (`docs/PLAN.md`), the data-quality contract and the style guide, and decided the stack.
2. For each unit of work the lead session wrote a brief: goal, interface, acceptance test, out of scope.
3. One specialist agent implemented it, tests first where a test was meaningful, and opened a pull request with a What, Why, How verified, Not done description.
4. The `reviewer` agent reviewed the diff cold and reported blocking, should-fix and nit findings.
5. Every run ended with a fixed-format report (outcome, deviations, skipped steps, verification, configuration for the next agent) that went into the PR and into `docs/STATUS.md`, the file that records where the project actually stands against the plan.
6. Casey read the summary and the findings, asked questions, and approved or sent it back. Every decision listed under "Decisions that stop for a human" in `CLAUDE.md` was made by Casey and recorded in `docs/adr/`. From Phase 2, routine PRs that passed CI and review merged without waiting for him; anything on the stop list waited.
7. Merges were rebase-and-merge onto a protected `main`, so the test-first commit sequence is visible in the history.

The phases, their order and their exit criteria are in `docs/PLAN.md`. Each phase ends with a git tag.

## Contributing as a person

The same rules apply. Read `CLAUDE.md` and `docs/STYLE.md`. Open a branch from `main`, keep PRs small, write the test first where it adds confidence, describe what you did not do and why, and expect a cold review.

## Licence and data

Code is MIT. Meshtastic packet data is community-contributed and carries no formal licence; node identities are pseudonymised and public outputs are aggregated to 1 km or H3 resolution 8. USGS 3DEP and NLCD are public domain. OpenStreetMap data is ODbL and is attributed in the web map and the GeoPackage metadata.
