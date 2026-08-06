<div align="center">
  <img src="assets/rig-agent-firm-hero.png" width="100%" />
</div>

<br/>

<div align="center">
  <h3>agent-firm</h3>
  <p><em>A GitHub-native agent firm — the heartbeat that proves it's alive.</em></p>
</div>

<div align="center">

![heartbeat](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmrodgersjs-web%2Fagent-firm%2Fheartbeat%2Fbadge.json)
![agents](https://img.shields.io/badge/agents-6-C8A96E?style=flat-square&labelColor=0A0806)
![license](https://img.shields.io/badge/license-MIT-C8A96E?style=flat-square&labelColor=0A0806)

</div>

<br/>

> 🥇 Each repository owns one role. This repository owns the heartbeat — an hourly job that checks every agent's invariant, writes status JSON, and regenerates a badge that never lies about whether the firm is actually running.

## 60-second install

```bash
# Create some sample status files
mkdir -p status
echo '{"repo":"proof-studio","status":"online"}' > status/proof-studio.json
echo '{"repo":"jake-studio","status":"online"}' > status/jake-studio.json

# Generate the endpoint badge
./scripts/generate-dashboard.sh status badge.json
cat badge.json
```

## How it works

<div align="center">
  <img src="assets/architecture.svg" width="100%" alt="Agent firm architecture: agent specs declare role and invariant, hourly heartbeat workflow checks each repo, writes status JSON, and regenerates the mission control badge" />
</div>

<sub align="center">agents/*.yaml spec → hourly heartbeat workflow → status JSON on heartbeat branch → dashboard.svg + badge.json</sub>

1. **Spec** — `agents/<repo>.yaml` defines the agent's role, boundary, trigger, implementation, SLA, and heartbeat invariant
2. **Heartbeat** — `.github/workflows/heartbeat.yml` runs hourly, checks the invariant in each fleet repo, writes a status JSON, regenerates the shields.io badge
3. **Dashboard** — `dashboard.svg` is the mission-control template; status JSONs live on the `heartbeat` branch for downstream renderers
4. **Badge** — `scripts/generate-dashboard.sh` reads the status JSONs and emits `badge.json`

## The fleet

| Agent | Repo | Role | Verify |
| :-- | :-- | :-- | :-- |
| Verifier | [proof-studio](https://github.com/mrodgersjs-web/proof-studio) | Catch false "done" with signed ProofPackets | `scripts/smoke.sh` |
| Operator | [jake-studio](https://github.com/mrodgersjs-web/jake-studio) | Run closed-loop operator sessions | `scripts/smoke.sh` |
| Fleet | [mesh-studio](https://github.com/mrodgersjs-web/mesh-studio) | Probe, boot, and recover nodes | `scripts/smoke.sh` |
| Builder | [rigforge](https://github.com/mrodgersjs-web/rigforge) | Deterministic build and proof scaffolding | `scripts/smoke.sh` |
| Strategist | [fde-portfolio](https://github.com/mrodgersjs-web/fde-portfolio) | Discovery → eval → handoff playbooks | `scripts/smoke.sh` |
| Governor | [doctrine](https://github.com/mrodgersjs-web/doctrine) | Rules agents load before acting | `scripts/smoke.sh` |

<sup>Each agent is declared in <code>agents/</code> and bound by <a href="./CONSTITUTION.md"><code>CONSTITUTION.md</code></a>.</sup>

## Why it exists

- **The badge is proof, not decoration** — it's regenerated hourly from a real invariant check on every fleet repo
- **One repo, one role** — no agent's responsibilities overlap with another's
- **Forkable by design** — copy the repo, edit the agent specs, get your own firm
- **GitHub-native** — no external orchestrator, no separate infrastructure to run

<details>
<summary><strong>Fork this firm</strong></summary>

<br/>

See [`CONSTITUTION.md`](./CONSTITUTION.md) for the full fork-and-instantiate guide. The short version:

1. Copy this repository
2. Edit `agents/*.yaml` and the matrix in `.github/workflows/heartbeat.yml`
3. Enable Actions and push — the workflow creates the `heartbeat` branch on first run
4. Point your profile README at the generated badge

</details>

## Documentation

| Resource | Description |
| :-- | :-- |
| [`agents/`](./agents/) | Per-repo agent specs |
| [`CONSTITUTION.md`](./CONSTITUTION.md) | Fork-and-instantiate guide |
| [`.github/workflows/heartbeat.yml`](.github/workflows/heartbeat.yml) | Hourly invariant check |
| [LICENSE](LICENSE) | MIT |

---

<div align="center"><sub>Built by Mike Rodgers · Forward Deployed Engineer · <a href="https://rodgersintelligence.com">rodgersintelligence.com</a></sub></div>
