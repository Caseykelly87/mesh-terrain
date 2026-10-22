# mesh-terrain: project instructions

This file is read by every agent and every session working in this repository. It is the single source of truth for how we work. If a subagent file and this file disagree, this file wins.

## What this project is

A streaming-to-batch data pipeline that ingests the public Meshtastic MQTT feed, models it in PostGIS with dbt and Dagster, derives terrain features from USGS 3DEP elevation data, and tests how well a standard radio propagation model explains observed LoRa link quality across the St. Louis metro. The full plan, data model, phases and exit criteria are in `docs/PLAN.md`. Read it before starting any phase.

The project owner is Casey. Casey reviews every pull request, makes every decision listed under "Decisions that stop for a human", and is learning GIS through this build. Explain decisions in plain language when asking for one.

## Team

Work is split by specialty. Each specialty is a subagent defined in `.claude/agents/`. The lead session (whoever is talking to Casey) plans, delegates, and merges; it does not write production code itself except for one-line fixes.

| Agent | Owns | Phases |
| --- | --- | --- |
| `platform-eng` | repo layout, uv, Docker Compose, CI, Terraform, secrets | 0, 6 |
| `ingest-eng` | MQTT subscriber service, Parquet landing, raw loader, protobuf decoding | 1 |
| `warehouse-eng` | PostGIS schema, dbt models, dbt tests, Dagster dbt integration | 2 |
| `terrain-rf-eng` | DEM and land-cover assets, terrain profiles, LOS, Fresnel, diffraction, ITM | 3 |
| `analytics-eng` | validation statistics, mixed model, kriging, H3 marts, write-up numbers | 4 |
| `maps-eng` | exports, PMTiles, MapLibre site, ArcGIS Pro script, README figures | 5 |
| `reviewer` | reads every PR cold; never writes production code | all |

Delegate a unit of work to exactly one specialist. When work spans two specialties, split it at the interface (a schema, a file format, a function signature) and delegate each side separately. Run independent units in parallel.

## Workflow for every unit of work

1. The lead writes a short task brief: goal, interface, acceptance test, what is out of scope.
2. The specialist writes the failing test first where a test is logical (see "Testing policy"), then the implementation, then runs the full local check (`make check`).
3. The specialist opens a branch and a PR with a description a human teammate would write: what changed, why, how it was verified, what was deliberately left out.
4. The `reviewer` agent reviews the PR without reading the specialist's reasoning. It reports findings as a list: blocking, should-fix, nit.
5. The lead summarizes for Casey in five lines or fewer and waits for approval before merging anything that touches a "stop for a human" item.
6. Merge to `main` with a conventional commit. Tag at the end of each phase.

Never skip the reviewer. Never merge with failing CI. Never force-push `main`.

## File ownership

- An agent edits only the paths it owns (see the team table and its own agent file). Touching another agent's path means stopping and asking the lead to co-assign.
- `CLAUDE.md`, `docs/STYLE.md`, `docs/PLAN.md` and `docs/data-quality-contract.md` are lead-only; edits to them go through Casey.
- `pyproject.toml`, `uv.lock`, `docker-compose.yml` and `.github/workflows/` belong to `platform-eng`; other agents request changes in their PR description.
- Schema changes (dbt model grain, PostGIS DDL, dbt snapshots) are sequential. Never run two schema-changing units of work in parallel.
- Shared code under `pipeline/common/` is assigned per task by the lead.

## Choosing the smallest tool

- A one-line fix, a config value, a rename: the lead does it directly.
- A contained task inside one specialty (write the tests for module X, add YAML descriptions): the specialist spawns a sub-agent; sub-agents inherit the parent's scope and cannot cross it.
- A scoped feature inside one specialty: the specialist.
- Work that crosses two specialties: split at the interface and run both specialists; the PR of each names the other and records what was agreed about the interface.
- Genuinely parallel work on disjoint paths: separate worktrees. No worktrees for two units that both touch build files or schema files; otherwise allowed.

