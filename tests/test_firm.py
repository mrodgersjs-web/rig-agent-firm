import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from agent_firm.cli import main
from agent_firm.github import ApiError, FixtureClient, fixture_name
from agent_firm.heartbeat import firm_status, parse_ts, run_agent, run_heartbeat
from agent_firm.render import make_badge, make_dashboard
from agent_firm.schema import load_specs, validate_spec

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "api"
NOW = parse_ts("2026-10-12T15:11:22Z")  # moment the newest fixtures (rig-agent-firm, improver) were recorded


@pytest.fixture
def specs():
    s, errs = load_specs(ROOT / "agents")
    assert errs == []
    return {x.dept: x for x in s}


def valid_spec():
    return yaml.safe_load((ROOT / "agents" / "proof-studio.yaml").read_text())


# ---------- schema ----------

def test_repo_specs_all_valid(specs):
    assert set(specs) == {"proof-studio", "jake-studio", "mesh-studio", "rigforge", "fde-portfolio", "doctrine",
                          "rig-agent-firm"}
    assert {s.name for s in specs.values()} == {"verifier", "operator", "fleet", "builder", "strategist", "governor",
                                                "improver"}


def test_validator_rejects_bad_specs():
    d = valid_spec()
    assert validate_spec(d) == []
    d["checks"] = [{"type": "telepathy"}]
    assert any("unknown type" in e for e in validate_spec(d))
    d = valid_spec()
    d["checks"] = [{"type": "file_exists"}]
    assert any("missing required param 'path'" in e for e in validate_spec(d))
    d = valid_spec()
    d["checks"] = []
    assert any("non-empty list" in e for e in validate_spec(d))
    d = valid_spec()
    d["sla_hours"] = 0
    assert any("sla_hours" in e for e in validate_spec(d))
    d = valid_spec()
    d["repo"] = "not-a-slug"
    assert any("owner/name" in e for e in validate_spec(d))
    d = valid_spec()
    del d["boundary"]
    assert any("'boundary'" in e for e in validate_spec(d))
    d = valid_spec()
    d["checks"][0]["bogus"] = 1
    assert any("unknown param 'bogus'" in e for e in validate_spec(d))


def test_duplicate_repo_is_rejected(tmp_path):
    for n in ("a.yaml", "b.yaml"):
        shutil.copy(ROOT / "agents" / "proof-studio.yaml", tmp_path / n)
    _, errs = load_specs(tmp_path)
    assert any("duplicate agent name" in e for e in errs)
    assert any("one repo, one role" in e for e in errs)


# ---------- heartbeat against recorded API fixtures ----------

def test_fixture_run_matches_recorded_reality(specs):
    client = FixtureClient(FIX)
    v = run_agent(client, specs["proof-studio"], NOW)
    assert v["status"] == "online"
    assert v["run_url"].startswith("https://github.com/mrodgersjs-web/proof-studio/actions/runs/")
    assert len(v["sha"]) == 40 and v["evidence_at"].endswith("Z")
    assert [c["ok"] for c in v["checks"]] == [True, True]
    # stale-but-green runs are reported honestly as offline
    d = run_agent(client, specs["doctrine"], NOW)
    assert d["status"] == "offline"
    assert "old (limit 168h)" in d["invariant_result"]
    # release check
    j = run_agent(client, specs["jake-studio"], NOW)
    assert j["status"] == "online" and j["checks"][2]["type"] == "release_exists" and j["checks"][2]["ok"]


def test_freshness_window_flips_with_clock(specs):
    client = FixtureClient(FIX)
    later = parse_ts("2026-10-20T00:24:34Z")  # 9 days on: every run now outside 168h
    assert run_agent(client, specs["proof-studio"], later)["status"] == "offline"


def test_non_executable_file_fails(tmp_path, specs):
    shutil.copytree(FIX, tmp_path / "api")
    f = tmp_path / "api" / fixture_name("repos/mrodgersjs-web/proof-studio/git/trees/main:scripts")
    data = json.loads(f.read_text())
    for e in data["tree"]:
        if e["path"] == "smoke.sh":
            e["mode"] = "100644"
    f.write_text(json.dumps(data))
    res = run_agent(FixtureClient(tmp_path / "api"), specs["proof-studio"], NOW)
    assert res["status"] == "offline"
    assert "not executable" in res["checks"][0]["detail"]


