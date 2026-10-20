# rig-agent-firm — copilot instructions

## What it is
A GitHub-native agent firm: the heartbeat cron (`0 * * * *`) proves
each agent is alive by hitting the GitHub REST API, validating the
configured invariants, and regenerating a per-agent status JSON, a
shields badge endpoint, and a dashboard SVG. The engine
(`agent_firm/`) is one Python package; the heartbeat job lives in
`.github/workflows/heartbeat.yml`.

## Layout
- `agent_firm/` — package: `cli.py`, `engine.py`, `render.py`,
  `validate.py`, `recorder.py`, `__main__.py`.
- `agents/` — `*.yaml` per-agent spec files (the ones the engine
  validates and the heartbeat probes).
- `tests/` — pytest suite (`tests/test_firm.py` + fixture API
  recordings).
- `scripts/smoke.sh` — full engine run on recorded fixtures; CI calls
  this as the smoke step.
- `.github/workflows/ci.yml` — matrix pytest + actionlint.
- `.github/workflows/heartbeat.yml` — hourly cron + manual dispatch.

## Setup and tests
```
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest -q
scripts/smoke.sh
```

The CLI lives at the `agent-firm` script entry point declared in
`pyproject.toml`. The smoke script writes its outputs to a temp dir
and revalidates schema + invariants; CI gates on a clean run.

## Conventions
Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `ci:`,
`build:`, `chore:`). Every public-code change ships a test. Heartbeat
schedule (`cron: '0 * * * *'`) is **load-bearing** and must not be
moved to a non-hourly cron without a separate steering ask. No
secrets in tracked files; `GITHUB_TOKEN` is the workflow token the
heartbeat job injects at run time. No machine paths
(`/Users/...`, `~/Developer/...`, `$HOME/Developer/...`). No prices,
no client names, no credential / military / personnel claims. Banned
words: unlock, empower, synergy, leverage, disrupt, hustle, utilize,
iterate, optimize. No em dashes.

## Truth rule
Every number in README and docs must come from a command you just
ran. Counts of agents / specs come from `agent-firm validate`. Badge
metrics come from the heartbeat output (`out/status/*.json`). The
README diagrams come from the data the engine produced on the last
recorded run. Drift fails CI.

## Do not touch
- The `cron: '0 * * * *'` line in `heartbeat.yml` and the
  `concurrency: heartbeat` group.
- `tests/fixtures/` recorded API responses (mtime-sensitive caches;
  `scripts/record.sh` regenerates them).
- The badge endpoint path consumed by the live `heartbeat` shields.

## Done-test for this work
```
ruff check .
pytest -q --cov=agent_firm --cov-report=term-missing
scripts/smoke.sh
```
All three clean on a fresh venv.