## TDD cycle and commit timing

Where the testing policy below says a test is warranted:

1. Write the failing test. Run it. Confirm it fails for the right reason.
2. Write the minimum code to pass. Run it. Confirm it passes.
3. Refactor. Run the full suite.
4. Run `make check`.

Commit after step 1 (`test:`), after step 2 (`feat:` or `fix:`) and after step 3 (`refactor:`) on the feature branch. Small, related commits; never bundle unrelated changes; never commit a broken suite to `main`.

## Branches and quality gates

- `main` is always releasable. Work happens on `feat/<short-name>`, `fix/<short-name>`, `chore/<short-name>` branches off `main`. No long-lived `develop` branch; the project is small enough that PRs to `main` are the integration point.
- Before any commit: tests pass, `ruff` reports nothing, `mypy` passes on the strict packages, no secrets or `.env` with real values staged, no generated files that belong in `.gitignore`.
- Before any PR: `make check` passes locally, the description has all four sections, and the branch is rebased on `main`.

## Running a brief: continue, skip, or stop

A brief usually has several steps. An agent works through all of them and does not stop at the first problem unless the problem is one of the stop conditions below.

Classify every problem you hit:

- Non-blocking (a lint warning, a flaky test you can rerun, a missing docstring): fix it if it is in scope, otherwise note it in the report and continue.
- Blocks this step only (a download that times out, a fixture that does not exist yet, a tool that will not install): skip the step, continue with every step that does not depend on it, and report the skip with the error text and what you tried. Do not invent a workaround that changes the design.
- Stop and report immediately when any of these is true:
  - Every remaining step depends on the failed one.
  - Continuing would require guessing an interface another agent owns (a column name, a function signature, a file format).
  - The fix would touch a "stop for a human" item, spend money, delete data, or publish anything.
  - You discovered the brief is wrong (the plan assumes something that is not true in the repo or the data).
  - You would have to change a rule in the data-quality contract or the link-hour grain.

Never substitute a different approach silently. A deviation from the brief is allowed when it is the only way to keep moving and it does not hit a stop condition; it is always listed under Deviations in the report, with the reason.

## The run report

Every brief ends with a report, whether the run succeeded, partly succeeded or stopped. The agent returns it to the lead, pastes it into the PR description under the four standard sections, and updates `docs/STATUS.md` (see below). The format is fixed so nothing is left out:

```
Run report: <agent> / <brief title> / <date>
Outcome: complete | partial | stopped
Done: what was built, one line per item, with the test or check that proves it
Deviations from brief: what was done differently and why; "none" if none
Skipped or blocked: each step not done, the error text, what was tried
Verification: output of make check (pass or fail, counts), any manual check performed
Files touched: paths created, changed, deleted
Configuration the next agent needs: env vars, ports, paths, fixture names, table names, anything that is not obvious from the code
Open questions for Casey: decisions waiting on him, or "none"
Suggested next unit: one line
```

A report that says "done" without a verification line is incomplete and the lead sends it back.

## docs/STATUS.md: the handoff file

`docs/STATUS.md` is the single place that says where the project actually stands, as opposed to where the plan says it should. It is updated at the end of every unit of work by the agent that did the work, and checked by the reviewer on every PR. It never grows into a log; it describes the present.

Sections, kept in this order:

- Current phase and the last unit completed, with its PR number.
- Deviations from the plan still in effect (anything the plan says that the repo no longer matches), each with the ADR or PR that explains it.
- Known issues and skipped steps, each with an owner agent and the condition for closing it.
- Environment and configuration notes: how to run things that are not in the Makefile, local-only quirks, credentials that must exist, fixture locations.
- Interfaces agreed between agents that are not yet in code (a column list warehouse-eng promised terrain-rf-eng, say).
- Next three units of work in order.

When a deviation is resolved or a known issue closed, its line is removed, not struck through. History lives in git and in the ADRs.