def test_failed_run_and_missing_release(tmp_path, specs):
    shutil.copytree(FIX, tmp_path / "api")
    runs = tmp_path / "api" / fixture_name(
        "repos/mrodgersjs-web/jake-studio/actions/workflows/smoke.yml/runs?branch=main&status=completed&per_page=1")
    data = json.loads(runs.read_text())
    data["workflow_runs"][0]["conclusion"] = "failure"
    runs.write_text(json.dumps(data))
    (tmp_path / "api" / fixture_name("repos/mrodgersjs-web/jake-studio/releases/latest")).unlink()
    res = run_agent(FixtureClient(tmp_path / "api"), specs["jake-studio"], NOW)
    assert res["status"] == "offline"
    assert "concluded failure" in res["checks"][1]["detail"]
    assert res["checks"][2]["detail"] == "no published release"


def test_api_outage_is_unknown_not_offline(tmp_path, specs):
    shutil.copytree(FIX, tmp_path / "api")
    f = tmp_path / "api" / fixture_name("repos/mrodgersjs-web/mesh-studio/git/trees/main:scripts")
    f.write_text(json.dumps({"__error__": 503}))
    res = run_agent(FixtureClient(tmp_path / "api"), specs["mesh-studio"], NOW)
    assert res["checks"][0]["ok"] is None
    assert res["status"] == "unknown"


def test_unreachable_repo_is_offline(tmp_path, specs):
    res = run_agent(FixtureClient(tmp_path), specs["doctrine"], NOW)
    assert res["status"] == "offline" and res["checks"][0]["type"] == "repo_reachable"


def test_fixture_client_404():
    with pytest.raises(ApiError):
        FixtureClient(FIX).get("repos/nobody/nothing")


def test_firm_status_rules():
    def mk(*s):
        return [{"status": x} for x in s]

    assert firm_status(mk("online", "online"))["state"] == "online"
    assert firm_status(mk("online", "unknown"))["state"] == "degraded"
    assert firm_status(mk("online", "unknown", "offline"))["state"] == "offline"
    assert firm_status([])["state"] == "no data"


# ---------- outputs ----------

def test_heartbeat_writes_status_badge_dashboard(tmp_path, specs):
    docs = run_heartbeat(FixtureClient(FIX), list(specs.values()), tmp_path, now=NOW)
    assert len(docs) == 7
    files = sorted(p.name for p in (tmp_path / "status").glob("*.json"))
    assert files == sorted(f"{d}.json" for d in specs)
    for f in (tmp_path / "status").glob("*.json"):
        d = json.loads(f.read_text())
        assert d["schema_version"] == 2 and d["checked_at"] == "2026-10-12T15:11:22Z"
    badge = make_badge([json.loads(f.read_text()) for f in (tmp_path / "status").glob("*.json")])
    assert badge == {"schemaVersion": 1, "label": "agent firm", "message": "5/7 online", "color": "red"}


def test_dashboard_is_valid_svg_and_escapes(specs):
    docs = [run_agent(FixtureClient(FIX), s, NOW) for s in specs.values()]
    docs[0]["checks"][0]["detail"] = "<script>&</script>"
    svg = make_dashboard(docs)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "<script>" not in svg
    assert svg.count("<g transform") == 7
    assert "5/7 online" in svg


def test_cli_end_to_end(tmp_path, capsys):
    assert main(["validate", "--agents", str(ROOT / "agents")]) == 0
    rc = main(["heartbeat", "--agents", str(ROOT / "agents"), "--fixtures", str(FIX), "--out", str(tmp_path),
               "--now", "2026-10-12T15:11:22Z"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "firm: offline (5/7 online)" in out
    assert (tmp_path / "badge.json").exists() and (tmp_path / "dashboard.svg").exists()
    assert main(["heartbeat", "--agents", str(ROOT / "agents"), "--fixtures", str(FIX), "--out", str(tmp_path),
                 "--now", "2026-10-12T15:11:22Z", "--fail-on-offline"]) == 1
    # --only keeps other agents' earlier status files in the rendered outputs
    main(["heartbeat", "--agents", str(ROOT / "agents"), "--fixtures", str(FIX), "--out", str(tmp_path),
          "--now", "2026-10-12T15:11:22Z", "--only", "proof-studio"])
    assert json.loads((tmp_path / "badge.json").read_text())["message"] == "5/7 online"


def test_cli_rejects_bad_specs(tmp_path):
    bad = tmp_path / "agents"
    bad.mkdir()
    (bad / "x.yaml").write_text("name: x\n")
    assert main(["validate", "--agents", str(bad)]) == 2
