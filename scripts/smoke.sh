#!/usr/bin/env bash
# Smoke test: validate the specs, run the heartbeat engine on recorded API fixtures,
# and check every output. Needs the package installed (pip install -e .).
set -euo pipefail
cd "$(dirname "$0")/.."
out="$(mktemp -d)"
agent-firm validate --agents agents
agent-firm heartbeat --agents agents --fixtures tests/fixtures/api --out "$out" --now 2026-10-11T00:24:34Z
python - "$out" <<'PY'
import json, sys, xml.etree.ElementTree as ET
from pathlib import Path
out = Path(sys.argv[1])
status = sorted((out / "status").glob("*.json"))
assert len(status) == 6, f"expected 6 status files, got {len(status)}"
for f in status:
    d = json.loads(f.read_text())
    assert d["status"] in {"online", "offline", "unknown"}, f
    assert d["checked_at"] and d["checks"], f
    assert d["run_url"].startswith("https://github.com/"), f
badge = json.loads((out / "badge.json").read_text())
assert badge["schemaVersion"] == 1 and badge["label"] == "agent firm", badge
assert badge["message"] == "4/6 online" and badge["color"] == "red", badge
ET.parse(out / "dashboard.svg")
print("smoke ok:", badge["message"])
PY