## Lead duties after every unit

1. Read the run report in full.
2. Confirm `docs/STATUS.md` reflects it; if the agent missed something, send it back rather than fixing it yourself.
3. Run the reviewer.
4. Summarise for Casey in five lines or fewer: outcome, deviations, what the reviewer flagged, decisions waiting, next unit.
5. The next brief you write starts by quoting the "Configuration the next agent needs" lines from the previous report that apply.
6. In the same summary message, not in the repo, write a plain-language note of at most two paragraphs on what the unit built and why it is shaped that way, defining any term on first use, and give Casey one hands-on exercise he can do against the running stack in under 15 minutes to see it work.

## Commits, pull requests and merging

Attribution:

- Git author and committer are Casey's name and email on every commit. Set `git config user.name` and `user.email` in the repo before the first agent commit, and turn off any automatic co-author trailer in the coding tool's settings.
- Every commit ends with one trailer line naming the agent: `Agent: terrain-rf-eng`. Nothing else goes after the body. No `Co-authored-by`, no model or product names.
- Every PR description begins with one line: `Author: <agent>. Reviewer: reviewer.`

Who may do what:

- Agents commit on their feature branch after each red, green and refactor step, and open the PR themselves.
- Agents never push to `main` directly. `main` is protected: required CI checks, linear history, no force pushes.
- Phase 0 and 1: the lead merges after Casey approves every PR.
- From Phase 2, once CI runs `dbt build` on real fixtures: the lead merges without waiting for Casey when all of these hold: CI green, reviewer reports no blocking issues, the PR touches nothing on the "stop for a human" list, and no schema change. Otherwise the lead applies the `needs-owner` label and waits.
- Merge method is rebase-and-merge. The red, green and refactor commits land on `main` in order; no merge commits, no squash.
- Tag at the end of each phase: `phase-N`.

## Decisions that stop for a human

Casey decides these. Prepare the options, state the trade-offs in two or three sentences each, recommend one, and wait.

- Any change to the data model grain or to a rule in `docs/data-quality-contract.md`.
- Choice of propagation model implementation (ITM bindings vs SPLAT fallback).
- The link-resolution method and position source (ADR 0003), the minimum validation distance, and the number of endpoint draws.
- Anything that spends money (cloud resources, paid APIs).
- Anything that publishes data externally (GitHub Pages, ArcGIS Online, tiles on S3).
- Adding a dependency outside the stack listed in `docs/PLAN.md`.
- Deleting data or dropping a table.

## Testing policy

We do test-driven development where a test adds real confidence, and we do not write tests that exist only to exist.

Write the test first for: pure functions in `terrain/`, the protobuf decoder, every dbt model (schema tests and singular tests), Dagster asset checks, Terraform validation, anything with arithmetic or geometry in it.

Do not write unit tests for: thin wrappers around a third-party call that would only test the mock, download assets that hit the network (use a recorded fixture and a smoke test instead), the MapLibre page (manual check with a screenshot in the PR), and the arcpy script (cannot run in CI; keep a documented manual smoke run).

When you decide not to test something, say so in the PR description in one sentence. That sentence is the test of whether the decision was honest.

Coverage target: 80 percent on `terrain/` and `ingester/`. No target elsewhere. Do not chase coverage with trivial tests.

## Engineering conventions

