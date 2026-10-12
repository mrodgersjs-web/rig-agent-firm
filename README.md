<div align="center">
  <img src="assets/rig-agent-firm-hero.png" width="100%" />
</div>

<br/>

<div align="center">
  <h3>agent-firm</h3>
  <p><em>A GitHub-native agent firm — the heartbeat that proves it's alive.</em></p>
</div>

<div align="center">

![heartbeat](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmrodgersjs-web%2Frig-agent-firm%2Fheartbeat%2Fbadge.json)
[![heartbeat](https://img.shields.io/github/actions/workflow/status/mrodgersjs-web/rig-agent-firm/heartbeat.yml?branch=main)](https://github.com/mrodgersjs-web/rig-agent-firm/actions/workflows/heartbeat.yml)
![agents](https://img.shields.io/badge/agents-7-C8A96E?style=flat-square&labelColor=0A0806)
![license](https://img.shields.io/badge/license-MIT-C8A96E?style=flat-square&labelColor=0A0806)

</div>

<br/>

> Each repository owns one role. This repository owns the heartbeat: an hourly job that checks every agent's invariant through the GitHub API, writes a status JSON with evidence, and regenerates a badge and dashboard from those files.

## Quick start

```bash
pip install -e '.[dev]'
agent-firm validate                      # check agents/*.yaml against the schema
scripts/smoke.sh                         # full engine run on recorded API fixtures, outputs verified
GITHUB_TOKEN=<token> agent-firm heartbeat --out out   # live run against GitHub (public repos need no scopes)
cat out/badge.json                       # shields endpoint; out/dashboard.svg and out/status/*.json sit beside it
```

`agent-firm heartbeat` flags: `--only a,b` (subset of agents), `--fixtures DIR` (replay recorded responses), `--record DIR` (save live responses as fixtures), `--now ISO` (fix the clock), `--fail-on-offline` (exit 1 unless every agent is online). `agent-firm render --status out/status --out out` rebuilds the badge and dashboard from existing status files.

## How it works

<sub align="center">agents/*.yaml spec → validator → heartbeat engine (GitHub REST API) → status/*.json → badge.json + dashboard.svg</sub>

1. **Spec** — `agents/<repo>.yaml` declares role, boundary, trigger, SLA (`sla` prose plus `sla_hours`), a one-line `invariant`, and a list of machine `checks`. `agent-firm validate` rejects missing fields, unknown check types or params, bad repo slugs, and duplicate agent names or repos.
2. **Checks** — each check is evaluated against the agent's repo on its default branch:

   | Type | Passes when |
   | :-- | :-- |
   | `file_exists` (`path`, optional `executable`) | the file is a blob in the branch's git tree (executable = mode 100755) |
   | `workflow_success` (`workflow`, optional `max_age_hours`) | the latest completed run of that workflow on the default branch concluded `success` and finished within the window (default `sla_hours`) |
   | `release_exists` (optional `max_age_hours`) | the repo has a latest published release |
   | `commit_within` (optional `hours`) | the default branch has a commit newer than the window |

3. **Status** — one `status/<repo>.json` per agent: `status` (`online` when every check passes, `offline` when any check definitively fails or the repo is missing, `unknown` when the API could not answer, for example a rate limit or 5xx), per-check `detail`, plus evidence: `run_url`, `sha`, `evidence_at`, `checked_at`. The older keys (`repo`, `owner`, `agent`, `invariant`, `invariant_result`, `checked_at`, `run_url`) are kept; `schema_version` is 2.
4. **Firm status** — `online` if all agents are online, `offline` if any is offline, `degraded` if some are unknown. `badge.json` is a shields endpoint (`N/M online`; green, yellow or red). `dashboard.svg` shows one card per agent with its check results and evidence.
5. **Workflow** — `.github/workflows/heartbeat.yml` runs hourly (and on manual dispatch), installs this package, runs the engine with the workflow's own `GITHUB_TOKEN`, uploads the output as an artifact, and commits `status/`, `badge.json` and `dashboard.svg` to the `heartbeat` branch. `ci.yml` runs ruff, pytest and `scripts/smoke.sh` on every push and pull request.

A red badge is honest, not an error. A check that fails reports what it saw: for example, an agent whose smoke workflow last succeeded 770 hours ago is `offline` even though that run was green.

## The fleet

| Agent | Repo | Role | Verify |
| :-- | :-- | :-- | :-- |
| Verifier | [proof-studio](https://github.com/mrodgersjs-web/proof-studio) | Catch false "done" with signed ProofPackets | smoke script + `smoke.yml` run |
| Operator | [jake-studio](https://github.com/mrodgersjs-web/jake-studio) | Run closed-loop operator sessions | smoke script + `smoke.yml` run |
| Fleet | [mesh-studio](https://github.com/mrodgersjs-web/mesh-studio) | Probe, boot, and recover nodes | smoke script + `smoke.yml` run |
| Builder | [rigforge](https://github.com/mrodgersjs-web/rigforge) | Deterministic build and proof scaffolding | smoke script + `smoke.yml` run |
| Strategist | [fde-portfolio](https://github.com/mrodgersjs-web/fde-portfolio) | Discovery → eval → handoff playbooks | smoke script + `smoke.yml` run |
| Governor | [doctrine](https://github.com/mrodgersjs-web/doctrine) | Rules agents load before acting | smoke script + `smoke.yml` run |
| Improver | [rig-agent-firm](https://github.com/mrodgersjs-web/rig-agent-firm) | Nightly measure → pick → PR loop for every studio/foundry/capability repo | smoke script + `ci.yml` run |

<sup>Each agent is declared in <code>agents/</code> and bound by <a href="./CONSTITUTION.md"><code>CONSTITUTION.md</code></a>.</sup>

## The Improver (nightly improvement loop)

`agents/improver.yaml` declares the seventh role. Its cycle is **MEASURE → PICK → BUILD → VERIFY → SHIP → RECORD**, run nightly by `.github/workflows/improver.yml` for every repo whose GitHub topics or name tokens match `studio`, `foundry` or `capability` (public repos by default).

```bash
GITHUB_TOKEN=<token> agent-firm improver measure --out out/improver   # scorecard.json + scorecard.md from live GitHub data
agent-firm improver pick --scorecard out/improver/scorecard.json      # lowest dimension -> one scoped issue draft (dry run)
agent-firm improver verify mrodgersjs-web/<repo>#<n>                  # PR ready to ship = CI pass + review by someone else
agent-firm improver record --before old/scorecard.json --after out/improver/scorecard.json   # before/after receipt
```

| Dimension | Measured from (GitHub REST) |
| :-- | :-- |
| `ci_smoke` | latest completed run of every workflow on the default branch passed (60) + a smoke script/workflow exists and passed (40) |
| `tests_coverage` | test files in the tree (50), 5+ test files (20), a coverage config file (30) |
| `stranger_time` | static proxy for stranger time-to-working: README quick start/install heading (35), a fenced install/run command (35), an install manifest (30) |
| `readme_quality` | README length (30), 3+ headings (25), description (15), license (15), 3+ topics (15) |
| `hero_image` | README shows a non-badge image (100); hero file present but not shown (50) |
| `release_cadence` | age of the latest published release (≤30d 100, ≤90d 70, ≤180d 40, older 15, none 0) |
| `issue_age` | median age of open issues (≤14d 100, ≤30d 80, ≤90d 50, older 20, none open 100) |
| `security_basics` | `.github/dependabot.yml` (35), CodeQL workflow or default setup (35), secret scanning enabled (30) |

A part the API cannot answer (for example secret-scanning status without an admin token) is `?`: left out of the score, never counted as a pass. Without a token the CLI warns that unauthenticated GitHub allows 60 requests/hour; any rate-limit or 5xx makes the scorecard `partial` and `measure` exits 3 (use `--allow-partial` to accept it).

`pick` takes each repo's lowest known dimension (ties go to the order in the spec), skips repos that already have an open `improver` issue, ranks across the firm and keeps `max_issues_per_night` (1). It is a dry run unless `--open-issue` is passed; the nightly workflow opens issues only when the repository variable `IMPROVER_OPEN_ISSUES` is `true` and an `IMPROVER_TOKEN` secret exists. BUILD is done by a coding agent on the issue's suggested branch.

**Merges stay with the human; the Improver only opens issues and PRs.** This is enforced three ways: the spec validator rejects an `improver` block without `merge_policy: human_only` and `auto_merge: false`; `agent-firm improver` refuses to run unless `.rig/loop.yaml` `roles.improver` says the same; and `agent_firm/github.py` allow-lists one write (`POST repos/<o>/<r>/issues`) and refuses merge, review, update-branch and GraphQL (auto-merge) endpoints before any network call.

## Why it exists

- **The badge is proof, not decoration** — it is regenerated hourly from API checks on every fleet repo, with the run URL and commit SHA that back each result
- **One repo, one role** — no agent's responsibilities overlap with another's
- **Forkable by design** — copy the repo, edit the agent specs, get your own firm
- **GitHub-native** — one workflow, one small Python package (PyYAML is the only dependency), no external services

<details>
<summary><strong>Fork this firm</strong></summary>

<br/>

See [`CONSTITUTION.md`](./CONSTITUTION.md) for the full fork-and-instantiate guide. The short version:

1. Copy this repository
2. Edit `agents/*.yaml` (and run `agent-firm validate`); the workflow reads the list from there
3. Enable Actions and push — the workflow creates the `heartbeat` branch on first run
4. Point your profile README at the generated badge

</details>

## Documentation

| Resource | Description |
| :-- | :-- |
| [`agents/`](./agents/) | Per-repo agent specs |
| [`CONSTITUTION.md`](./CONSTITUTION.md) | Fork-and-instantiate guide |
| [`agent_firm/`](./agent_firm/) | Schema, GitHub client, heartbeat engine, renderers, CLI |
| [`tests/`](./tests/) | Tests and recorded API fixtures (`tests/fixtures/api`) |
| [`.github/workflows/heartbeat.yml`](.github/workflows/heartbeat.yml) | Hourly heartbeat |
| [LICENSE](LICENSE) | MIT |

## Roadmap

Not built yet:

- Checks beyond the four types above (issue/PR queue depth, signed ProofPackets). The specs name `queue_depth_metric`, `last_action_metric` and `claim` as documentation only; the engine does not read them.
- `heartbeat_cron` in a spec is documentation; the real schedule is the cron line in `heartbeat.yml`.
- Alerting when an agent goes offline.
- Improver BUILD (writing the fix PR) is done by a coding agent, not by this package; `stranger_time` is a static README/manifest proxy, not a timed fresh-clone run.
- `mcp/server.py` is a separate results server carried over from the template; the heartbeat does not use it.

---

<div align="center"><sub>Built by Mike Rodgers · Forward Deployed Engineer · <a href="https://rodgersintelligence.com">rodgersintelligence.com</a></sub></div>
