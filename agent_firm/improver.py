"""Improver: the nightly continuous-improvement loop (agents/improver.yaml).

Cycle: MEASURE -> PICK -> BUILD -> VERIFY -> SHIP -> RECORD.

- MEASURE (``measure``): score every in-scope repo on the dimensions in the spec, from
  live GitHub REST data, into ``scorecard.json`` and ``scorecard.md``.
- PICK (``pick``): take each repo's lowest-scoring dimension, rank across the firm, and
  draft one scoped issue per pick (opened only with ``open_issue=True``).
- BUILD: a coding agent opens a PR on the issue's branch. This module does not write code.
- VERIFY (``verify_pr``): the PR needs green checks and an approving review by someone else.
- SHIP: the human owner merges. Nothing here can merge; the GitHub client refuses merge,
  review and auto-merge endpoints, and a spec or loop.yaml that allows auto-merge is rejected.
- RECORD (``record``): diff two scorecards into a measured before/after receipt.

Every score is made of parts. A part the API could not answer is ``None`` (unknown) and is
left out of the score instead of being counted as a pass or a failure.
"""

from __future__ import annotations

import base64
import json
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .github import ApiError
from .heartbeat import Client, iso, parse_ts
from .schema import IMPROVER_DIMENSIONS, AgentSpec, load_specs

SCORECARD_SCHEMA = "agent-firm.improver.scorecard.v1"
RECEIPT_SCHEMA = "agent-firm.improver.receipt.v1"
OK_CONCLUSIONS = {"success", "skipped", "neutral"}
TRANSIENT = {0, 429}  # plus every 5xx: the API did not answer, so the scorecard is partial

LABELS = {
    "ci_smoke": "CI green + smoke test",
    "tests_coverage": "tests and coverage",
    "stranger_time": "stranger time-to-working",
    "readme_quality": "README quality",
    "hero_image": "hero image",
    "release_cadence": "release cadence",
    "issue_age": "open-issue age",
    "security_basics": "security basics",
}


class ImproverConfigError(Exception):
    pass


# ---------------------------------------------------------------- config


def load_improver(agents: str | Path = "agents", loop: str | Path = ".rig/loop.yaml") -> AgentSpec:
    """Return the one spec with an ``improver`` block, cross-checked against ``.rig/loop.yaml``."""
    specs, errors = load_specs(agents)
    if errors:
        raise ImproverConfigError("; ".join(errors))
    found = [s for s in specs if s.improver is not None]
    if len(found) != 1:
        raise ImproverConfigError(f"expected exactly one spec with an 'improver' block, found {len(found)}")
    spec = found[0]
    loop_path = Path(loop)
    if not loop_path.exists():
        raise ImproverConfigError(f"{loop_path}: missing; the improver role must be declared there")
    cfg = yaml.safe_load(loop_path.read_text()) or {}
    role = (cfg.get("roles") or {}).get("improver")
    if not isinstance(role, dict):
        raise ImproverConfigError(f"{loop_path}: roles.improver is not declared")
    if role.get("merge") != "human_only" or role.get("auto_merge") is not False:
        raise ImproverConfigError(f"{loop_path}: roles.improver must set merge: human_only and auto_merge: false")
    if Path(role.get("spec", "")).name != Path(spec.path).name:
        raise ImproverConfigError(f"{loop_path}: roles.improver.spec {role.get('spec')!r} does not match {spec.path}")
    if role.get("cadence") != spec.improver["cadence"]:
        raise ImproverConfigError(f"{loop_path}: roles.improver.cadence disagrees with {spec.path}")
    if list(role.get("phases") or []) != list(spec.improver["phases"]):
        raise ImproverConfigError(f"{loop_path}: roles.improver.phases disagree with {spec.path}")
    return spec


# ---------------------------------------------------------------- scope


def _tokens(name: str) -> set[str]:
    out = set()
    for t in re.split(r"[-_.]+", name.lower()):
        if not t:
            continue
        out.add(t)
        if t.endswith("ies"):
            out.add(t[:-3] + "y")
        elif t.endswith("s"):
            out.add(t[:-1])
    return out


