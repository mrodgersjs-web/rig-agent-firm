# Agent Firm Constitution

This document defines the agent-firm model used by `mrodgersjs-web`. It is
intended to be forked: copy this repository, edit the files under `agents/`,
and enable the hourly heartbeat.

## What an agent firm is

An agent firm is a set of role-bound agents mapped one-to-one to repositories.
Each agent has a narrow job, a hard boundary, and a machine-readable heartbeat.
The firm does not replace humans; it makes agent autonomy observable and
governed.

## Roles

| Agent | Repo | Responsibility | Boundary |
| --- | --- | --- | --- |
| Verifier | [proof-studio](https://github.com/mrodgersjs-web/proof-studio) | Seal and verify ProofPackets | Validates only; never writes production code |
| Operator | [jake-studio](https://github.com/mrodgersjs-web/jake-studio) | Run closed-loop sessions | Executes plans; escalates below 70% confidence |
| Fleet | [mesh-studio](https://github.com/mrodgersjs-web/mesh-studio) | Probe, boot, recover nodes | Infrastructure only; no autonomous code changes |
| Builder | [rigforge](https://github.com/mrodgersjs-web/rigforge) | Scaffold deterministic builds | Build automation only; keys stay in CI secrets |
| Strategist | [fde-portfolio](https://github.com/mrodgersjs-web/fde-portfolio) | Discovery, eval, handoff | Produces briefs; no client execution without contract |
| Governor | [doctrine](https://github.com/mrodgersjs-web/doctrine) | Versioned guardrails | Read-only at runtime; changes require human approval |

## Agent spec format

Every agent is declared in `agents/<dept>.yaml` with these fields:

- `name` — short agent name
- `dept` — repository the agent owns
- `repo` — full GitHub repository slug
- `role` — what the agent is responsible for
- `boundary` — what it must not do
- `trigger` — when it wakes
- `implementation` — how it does its job
- `sla` — expected behavior under normal operation
- `heartbeat_cron` — when the firm checks this agent
- `invariant` — the check the heartbeat runs
- `claim` — the public claim the invariant defends
- `owner`, `status_branch`, `queue_depth_metric`, `last_action_metric`

## Heartbeat

`.github/workflows/heartbeat.yml` runs every hour. It:

1. Checks out every fleet repo in a matrix job.
2. Verifies the declared invariant (e.g., `scripts/smoke.sh` exists and is
   executable).
3. Writes a status JSON per repo.
4. Generates `badge.json` for shields.io.
5. Pushes the status JSONs and badge to the `heartbeat` branch.

A green badge means every monitored repo passed its invariant. A red badge
means at least one failed.

## How to fork and instantiate

1. Click **Use this template** or `git clone` this repository.
2. Replace `mrodgersjs-web` in `agents/*.yaml` and `.github/workflows/heartbeat.yml`
   with your GitHub owner.
3. Edit the matrix in `.github/workflows/heartbeat.yml` to list your repos.
4. Write an `invariant` per agent that is cheap, deterministic, and proves the
   agent is not dead.
5. Enable GitHub Actions and create an empty `heartbeat` branch, or let the
   workflow create it on first run.
6. Point your profile README badge at the generated `badge.json`.

## Governance rules

1. **No role shall do another role's job.** The Verifier never writes code; the
   Builder never overrides a gate; the Governor never executes.
2. **Every agent must have an invariant.** If a repo cannot prove it is alive,
   it is offline.
3. **Heartbeat failures are public.** Status lives on a branch, not in a chat
   log, so anyone can audit history.
4. **Changes to boundaries require a constitution amendment.** Update this file
   and the relevant `agents/*.yaml`, then record the change in the heartbeat
   branch commit log.
5. **Humans remain the decision-maker.** Agents advise, automate, and escalate;
   they do not approve irreversible actions.