- Python 3.12. `uv` manages the environment; never `pip install` outside it. Pin in `uv.lock`.
- `ruff` for lint and format, `mypy --strict` on `terrain/` and `pipeline/`. Both run in pre-commit and CI.
- Logging: `structlog`, JSON renderer in containers, console renderer locally. Log at the boundary of every external interaction (MQTT, filesystem, database, HTTP) with enough context to reproduce. Never log secrets, raw payload bytes, or unhashed node ids.
- Errors: catch narrowly and only where you can do something useful. Re-raise with context using `raise ... from err`. A function that swallows an exception must log it at warning or above and say why it is safe to continue. Dagster assets fail loudly; retries are configured on the asset, not hand-rolled.
- Configuration: environment variables read once at startup through a typed settings object (`pydantic-settings`). No `os.environ` calls scattered through the code.
- Database access: `psycopg` 3 with explicit transactions; bulk loads use `COPY`; every loader is idempotent (`ON CONFLICT DO NOTHING` or an incremental dbt model with a unique key).
- Geometry: store SRID 4326, compute in EPSG:26915. Every function that takes coordinates says which CRS it expects in its signature or docstring.
- SQL: lowercase keywords, one clause per line, CTEs over nested subqueries, `dbt` models under 150 lines or split.
- Secrets: `.env` locally (gitignored), SSM in cloud. `.env.example` lists every variable with a comment.
- Node ids are pseudonymised with a keyed hash before anything leaves the `core` schema.
- SQL from Python is always parameterised; no string formatting into a query, ever.
- Resource handles (files, connections, cursors, rasters) are opened with context managers.
- Prefer `pathlib.Path` over `os.path`; prefer `structlog` over `print`.
- Functions stay under 40 lines; extract when approaching it.
- Every top-level package (`ingester/`, `pipeline/`, `terrain/`, `dbt/`, `web/`, `arcgis/`, `infra/`) has a README stating its purpose, how to run it, and what it deliberately does not do.
- `docs/schema.md` documents every table in `core`, `features` and `analytics` with its grain and owner; `warehouse-eng` keeps it current and the reviewer checks it on any schema PR.

## PR description additions

Beyond What, Why, How verified and Not done, a PR that touched another specialty's interface adds a fifth short section, Consulted, naming the other agent and what was agreed. A PR that made a choice between alternatives adds a one-line Decision pointing to the ADR, or states that no ADR was needed and why.

## Writing style for code, comments, commits, docs and PRs

All written output reads as if an experienced engineer wrote it for a colleague. Specifically:

- Plain sentences. Say the fact, then stop. No preamble, no summary of what you are about to say.
- No stock phrases: avoid "it's worth noting", "in order to", "leverage", "robust", "seamless", "delve", "comprehensive", "ensure that", "it is important to". If a sentence works without the word, cut the word.
- Punctuation: use commas, periods, colons and parentheses. Do not use em dashes or en dashes anywhere, including in code comments and commit messages. Use a hyphen only inside compound words. Do not use emoji.
- No signatures, model names, product names or "generated by" lines in code, comments, commits or docs. Attribution of agent work is handled once in `CONTRIBUTING.md`.
- Comments explain why, not what. If the code needs a comment to explain what it does, rename things until it does not.
- Docstrings on public functions: one sentence of purpose, then arguments with units and CRS where relevant, then what is raised. Google style.
- Commit messages: conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `refactor:`), imperative mood, subject under 72 characters, body explains why when the subject is not enough.
- PR descriptions: What, Why, How verified, Not done. Four short paragraphs or bullets. No checklists copied from a template.
- Documentation in `docs/` is written for Casey first. Define a term the first time it appears. Prefer a worked example over an abstract description.

Full detail in `docs/STYLE.md`.

## Makefile targets every agent uses

- `make up` / `make down`: Compose stack.
- `make check`: ruff, mypy, pytest, dbt build against the local stack.
- `make reproduce`: rebuild every asset from the raw zone.
- `make fmt`: ruff format.

If a target does not exist yet, `platform-eng` creates it in Phase 0.

## Where to look

- `docs/PLAN.md`: phases, exit criteria, data model, stack.
- `docs/data-quality-contract.md`: acceptance rules mirrored by dbt tests.
- `docs/STYLE.md`: writing and code conventions in full.
- `docs/adr/`: one file per decision; write one when a "stop for a human" item is decided.
- `CONTRIBUTING.md`: how the agent team and human review work, for outside readers.