def in_scope(repo: dict[str, Any], labels: list[str]) -> list[str]:
    """Return the labels that put *repo* in scope (topics or name tokens); empty = out of scope."""
    want = {x.lower() for x in labels}
    topics = {t.lower() for t in repo.get("topics") or []}
    return sorted(want & (topics | _tokens(repo.get("name", ""))))


def discover_repos(client: Client, scope: dict[str, Any]) -> list[dict[str, Any]]:
    owner = scope["owner"]
    base = (
        "user/repos?visibility=all&affiliation=owner&sort=full_name"
        if scope.get("visibility", "public") == "all"
        else f"users/{owner}/repos?type=owner&sort=full_name"
    )
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        batch = client.get(f"{base}&per_page=100&page={page}")
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    picked = []
    for r in repos:
        if r.get("archived") or r.get("fork"):
            continue
        if not r.get("full_name", "").startswith(owner + "/"):
            continue
        if scope.get("visibility", "public") == "public" and r.get("private"):
            continue
        why = in_scope(r, scope["labels"])
        if why:
            picked.append({**r, "_matched": why})
    return sorted(picked, key=lambda r: r["name"])


# ---------------------------------------------------------------- facts


@dataclass
class Facts:
    full_name: str
    meta: dict[str, Any]
    branch: str
    paths: list[str] | None = None
    readme: str | None = None
    runs: list[dict[str, Any]] | None = None
    releases: list[dict[str, Any]] | None = None
    issues: list[dict[str, Any]] | None = None
    codeql_default: str | None = None
    errors: dict[str, str] = field(default_factory=dict)
    transient: bool = False


def _fetch(f: Facts, key: str, fn):
    try:
        return fn()
    except ApiError as exc:
        f.errors[key] = f"HTTP {exc.status}"
        if exc.status in TRANSIENT or exc.status >= 500:
            f.transient = True
        return exc


def gather(client: Client, meta: dict[str, Any]) -> Facts:
    repo = meta["full_name"]
    if "security_and_analysis" not in meta:  # list endpoints omit it; the repo endpoint shows it to admins
        full = _fetch(Facts(repo, meta, ""), "meta", lambda: client.get(f"repos/{repo}"))
        if not isinstance(full, Exception):
            meta = {**full, **{k: v for k, v in meta.items() if k.startswith("_")}}
    f = Facts(full_name=repo, meta=meta, branch=meta.get("default_branch") or "main")

    tree = _fetch(f, "tree", lambda: client.get(f"repos/{repo}/git/trees/{f.branch}?recursive=1"))
    if not isinstance(tree, Exception):
        f.paths = [e["path"] for e in tree.get("tree", []) if e.get("type") == "blob"]
    elif tree.status == 409:  # empty repository
        f.paths = []

    rd = _fetch(f, "readme", lambda: client.get(f"repos/{repo}/readme"))
    if not isinstance(rd, Exception):
        f.readme = base64.b64decode(rd.get("content", "")).decode("utf-8", errors="replace")
    elif rd.status == 404:
        f.readme = ""

    runs = _fetch(f, "runs", lambda: client.get(
        f"repos/{repo}/actions/runs?branch={f.branch}&status=completed&per_page=50"))
    if not isinstance(runs, Exception):
        f.runs = runs.get("workflow_runs", [])

    rel = _fetch(f, "releases", lambda: client.get(f"repos/{repo}/releases?per_page=10"))
    if not isinstance(rel, Exception):
        f.releases = [r for r in rel if not r.get("draft")]

    if meta.get("has_issues", True):
        iss = _fetch(f, "issues", lambda: client.get(f"repos/{repo}/issues?state=open&per_page=100"))
        if not isinstance(iss, Exception):
            f.issues = [i for i in iss if "pull_request" not in i]

    if f.paths is not None and not any(_is_codeql(p) for p in f.paths):
        cq = _fetch(f, "codeql_default", lambda: client.get(f"repos/{repo}/code-scanning/default-setup"))
        if not isinstance(cq, Exception):
            f.codeql_default = cq.get("state", "")
        f.errors.pop("codeql_default", None)  # 403/404 is normal without security_events scope: unknown
    return f


