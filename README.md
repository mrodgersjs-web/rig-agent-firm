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
![agents](https://img.shields.io/badge/agents-6-C8A96E?style=flat-square&labelColor=0A0806)
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

<sup>Each agent is declared in <code>agents/</code> and bound by <a href="./CONSTITUTION.md"><code>CONSTITUTION.md</code></a>.</sup>

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
- `mcp/server.py` is a separate results server carried over from the template; the heartbeat does not use it.

---

<div align="center"><sub>Built by Mike Rodgers · Forward Deployed Engineer · <a href="https://rodgersintelligence.com">rodgersintelligence.com</a></sub></div>
