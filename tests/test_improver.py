import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from agent_firm import improver as imp
from agent_firm.cli import main
from agent_firm.github import FixtureClient, GitHubClient, WriteRefused, assert_write_allowed, fixture_name
from agent_firm.heartbeat import parse_ts
from agent_firm.schema import IMPROVER_PHASES, validate_spec

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "improver"  # live GitHub responses recorded 2026-10-12, pruned
LOOP = ROOT / ".rig" / "loop.yaml"
NOW = parse_ts("2026-10-12T15:11:22Z")
STUDIOS = ["agency-studio", "app-factory-studio", "communications-studio", "design-studio", "jake-studio",
           "mesh-studio", "proof-studio", "strategy-studio"]


@pytest.fixture
def spec():
    return imp.load_improver(ROOT / "agents", LOOP)


def raw():
    return yaml.safe_load((ROOT / "agents" / "improver.yaml").read_text())


# ---------- schema / config ----------

def test_improver_yaml_is_valid_and_complete(spec):
    assert validate_spec(raw()) == []
    assert spec.name == "improver" and spec.repo == "mrodgersjs-web/rig-agent-firm"
    cfg = spec.improver
    assert tuple(cfg["phases"]) == IMPROVER_PHASES == ("measure", "pick", "build", "verify", "ship", "record")
    assert cfg["cadence"] == "nightly"
    assert cfg["merge_policy"] == "human_only" and cfg["auto_merge"] is False
    assert set(cfg["scorecard"]) == set(imp.SCORERS)
    assert cfg["scope"]["labels"] == ["studio", "foundry", "capability"]
    assert "merges stay with the human" in spec.boundary.lower()


@pytest.mark.parametrize("mutate, needle", [
    (lambda b: b.update(auto_merge=True), "auto_merge must be false"),
    (lambda b: b.pop("auto_merge"), "auto_merge must be false"),
    (lambda b: b.update(merge_policy="bot"), "merge_policy must be 'human_only'"),
    (lambda b: b.update(phases=["measure", "pick", "build", "ship", "verify", "record"]), "phases must be exactly"),
    (lambda b: b.update(cadence="hourly"), "cadence must be one of"),
    (lambda b: b.update(scorecard=["ci_smoke", "vibes"]), "unknown dimension 'vibes'"),
    (lambda b: b.update(cron="nightly"), "cron must have 5 fields"),
    (lambda b: b["pick"].update(max_issues_per_night=0), "max_issues_per_night"),
    (lambda b: b["scope"].update(labels=[]), "scope.labels"),
    (lambda b: b.update(extra=1), "unknown key 'extra'"),
])
def test_validator_rejects_bad_improver_block(mutate, needle):
    d = raw()
    mutate(d["improver"])
    assert any(needle in e for e in validate_spec(d)), validate_spec(d)


def test_loop_yaml_must_declare_human_only_merge(tmp_path):
    cfg = yaml.safe_load(LOOP.read_text())
    assert cfg["roles"]["improver"]["merge"] == "human_only" and cfg["roles"]["improver"]["auto_merge"] is False
    for change, needle in [({"auto_merge": True}, "merge: human_only"), ({"merge": "auto"}, "merge: human_only"),
                           ({"phases": ["measure"]}, "phases disagree"), ({"spec": "agents/x.yaml"}, "does not match")]:
        bad = copy.deepcopy(cfg)
        bad["roles"]["improver"].update(change)
        p = tmp_path / "loop.yaml"
        p.write_text(yaml.safe_dump(bad))
        with pytest.raises(imp.ImproverConfigError, match=needle):
            imp.load_improver(ROOT / "agents", p)
    del cfg["roles"]
    (tmp_path / "loop.yaml").write_text(yaml.safe_dump(cfg))
    with pytest.raises(imp.ImproverConfigError, match="not declared"):
        imp.load_improver(ROOT / "agents", tmp_path / "loop.yaml")


def test_work_routing_declares_improver_and_human_merge():
    r = yaml.safe_load((ROOT / ".rig" / "work-routing.yaml").read_text())
    by = {a["name"]: a for a in r["automations"]}
    assert by["improver-measure-pick"]["owner"] == "plain_python"
    assert by["improver-merge"]["owner"] == "human_owner"


# ---------- never auto-merge, enforced in code ----------

@pytest.mark.parametrize("method, path", [
    ("PUT", "repos/o/r/pulls/7/merge"),
    ("POST", "repos/o/r/pulls/7/merge"),
    ("POST", "repos/o/r/merges"),
    ("POST", "graphql"),  # enablePullRequestAutoMerge lives here
    ("POST", "repos/o/r/pulls/7/reviews"),  # self-approval
    ("PUT", "repos/o/r/pulls/7/update-branch"),
    ("POST", "repos/o/r/pulls"),
    ("PATCH", "repos/o/r"),
    ("DELETE", "repos/o/r/git/refs/heads/main"),
    ("POST", "repos/o/r/git/refs"),
    ("POST", "repos/o/r/releases"),
])
def test_write_allow_list_refuses_merges_and_everything_else(method, path):
    with pytest.raises(WriteRefused):
        assert_write_allowed(method, path)