# ---------------------------------------------------------------- scoring


def _part(name: str, max_points: int, earned: float | None, detail: str) -> dict[str, Any]:
    return {"check": name, "max": max_points, "earned": earned, "detail": detail}


def _dim(parts: list[dict[str, Any]]) -> dict[str, Any]:
    known = [p for p in parts if p["earned"] is not None]
    total = sum(p["max"] for p in known)
    score = round(100 * sum(p["earned"] for p in known) / total, 1) if total else None
    return {"score": score, "parts": parts, "unknown": [p["check"] for p in parts if p["earned"] is None]}


def _is_codeql(p: str) -> bool:
    return p.startswith(".github/workflows/") and "codeql" in p.lower()


TEST_RE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)__tests__/")
COVERAGE_FILES = {".coveragerc", "codecov.yml", ".codecov.yml", ".nycrc", ".nycrc.json", "coverage.yml"}
MANIFESTS = {"pyproject.toml", "setup.py", "package.json", "Cargo.toml", "go.mod", "requirements.txt",
             "Makefile", "Dockerfile", "Gemfile", "pom.xml", "build.gradle"}
QUICKSTART_RE = re.compile(r"^#{1,6}\s*.*\b(quick ?start|getting started|install(ation)?|usage|setup|run|try it|demo|"
                           r"in \d+ (seconds|minutes))\b",
                           re.IGNORECASE | re.MULTILINE)
CMD_RE = re.compile(r"```[^\n]*\n(?:(?!```).)*?(pip install|uv |npm |npx |pnpm |yarn |make |docker |cargo |go run|"
                    r"go install|bash |\./|python3? |node |bun |deno |git clone|brew install)", re.DOTALL)
IMG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)|!\[[^\]]*\]\(([^)\s]+)", re.IGNORECASE)
BADGE_RE = re.compile(r"shields\.io|/badge\.svg|badge|/actions/workflows/|codecov\.io|img\.shields", re.IGNORECASE)


def score_ci_smoke(f: Facts, now: datetime) -> dict[str, Any]:
    if f.runs is None:
        ci = _part("ci_green", 60, None, f"workflow runs unreadable ({f.errors.get('runs')})")
        smoke_run = None
    else:
        latest: dict[str, dict[str, Any]] = {}
        for r in sorted(f.runs, key=lambda r: r.get("updated_at") or "", reverse=True):
            latest.setdefault(str(r.get("workflow_id") or r.get("name")), r)
        if not latest:
            ci = _part("ci_green", 60, 0, f"no completed workflow runs on {f.branch}")
        else:
            bad = sorted(f"{r.get('name')}={r.get('conclusion')}" for r in latest.values()
                         if r.get("conclusion") not in OK_CONCLUSIONS)
            ci = (_part("ci_green", 60, 0, f"latest run failing on {f.branch}: {', '.join(bad)}") if bad else
                  _part("ci_green", 60, 60, f"latest run of all {len(latest)} workflows on {f.branch} passed"))
        smoke_run = next((r for r in latest.values() if "smoke" in (r.get("name") or "").lower()
                          or "smoke" in (r.get("path") or "").lower()), None)
    if f.paths is None:
        smoke = _part("smoke_test", 40, None, f"tree unreadable ({f.errors.get('tree')})")
    else:
        has = "scripts/smoke.sh" in f.paths or any(
            p.startswith(".github/workflows/") and "smoke" in p.lower() for p in f.paths)
        if not has:
            smoke = _part("smoke_test", 40, 0, "no scripts/smoke.sh or smoke workflow")
        elif smoke_run is not None and smoke_run.get("conclusion") not in OK_CONCLUSIONS:
            smoke = _part("smoke_test", 40, 0, f"smoke workflow last concluded {smoke_run.get('conclusion')}")
        else:
            smoke = _part("smoke_test", 40, 40, "smoke test present" + (" and its last run passed" if smoke_run else ""))
    return _dim([ci, smoke])


