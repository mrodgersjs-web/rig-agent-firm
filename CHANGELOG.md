# Changelog

Notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
semantic versioning.

## [0.2.0] - 2026-10-19

### Changed

- One truthful naming across code and docs: an agent is named by its role. The
  spec files are now `agents/<name>.yaml` (`governor`, `strategist`, `operator`,
  `fleet`, `verifier`, `builder`), status files are `status/<name>.json`, and
  `--only` selects agents by role name. `repo` and `dept` keep naming the
  repository each agent stewards (the schema still requires `dept` to equal the
  `repo` name). Note: the `heartbeat` branch keeps older `status/<repo>.json`
  files from before this change; new runs write `status/<name>.json`.
- CI runs ruff, pytest with coverage and the smoke on Python 3.10 to 3.13.

### Added

- Naming-law test (spec file stem must equal the agent name) alongside the
  existing fixture-driven engine tests (16 tests total).
- Packaging metadata (version 0.2.0, authors, classifiers, project URLs,
  pytest-cov dev extra, `py.typed`), community files: this changelog,
  code of conduct, issue templates, pull request template, dependabot config.

## [0.1.0] - 2026-10-12

### Added

- Agent spec schema and validator, heartbeat engine with four check types,
  badge and dashboard renderers, `agent-firm` CLI (`validate`, `heartbeat`,
  `render`), recorded API fixtures and the hourly heartbeat workflow.