def test_only_issue_creation_is_allowed_and_refusal_happens_before_network():
    assert assert_write_allowed("POST", "repos/o/r/issues") is None
    live = GitHubClient(token="", base="http://127.0.0.1:9")  # unroutable: proves no request is attempted
    with pytest.raises(WriteRefused):
        live.post("repos/o/r/pulls/1/merge", {})
    with pytest.raises(WriteRefused):
        FixtureClient(FIX).post("graphql", {"query": "mutation{enablePullRequestAutoMerge}"})


def test_pick_refuses_to_run_if_auto_merge_was_switched_on(spec):
    card = imp.measure(FixtureClient(FIX), spec, now=NOW)
    spec.improver = {**spec.improver, "auto_merge": True}
    with pytest.raises(imp.ImproverConfigError, match="auto_merge"):
        imp.pick(card, spec)


# ---------- MEASURE on recorded live data ----------

def test_scope_matching():
    assert imp.in_scope({"name": "rig-capabilities"}, ["capability"]) == ["capability"]
    assert imp.in_scope({"name": "design-studio"}, ["studio", "foundry"]) == ["studio"]
    assert imp.in_scope({"name": "x", "topics": ["foundry"]}, ["foundry"]) == ["foundry"]
    assert imp.in_scope({"name": "studiolike"}, ["studio"]) == []


def test_measure_discovers_and_scores_real_repos(spec):
    card = imp.measure(FixtureClient(FIX), spec, now=NOW)
    assert card["schema"] == imp.SCORECARD_SCHEMA and card["generated_at"] == "2026-10-12T15:11:22Z"
    assert [r["repo"].split("/")[1] for r in card["repos"]] == STUDIOS
    assert card["partial"] is False and card["auto_merge"] is False
    by = {r["repo"].split("/")[1]: r for r in card["repos"]}
    a = by["agency-studio"]["dimensions"]
    assert a["release_cadence"]["score"] == 0 and a["release_cadence"]["parts"][0]["detail"] == "no published releases"
    assert a["ci_smoke"]["score"] == 100
    assert a["security_basics"]["score"] == 30  # secret scanning on; no dependabot, no CodeQL
    assert by["agency-studio"]["overall"] == 72.5
    assert by["jake-studio"]["dimensions"]["release_cadence"]["score"] == 100
    md = imp.scorecard_markdown(card)
    assert "| [agency-studio](https://github.com/mrodgersjs-web/agency-studio) | 72.5 |" in md


def test_api_outage_is_unknown_not_a_score(tmp_path, spec):
    shutil.copytree(FIX, tmp_path / "api")
    (tmp_path / "api" / fixture_name("repos/mrodgersjs-web/mesh-studio/readme")).write_text('{"__error__": 503}')
    card = imp.measure(FixtureClient(tmp_path / "api"), spec, now=NOW)
    mesh = next(r for r in card["repos"] if r["repo"].endswith("/mesh-studio"))
    assert mesh["dimensions"]["hero_image"]["score"] is None
    assert mesh["partial"] is True and card["partial"] is True
    assert mesh["api_errors"]["readme"] == "HTTP 503"


# ---------- PICK ----------

def _row(repo, **scores):
    dims = {d: {"score": scores.get(d, 100), "parts": [{"check": "x", "max": 100, "earned": scores.get(d, 100),
                                                         "detail": "d"}], "unknown": []} for d in imp.SCORERS}
    return {"repo": repo, "dimensions": dims, "open_improver_issues": [], "overall": 0}


def test_lowest_dimension_logic():
    dims = list(imp.SCORERS)
    assert imp.lowest_dimension(_row("o/a", hero_image=40, issue_age=60), dims) == ("hero_image", 40)
    # ties go to the dimension listed first in the spec
    assert imp.lowest_dimension(_row("o/a", issue_age=10, tests_coverage=10), dims) == ("tests_coverage", 10)
    # unknown dimensions are never picked
    r = _row("o/a", ci_smoke=None, readme_quality=90)
    assert imp.lowest_dimension(r, dims) == ("readme_quality", 90)
    assert imp.lowest_dimension(_row("o/a"), dims) is None


def test_pick_ranks_across_repos_and_opens_at_most_one(spec):
    card = {"generated_at": "2026-10-12T00:00:00Z", "dimensions": list(imp.SCORERS), "repos": [
        _row("o/a", hero_image=40), _row("o/b", security_basics=10), _row("o/c"), _row("o/d", issue_age=5)]}
    card["repos"][3]["open_improver_issues"] = ["https://github.com/o/d/issues/1"]
    res = imp.pick(card, spec)
    assert [(c["repo"], c["dimension"]) for c in res["chosen"]] == [("o/b", "security_basics")]
    assert [c["repo"] for c in res["queued"]] == ["o/a"]
    assert {s["repo"] for s in res["skipped"]} == {"o/c", "o/d"}
    assert res["dry_run"] and res["opened"] == []
    c = res["chosen"][0]
    assert c["branch"] == "improver/security-basics-2026-10-12" and c["labels"] == ["improver"]
    assert "Merges stay with the human owner" in c["body"]
    client = FixtureClient(FIX)
    res = imp.pick(card, spec, client=client, open_issue=True)
    assert [p for p, _ in client.posted] == ["repos/o/b/issues"]
    assert client.posted[0][1]["title"] == "improver: raise security basics from 10/100"


