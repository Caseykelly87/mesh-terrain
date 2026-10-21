# Kickoff

## Setup (once)

1. Create an empty folder, `git init`, and copy the contents of this bundle into it so `CLAUDE.md`, `CONTRIBUTING.md`, `KICKOFF.md`, `.claude/agents/` and `docs/` sit at the repo root.
2. Set your git identity in the repo: `git config user.name "<your name>"` and `git config user.email "<your email>"`. Every commit, including agent commits, is authored by you.
3. In the Claude Code settings file, set `includeCoAuthoredBy` to `false` so it stops appending its own co-author trailer. Check the first agent commit to confirm only the `Agent:` trailer is present.
4. Open the folder in Claude Code (desktop app or terminal). Confirm the agents are visible with `/agents`.
5. Make the first commit yourself, with the trailer, so the pattern is in the history from the start:

   ```
   chore: add project instructions, agent team and plan

   Agent: lead
   ```

6. Create the GitHub repo, push, and protect `main`: require the CI status checks (names appear after the first Phase 0 PR), require linear history, block force pushes, and allow only rebase merging in the repository merge settings. Create a `needs-owner` label.
7. Paste the prompt below as your first message.

## First prompt to paste

```
You are the lead session on this repository. Read CLAUDE.md, docs/PLAN.md, docs/STYLE.md, docs/data-quality-contract.md and CONTRIBUTING.md before anything else.

We are starting Phase 0. Work as follows:

1. Write a task brief for platform-eng covering the Phase 0 tasks in docs/PLAN.md except the ITM spike: uv project, tooling, Makefile targets, Docker Compose with PostGIS, MinIO and Dagster, dbt project and profile, Dagster Definitions with the dbt resource, and GitHub Actions. State the acceptance test for each item.
2. Write a separate brief for terrain-rf-eng for the ITM spike only: try to build the NTIA ITM library with Python bindings inside the Dagster image, time-box three hours, and report both outcomes in a draft docs/adr/0002-propagation-model.md with a recommendation. No feature code yet.
3. Delegate both briefs in parallel.
4. When each returns, run the reviewer agent on the diff.
5. Confirm each agent returned the run report in the fixed format and updated docs/STATUS.md; send it back if either is missing.
6. Give me a summary of five lines or fewer per unit of work: outcome, deviations from the brief, what the reviewer flagged, what you recommend, and which "stop for a human" decisions are waiting on me.

During Phase 0 and 1, do not merge anything until I say so. Do not write production code in this session; delegate it. Keep every message to me short and in plain language; I am learning GIS as we go, so define any term you use for the first time.
```

## Working rhythm after Phase 0

Each phase follows the same loop. Start the phase with:

```
Start Phase N. Write briefs for the owning agent(s) from docs/PLAN.md, delegate, review, summarise. Stop for every decision in the "stop for a human" list. For Phase 1, the first brief is the three-day spike and ADR 0003; nothing else in Phase 1 or 2 starts until I have decided it.
```

When you approve a PR:

```
Approved. Merge with a conventional commit and tell me the next unit of work.
```

When you want to understand a decision before approving:

```
Before I approve, explain in plain language why <thing> was done this way and what the alternative was. Two paragraphs.
```

At the end of each phase:

```
Phase N exit criteria check: go through each criterion in docs/PLAN.md, state pass or fail with evidence, tag the repo if all pass, and write the phase's ADRs if any decision is still unrecorded.
```

When Phase 2 exit criteria pass and CI runs dbt build on real fixtures:

```
Phase 2 exit criteria passed. From now on, merge routine PRs yourself under the rules in CLAUDE.md: CI green, reviewer reports nothing blocking, nothing on the stop list, no schema change. Label everything else needs-owner and wait for me.
```

## What to watch for

- An agent that writes code without a failing test first for something in `terrain/` or the decoder. Send it back.
- A PR description without a "Not done" section. Send it back.
- The reviewer reporting nothing blocking on a large diff. Ask it to trace the failure path by hand.
- Any dash that is not a hyphen inside a word, any emoji, any product or model name in the repo. Send it back; the reviewer should have caught it.
- A summary you do not understand. Ask. The point of the project is that you can explain it.
- A run report whose "Deviations" line says "none" on a unit that obviously hit trouble. Ask what was skipped.
- `docs/STATUS.md` unchanged after a unit of work. Something was not handed off.
- A commit without the `Agent:` trailer, or with any other trailer. The reviewer should have caught it; the lead sends it back.