def score_tests_coverage(f: Facts, now: datetime) -> dict[str, Any]:
    if f.paths is None:
        u = f"tree unreadable ({f.errors.get('tree')})"
        return _dim([_part("tests_present", 50, None, u), _part("tests_depth", 20, None, u),
                     _part("coverage_config", 30, None, u)])
    tests = [p for p in f.paths if TEST_RE.search(p)]
    cov = [p for p in f.paths if p.rsplit("/", 1)[-1] in COVERAGE_FILES or
           (p.startswith(".github/workflows/") and "coverage" in p.lower())]
    return _dim([
        _part("tests_present", 50, 50 if tests else 0, f"{len(tests)} test files"),
        _part("tests_depth", 20, 20 if len(tests) >= 5 else 0, f"{len(tests)} test files (5+ earns depth)"),
        _part("coverage_config", 30, 30 if cov else 0, f"coverage config: {', '.join(cov[:3]) or 'none found'}"),
    ])


def score_stranger_time(f: Facts, now: datetime) -> dict[str, Any]:
    """Static proxy for stranger time-to-working: can a newcomer find how to install and run it?"""
    if f.readme is None:
        u = f"README unreadable ({f.errors.get('readme')})"
        qs, cmd = _part("quickstart_section", 35, None, u), _part("runnable_command", 35, None, u)
    else:
        qs = _part("quickstart_section", 35, 35 if QUICKSTART_RE.search(f.readme) else 0,
                   "README has a quick start / install / usage heading" if QUICKSTART_RE.search(f.readme)
                   else "no quick start, install or usage heading in README")
        cmd = _part("runnable_command", 35, 35 if CMD_RE.search(f.readme) else 0,
                    "README has a fenced install/run command" if CMD_RE.search(f.readme)
                    else "no fenced install/run command in README")
    if f.paths is None:
        man = _part("manifest", 30, None, f"tree unreadable ({f.errors.get('tree')})")
    else:
        found = sorted(p for p in f.paths if p.count("/") <= 2 and p.rsplit("/", 1)[-1] in MANIFESTS)
        found.sort(key=lambda p: (p.count("/"), p))
        man = _part("manifest", 30, 30 if found else 0, f"install manifest: {', '.join(found[:3]) or 'none'}")
    return _dim([qs, cmd, man])


def score_readme_quality(f: Facts, now: datetime) -> dict[str, Any]:
    m = f.meta
    meta_parts = [
        _part("description", 15, 15 if (m.get("description") or "").strip() else 0,
              "repo description set" if (m.get("description") or "").strip() else "repo description empty"),
        _part("license", 15, 15 if m.get("license") else 0,
              f"license: {(m.get('license') or {}).get('spdx_id', 'none')}"),
        _part("topics", 15, 15 if len(m.get("topics") or []) >= 3 else 0, f"{len(m.get('topics') or [])} topics (3+)"),
    ]
    if f.readme is None:
        u = f"README unreadable ({f.errors.get('readme')})"
        return _dim([_part("length", 30, None, u), _part("headings", 25, None, u), *meta_parts])
    heads = len(re.findall(r"^#{1,6}\s+\S", f.readme, re.MULTILINE)) + len(re.findall(r"<h[1-6][ >]", f.readme, re.IGNORECASE))
    n = len(f.readme)
    return _dim([
        _part("length", 30, 30 if n >= 1500 else round(30 * n / 1500, 1), f"README {n} chars (1500+ full)"),
        _part("headings", 25, 25 if heads >= 3 else 0, f"{heads} headings (3+)"),
        *meta_parts,
    ])