def test_pick_on_real_scorecard(spec):
    res = imp.pick(imp.measure(FixtureClient(FIX), spec, now=NOW), spec)
    assert [(c["repo"], c["dimension"], c["score"]) for c in res["chosen"]] == [
        ("mrodgersjs-web/agency-studio", "release_cadence", 0)]
    assert len(res["queued"]) == 7


# ---------- VERIFY / RECORD ----------

def _pr_fixtures(d: Path, auto_merge=None, approve=True, conclusion="success"):
    d.mkdir(parents=True, exist_ok=True)
    put = {
        "repos/o/r/pulls/5": {"html_url": "https://github.com/o/r/pull/5", "state": "open", "merged": False,
                              "head": {"sha": "abc"}, "user": {"login": "improver-bot"}, "auto_merge": auto_merge},
        "repos/o/r/commits/abc/check-runs?per_page=100": {"check_runs": [
            {"name": "test", "status": "completed", "conclusion": conclusion},
            {"name": "actionlint", "status": "completed", "conclusion": "success"}]},
        "repos/o/r/pulls/5/reviews?per_page=100": [
            {"user": {"login": "improver-bot"}, "state": "APPROVED"},  # self-approval never counts
            *([{"user": {"login": "mike"}, "state": "APPROVED"}] if approve else [])],
    }
    for path, body in put.items():
        (d / fixture_name(path)).write_text(json.dumps(body))
    return FixtureClient(d)


def test_verify_pr_needs_ci_and_independent_review(tmp_path):
    ok = imp.verify_pr(_pr_fixtures(tmp_path / "a"), "o/r", 5)
    assert ok["ready_to_ship"] and ok["ci"] == "pass" and ok["approved_by"] == ["mike"]
    assert ok["ship"] == "merge stays with the human owner"
    no_review = imp.verify_pr(_pr_fixtures(tmp_path / "b", approve=False), "o/r", 5)
    assert not no_review["ready_to_ship"] and "no approving review" in no_review["problems"][0]
    red = imp.verify_pr(_pr_fixtures(tmp_path / "c", conclusion="failure"), "o/r", 5)
    assert not red["ready_to_ship"] and red["ci"] == "fail"
    auto = imp.verify_pr(_pr_fixtures(tmp_path / "d", auto_merge={"merge_method": "squash"}), "o/r", 5)
    assert not auto["ready_to_ship"] and any("auto-merge is enabled" in p for p in auto["problems"])


def test_record_receipt(spec):
    before = imp.measure(FixtureClient(FIX), spec, now=NOW)
    after = copy.deepcopy(before)
    after["generated_at"] = "2026-10-13T08:17:00Z"
    row = after["repos"][0]
    row["dimensions"]["release_cadence"]["score"] = 100
    row["overall"] = 85.0
    rc = imp.record(before, after)
    changed = [r for r in rc["repos"] if r["changes"]]
    assert len(changed) == 1 and changed[0]["changes"] == [
        {"dimension": "release_cadence", "before": 0, "after": 100, "delta": 100}]
    assert changed[0]["overall_delta"] == 12.5
    assert "| mrodgersjs-web/agency-studio | release_cadence | 0 | 100 | 100 |" in imp.receipt_markdown(rc)


# ---------- CLI ----------

def test_cli_improver_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    out = tmp_path / "out"
    assert main(["improver", "measure", "--fixtures", str(FIX), "--out", str(out), "--now", "2026-10-12T15:11:22Z"]) == 0
    card = json.loads((out / "scorecard.json").read_text())
    assert len(card["repos"]) == 8 and (out / "scorecard.md").exists()
    assert main(["improver", "pick", "--scorecard", str(out / "scorecard.json"), "--out", str(out)]) == 0
    picks = json.loads((out / "picks.json").read_text())
    assert picks["dry_run"] and len(picks["chosen"]) == 1
    assert main(["improver", "record", "--before", str(out / "scorecard.json"), "--after", str(out / "scorecard.json"),
                 "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert "scored 8 repos" in text and "1 chosen, 7 queued" in text and "0 score changes" in text


def test_cli_measure_partial_exits_3(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    shutil.copytree(FIX, tmp_path / "api")
    (tmp_path / "api" / fixture_name("repos/mrodgersjs-web/jake-studio/releases?per_page=10")).write_text(
        '{"__error__": 429}')
    args = ["improver", "measure", "--fixtures", str(tmp_path / "api"), "--out", str(tmp_path / "o"),
            "--now", "2026-10-12T15:11:22Z"]
    assert main(args) == 3
    assert main([*args, "--allow-partial"]) == 0
