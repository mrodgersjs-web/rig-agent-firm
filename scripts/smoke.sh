#!/usr/bin/env bash
# Smoke test: validate the specs, run the heartbeat engine on recorded API fixtures,
# and check every output. Needs the package installed (pip install -e .).
set -euo pipefail
cd "$(dirname "$0")/.."
out="$(mktemp -d)"
agent-firm validate --agents agents
agent-firm heartbeat --agents agents --fixtures tests/fixtures/api --out "$out" --now 2026-10-12T15:11:22Z
python - "$out" <<'PY'
import json, sys, xml.etree.ElementTree as ET
from pathlib import Path
out = Path(sys.argv[1])
status = sorted((out / "status").glob("*.json"))
assert len(status) == 7, f"expected 7 status files, got {len(status)}"
for f in status:
    d = json.loads(f.read_text())
    assert d["status"] in {"online", "offline", "unknown"}, f
    assert d["checked_at"] and d["checks"], f
    assert d["run_url"].startswith("https://github.com/"), f
badge = json.loads((out / "badge.json").read_text())
assert badge["schemaVersion"] == 1 and badge["label"] == "agent firm", badge
assert badge["message"] == "5/7 online" and badge["color"] == "red", badge
ET.parse(out / "dashboard.svg")
print("smoke ok:", badge["message"])
PY
# Improver: measure + pick on recorded live API responses, then a receipt.
agent-firm improver measure --fixtures tests/fixtures/improver --out "$out/improver" --now 2026-10-12T15:11:22Z
agent-firm improver pick --scorecard "$out/improver/scorecard.json" --out "$out/improver"
agent-firm improver record --before "$out/improver/scorecard.json" --after "$out/improver/scorecard.json" \
  --out "$out/improver"
python - "$out/improver" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
card = json.loads((d / "scorecard.json").read_text())
assert card["merge_policy"] == "human_only" and card["auto_merge"] is False, card["merge_policy"]
assert len(card["repos"]) == 8 and not card["partial"], len(card["repos"])
for r in card["repos"]:
    assert set(r["dimensions"]) == set(card["dimensions"]), r["repo"]
picks = json.loads((d / "picks.json").read_text())
assert picks["dry_run"] and picks["opened"] == [] and len(picks["chosen"]) == 1, picks["chosen"]
c = picks["chosen"][0]
assert (c["repo"], c["dimension"]) == ("mrodgersjs-web/agency-studio", "release_cadence"), c
assert "Merges stay with the human owner" in c["body"]
assert (d / "scorecard.md").read_text().startswith("# Improver scorecard")
print("smoke ok: improver scored", len(card["repos"]), "repos; pick", c["repo"], c["dimension"], c["score"])
PY