def score_hero_image(f: Facts, now: datetime) -> dict[str, Any]:
    if f.readme is None:
        return _dim([_part("hero_in_readme", 100, None, f"README unreadable ({f.errors.get('readme')})")])
    imgs = [a or b for a, b in IMG_RE.findall(f.readme)]
    hero = [u for u in imgs if not BADGE_RE.search(u)]
    if hero:
        return _dim([_part("hero_in_readme", 100, 100, f"README shows {hero[0]}")])
    files = [p for p in (f.paths or []) if re.search(r"hero|banner|cover", p, re.IGNORECASE)
             and re.search(r"\.(png|jpe?g|gif|svg|webp)$", p, re.IGNORECASE)]
    if files:
        return _dim([_part("hero_in_readme", 100, 50, f"{files[0]} exists but the README does not show it")])
    return _dim([_part("hero_in_readme", 100, 0, "no hero image in README (badges do not count)")])


def score_release_cadence(f: Facts, now: datetime) -> dict[str, Any]:
    if f.releases is None:
        return _dim([_part("latest_release_age", 100, None, f"releases unreadable ({f.errors.get('releases')})")])
    if not f.releases:
        return _dim([_part("latest_release_age", 100, 0, "no published releases")])
    latest = max(f.releases, key=lambda r: r.get("published_at") or r.get("created_at") or "")
    ts = latest.get("published_at") or latest.get("created_at")
    days = (now - parse_ts(ts)).total_seconds() / 86400
    pts = 100 if days <= 30 else 70 if days <= 90 else 40 if days <= 180 else 15
    return _dim([_part("latest_release_age", 100, pts, f"latest {latest.get('tag_name')} is {days:.0f} days old "
                       f"({len(f.releases)} in last page)")])


def score_issue_age(f: Facts, now: datetime) -> dict[str, Any]:
    if not f.meta.get("has_issues", True):
        return _dim([_part("open_issue_age", 100, None, "issues are disabled on this repo")])
    if f.issues is None:
        return _dim([_part("open_issue_age", 100, None, f"issues unreadable ({f.errors.get('issues')})")])
    if not f.issues:
        return _dim([_part("open_issue_age", 100, 100, "no open issues")])
    ages = [(now - parse_ts(i["created_at"])).total_seconds() / 86400 for i in f.issues]
    med = statistics.median(ages)
    pts = 100 if med <= 14 else 80 if med <= 30 else 50 if med <= 90 else 20
    return _dim([_part("open_issue_age", 100, pts,
                       f"{len(ages)} open issues, median {med:.0f} days, oldest {max(ages):.0f} days")])


def score_security_basics(f: Facts, now: datetime) -> dict[str, Any]:
    if f.paths is None:
        u = f"tree unreadable ({f.errors.get('tree')})"
        dep = _part("dependabot", 35, None, u)
        cq = _part("codeql", 35, None, u)
    else:
        has_dep = any(p in (".github/dependabot.yml", ".github/dependabot.yaml") for p in f.paths)
        dep = _part("dependabot", 35, 35 if has_dep else 0,
                    ".github/dependabot.yml present" if has_dep else "no .github/dependabot.yml")
        wf = [p for p in f.paths if _is_codeql(p)]
        if wf:
            cq = _part("codeql", 35, 35, f"CodeQL workflow {wf[0]}")
        elif f.codeql_default == "configured":
            cq = _part("codeql", 35, 35, "CodeQL default setup configured")
        elif f.codeql_default is not None:
            cq = _part("codeql", 35, 0, f"no CodeQL workflow; default setup is {f.codeql_default or 'off'}")
        else:
            cq = _part("codeql", 35, None, "no CodeQL workflow; default setup not readable with this token")
    sa = f.meta.get("security_and_analysis")
    if not isinstance(sa, dict):
        ss = _part("secret_scanning", 30, None, "secret scanning status not visible with this token")
    else:
        st = (sa.get("secret_scanning") or {}).get("status")
        ss = _part("secret_scanning", 30, 30 if st == "enabled" else 0, f"secret scanning {st or 'not reported'}")
    return _dim([dep, cq, ss])


