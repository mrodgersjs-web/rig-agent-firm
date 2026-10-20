# Contributing

## Bar

1. Public-boundary safe (no PII, secrets, client dumps, ToS-risk bots).
2. `ruff check .` clean and `pytest -q` passing.
3. `agent-firm validate` clean (every spec in `agents/` must satisfy the schema).
4. README claims must match runnable commands.
5. Prefer small diffs with proof over large narrative refactors.

## Flow

1. Fork or branch from `main`.
2. `pip install -e '.[dev]'`, then add or adjust tests for the change.
3. Run `ruff check . && pytest -q --cov=agent_firm` and `bash scripts/smoke.sh`.
4. Open a PR with: what changed, how verified, boundary impact.

Adding an agent means one spec file named for its role (`agents/<name>.yaml`)
plus recorded fixtures if it brings a new check type.