SCORERS = {
    "ci_smoke": score_ci_smoke,
    "tests_coverage": score_tests_coverage,
    "stranger_time": score_stranger_time,
    "readme_quality": score_readme_quality,
    "hero_image": score_hero_image,
    "release_cadence": score_release_cadence,
    "issue_age": score_issue_age,
    "security_basics": score_security_basics,
}
assert set(SCORERS) == set(IMPROVER_DIMENSIONS)


def score_repo(f: Facts, dims: list[str], now: datetime, issue_label: str = "improver") -> dict[str, Any]:
    scores = {d: SCORERS[d](f, now) for d in dims}
    known = [v["score"] for v in scores.values() if v["score"] is not None]
    open_improver = [i.get("html_url", "") for i in (f.issues or [])
                     if issue_label in {lb.get("name") for lb in i.get("labels") or []}]
    return {
        "repo": f.full_name,
        "html_url": f.meta.get("html_url", f"https://github.com/{f.full_name}"),
        "default_branch": f.branch,
        "matched": f.meta.get("_matched", []),
        "overall": round(sum(known) / len(known), 1) if known else None,
        "dimensions": scores,
        "open_improver_issues": open_improver,
        "api_errors": f.errors,
        "partial": f.transient,
    }


# ---------------------------------------------------------------- MEASURE


def measure(client: Client, spec: AgentSpec, now: datetime | None = None,
            repos: list[str] | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = spec.improver
    scope = cfg["scope"]
    if repos:
        metas = []
        for r in repos:
            full = r if "/" in r else f"{scope['owner']}/{r}"
            m = client.get(f"repos/{full}")
            metas.append({**m, "_matched": in_scope(m, scope["labels"]) or ["explicit"]})
    else:
        metas = discover_repos(client, scope)
    rows = [score_repo(gather(client, m), cfg["scorecard"], now, cfg["pick"]["issue_label"]) for m in metas]
    return {
        "schema": SCORECARD_SCHEMA,
        "agent": spec.name,
        "generated_at": iso(now),
        "scope": {"owner": scope["owner"], "labels": scope["labels"], "visibility": scope.get("visibility", "public")},
        "dimensions": cfg["scorecard"],
        "merge_policy": cfg["merge_policy"],
        "auto_merge": cfg["auto_merge"],
        "repos": rows,
        "partial": any(r["partial"] for r in rows),
    }


def _fmt(v: float | None) -> str:
    return "?" if v is None else f"{v:g}"


def scorecard_markdown(card: dict[str, Any]) -> str:
    dims = card["dimensions"]
    lines = [
        f"# Improver scorecard — {card['generated_at']}",
        "",
        f"Scope: `{card['scope']['owner']}` repos labeled {', '.join(card['scope']['labels'])} "
        f"({card['scope']['visibility']}) · {len(card['repos'])} repos · merges: {card['merge_policy']}"
        + (" · **PARTIAL: some API calls failed**" if card["partial"] else ""),
        "",
        "| repo | overall | " + " | ".join(dims) + " |",
        "| :-- | --: | " + " | ".join("--:" for _ in dims) + " |",
    ]
    for r in card["repos"]:
        name = r["repo"].split("/", 1)[1]
        lines.append(f"| [{name}]({r['html_url']}) | {_fmt(r['overall'])} | "
                     + " | ".join(_fmt(r["dimensions"][d]["score"]) for d in dims) + " |")
    lines += ["", "`?` = the API could not tell (left out of the score, never counted as a pass).", ""]
    for r in card["repos"]:
        misses = [f"{d}/{p['check']}: {p['detail']}" for d in dims for p in r["dimensions"][d]["parts"]
                  if p["earned"] is not None and p["earned"] < p["max"]]
        if misses:
            lines.append(f"- **{r['repo']}**: " + "; ".join(misses))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- PICK


def lowest_dimension(row: dict[str, Any], dims: list[str]) -> tuple[str, float] | None:
    """Lowest known score; ties go to the dimension listed first in the spec. None if all 100 or unknown."""
    cands = [(row["dimensions"][d]["score"], i, d) for i, d in enumerate(dims)
             if row["dimensions"].get(d, {}).get("score") is not None]
    if not cands:
        return None
    score, _, dim = min(cands)
    return None if score >= 100 else (dim, score)


def issue_draft(row: dict[str, Any], dim: str, score: float, label: str, date: str) -> dict[str, Any]:
    d = row["dimensions"][dim]
    misses = [p for p in d["parts"] if p["earned"] is not None and p["earned"] < p["max"]]
    branch = f"improver/{dim.replace('_', '-')}-{date}"
    body = "\n".join([
        (f"The nightly Improver scorecard ({date}) scored **{LABELS[dim]}** at **{_fmt(score)}/100**, "
         "the lowest dimension for this repo."),
        "",
        "Measured parts:",
        *[f"- {p['check']}: {_fmt(p['earned'])}/{p['max']} — {p['detail']}" for p in d["parts"]],
        "",
        "Acceptance (scoped to this one dimension):",
        *[f"- [ ] {p['check']}: earn the full {p['max']} points (now {_fmt(p['earned'])}: {p['detail']})"
          for p in misses],
        "- [ ] CI is green on the PR and at least one review approves it",
        "- [ ] The next scorecard shows the new score (before/after receipt from `agent-firm improver record`)",
        "",
        f"Suggested branch: `{branch}`.",
        "",
        ("Merges stay with the human owner: the Improver opens this issue and a PR only. "
         "It never merges, never enables auto-merge and never approves its own PR."),
    ])
    return {
        "repo": row["repo"],
        "dimension": dim,
        "score": score,
        "branch": branch,
        "title": f"improver: raise {LABELS[dim]} from {_fmt(score)}/100",
        "body": body,
        "labels": [label],
    }


def pick(card: dict[str, Any], spec: AgentSpec, client: Client | None = None,
         open_issue: bool = False) -> dict[str, Any]:
    cfg = spec.improver
    if cfg["merge_policy"] != "human_only" or cfg["auto_merge"] is not False:
        raise ImproverConfigError("refusing to pick: the spec must keep merge_policy human_only and auto_merge false")
    dims = card["dimensions"]
    label = cfg["pick"]["issue_label"]
    date = card["generated_at"][:10]
    candidates, skipped = [], []
    for row in card["repos"]:
        low = lowest_dimension(row, dims)
        if low is None:
            skipped.append({"repo": row["repo"], "reason": "every known dimension is 100 or unknown"})
            continue
        if row["open_improver_issues"]:
            skipped.append({"repo": row["repo"], "reason": f"improver issue already open: {row['open_improver_issues'][0]}"})
            continue
        candidates.append(issue_draft(row, low[0], low[1], label, date))
    order = {d: i for i, d in enumerate(dims)}
    candidates.sort(key=lambda c: (c["score"], order[c["dimension"]], c["repo"]))
    chosen = candidates[: cfg["pick"]["max_issues_per_night"]]
    opened = []
    if open_issue:
        if client is None:
            raise ImproverConfigError("--open-issue needs a GitHub client")
        for c in chosen:
            res = client.post(f"repos/{c['repo']}/issues",
                              {"title": c["title"], "body": c["body"], "labels": c["labels"]})
            opened.append({"repo": c["repo"], "number": res.get("number"), "html_url": res.get("html_url")})
    return {"generated_at": card["generated_at"], "chosen": chosen, "queued": candidates[len(chosen):],
            "skipped": skipped, "opened": opened, "dry_run": not open_issue}


def picks_markdown(p: dict[str, Any]) -> str:
    lines = [f"# Improver picks — {p['generated_at']}", "",
             "Dry run: nothing was opened." if p["dry_run"] else f"Opened {len(p['opened'])} issue(s).", ""]
    for c in p["chosen"]:
        lines += [f"## {c['repo']} — {c['title']}", "", f"Branch: `{c['branch']}`", "", c["body"], ""]
    if p["queued"]:
        lines += ["## Queued for later nights", ""]
        lines += [f"- {c['repo']}: {c['dimension']} {_fmt(c['score'])}" for c in p["queued"]]
    if p["skipped"]:
        lines += ["", "## Skipped", ""] + [f"- {s['repo']}: {s['reason']}" for s in p["skipped"]]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- VERIFY / SHIP


def verify_pr(client: Client, repo: str, number: int) -> dict[str, Any]:
    """Read-only. A PR is ready to SHIP when checks pass and someone other than the author approved.
    The merge itself is the human owner's; this function never merges."""
    pr = client.get(f"repos/{repo}/pulls/{number}")
    sha = pr["head"]["sha"]
    runs = client.get(f"repos/{repo}/commits/{sha}/check-runs?per_page=100").get("check_runs", [])
    pending = [r["name"] for r in runs if r.get("status") != "completed"]
    failed = [f"{r['name']}={r.get('conclusion')}" for r in runs
              if r.get("status") == "completed" and r.get("conclusion") not in OK_CONCLUSIONS]
    ci = "none" if not runs else "pending" if pending else "fail" if failed else "pass"
    reviews = client.get(f"repos/{repo}/pulls/{number}/reviews?per_page=100")
    author = (pr.get("user") or {}).get("login")
    latest: dict[str, str] = {}
    for rv in reviews:
        who = (rv.get("user") or {}).get("login")
        if who and who != author and rv.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[who] = rv["state"]
    approved = sorted(w for w, s in latest.items() if s == "APPROVED")
    blocked = sorted(w for w, s in latest.items() if s == "CHANGES_REQUESTED")
    auto = pr.get("auto_merge")
    problems = []
    if ci != "pass":
        problems.append(f"CI {ci}" + (f": {', '.join(failed or pending)}" if failed or pending else ""))
    if not approved:
        problems.append("no approving review from someone other than the author")
    if blocked:
        problems.append(f"changes requested by {', '.join(blocked)}")
    if auto:
        problems.append("auto-merge is enabled: the Improver's rule is human-only merges; disable it")
    state = "merged" if pr.get("merged") else pr.get("state")
    return {
        "repo": repo, "number": number, "url": pr.get("html_url"), "state": state, "head_sha": sha,
        "ci": ci, "checks": len(runs), "failed": failed, "pending": pending,
        "approved_by": approved, "auto_merge": bool(auto),
        "ready_to_ship": state == "open" and not problems,
        "problems": problems,
        "ship": "merge stays with the human owner",
    }


# ---------------------------------------------------------------- RECORD


def record(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Measured before/after receipt between two scorecards (only rows present in both)."""
    b = {r["repo"]: r for r in before["repos"]}
    rows = []
    for r in after["repos"]:
        if r["repo"] not in b:
            continue
        old = b[r["repo"]]
        changes = []
        for d in after["dimensions"]:
            o = old["dimensions"].get(d, {}).get("score")
            n = r["dimensions"].get(d, {}).get("score")
            if o != n:
                changes.append({"dimension": d, "before": o, "after": n,
                                "delta": None if o is None or n is None else round(n - o, 1)})
        rows.append({"repo": r["repo"], "overall_before": old["overall"], "overall_after": r["overall"],
                     "overall_delta": None if old["overall"] is None or r["overall"] is None
                     else round(r["overall"] - old["overall"], 1), "changes": changes})
    return {"schema": RECEIPT_SCHEMA, "before_at": before["generated_at"], "after_at": after["generated_at"],
            "repos": rows}


def receipt_markdown(rc: dict[str, Any]) -> str:
    lines = [f"# Improver receipt — {rc['before_at']} → {rc['after_at']}", "",
             "| repo | dimension | before | after | delta |", "| :-- | :-- | --: | --: | --: |"]
    n = 0
    for r in rc["repos"]:
        for c in r["changes"]:
            n += 1
            lines.append(f"| {r['repo']} | {c['dimension']} | {_fmt(c['before'])} | {_fmt(c['after'])} | "
                         f"{_fmt(c['delta'])} |")
    if not n:
        lines.append("| (no score changes) | | | | |")
    return "\n".join(lines) + "\n"


def write_json(path: str | Path, data: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return p
