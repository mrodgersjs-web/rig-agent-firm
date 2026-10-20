"""Heartbeat engine: run each agent's checks against its repo through the GitHub REST API."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .github import ApiError
from .schema import AgentSpec, Check

SCHEMA_VERSION = 2
ONLINE, OFFLINE, UNKNOWN = "online", "offline", "unknown"


class Client(Protocol):
    def get(self, path: str) -> Any: ...


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Result:
    """Outcome of one check. ok is True (pass), False (definite failure) or None (could not tell)."""

    type: str
    ok: bool | None
    detail: str
    url: str = ""
    sha: str = ""
    timestamp: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "params": self.params,
            "ok": self.ok,
            "detail": self.detail,
            "url": self.url,
            "sha": self.sha,
            "timestamp": self.timestamp,
        }


def _age_hours(ts: str, now: datetime) -> float:
    return (now - parse_ts(ts)).total_seconds() / 3600


def check_file_exists(c: Client, repo: str, branch: str, check: Check, spec: AgentSpec, now: datetime) -> Result:
    path = check.params["path"].strip("/")
    parent, _, leaf = path.rpartition("/")
    ref = f"{branch}:{parent}" if parent else branch
    tree = c.get(f"repos/{repo}/git/trees/{ref}")
    entry = next((e for e in tree.get("tree", []) if e.get("path") == leaf and e.get("type") == "blob"), None)
    url = f"https://github.com/{repo}/blob/{branch}/{path}"
    if entry is None:
        return Result("file_exists", False, f"{path} not found on {branch}", url=url)
    if check.params.get("executable") and entry.get("mode") != "100755":
        return Result("file_exists", False, f"{path} exists but is not executable (mode {entry.get('mode')})",
                      url=url, sha=entry.get("sha", ""))
    note = " and is executable" if check.params.get("executable") else ""
    return Result("file_exists", True, f"{path} exists on {branch}{note}", url=url, sha=entry.get("sha", ""))


def check_workflow_success(c: Client, repo: str, branch: str, check: Check, spec: AgentSpec, now: datetime) -> Result:
    wf = check.params["workflow"]
    limit = check.params.get("max_age_hours", spec.sla_hours)
    data = c.get(f"repos/{repo}/actions/workflows/{wf}/runs?branch={branch}&status=completed&per_page=1")
    runs = data.get("workflow_runs", [])
    if not runs:
        return Result("workflow_success", False, f"no completed run of {wf} on {branch}")
    run = runs[0]
    ts = run.get("updated_at") or run.get("created_at", "")
    base = {"url": run.get("html_url", ""), "sha": run.get("head_sha", ""), "timestamp": ts}
    if run.get("conclusion") != "success":
        return Result("workflow_success", False, f"latest {wf} run on {branch} concluded {run.get('conclusion')}", **base)
    age = _age_hours(ts, now)
    if age > limit:
        return Result("workflow_success", False,
                      f"latest {wf} run succeeded but is {age:.0f}h old (limit {limit}h)", **base)
    return Result("workflow_success", True, f"latest {wf} run on {branch} succeeded {age:.1f}h ago (limit {limit}h)", **base)


def check_release_exists(c: Client, repo: str, branch: str, check: Check, spec: AgentSpec, now: datetime) -> Result:
    try:
        rel = c.get(f"repos/{repo}/releases/latest")
    except ApiError as exc:
        if exc.status == 404:
            return Result("release_exists", False, "no published release")
        raise
    ts = rel.get("published_at") or rel.get("created_at", "")
    base = {"url": rel.get("html_url", ""), "sha": rel.get("target_commitish", ""), "timestamp": ts}
    limit = check.params.get("max_age_hours")
    if limit and ts and _age_hours(ts, now) > limit:
        return Result("release_exists", False, f"latest release {rel.get('tag_name')} is older than {limit}h", **base)
    return Result("release_exists", True, f"latest release {rel.get('tag_name')} published {ts}", **base)


def check_commit_within(c: Client, repo: str, branch: str, check: Check, spec: AgentSpec, now: datetime) -> Result:
    limit = check.params.get("hours", spec.sla_hours)
    commits = c.get(f"repos/{repo}/commits?sha={branch}&per_page=1")
    if not commits:
        return Result("commit_within", False, f"no commits on {branch}")
    cm = commits[0]
    ts = cm.get("commit", {}).get("committer", {}).get("date", "")
    base = {"url": cm.get("html_url", ""), "sha": cm.get("sha", ""), "timestamp": ts}
    age = _age_hours(ts, now)
    if age > limit:
        return Result("commit_within", False, f"last commit on {branch} is {age:.0f}h old (limit {limit}h)", **base)
    return Result("commit_within", True, f"last commit on {branch} is {age:.1f}h old (limit {limit}h)", **base)


CHECKS: dict[str, Callable[..., Result]] = {
    "file_exists": check_file_exists,
    "workflow_success": check_workflow_success,
    "release_exists": check_release_exists,
    "commit_within": check_commit_within,
}


def run_agent(client: Client, spec: AgentSpec, now: datetime) -> dict[str, Any]:
    """Evaluate every check for one agent and return its status document."""
    results: list[Result] = []
    try:
        meta = client.get(f"repos/{spec.repo}")
        branch = meta["default_branch"]
    except ApiError as exc:
        branch = ""
        results.append(Result("repo_reachable", False if exc.status == 404 else None,
                              f"cannot read {spec.repo}: HTTP {exc.status}"))
    if branch:
        for check in spec.checks:
            try:
                res = CHECKS[check.type](client, spec.repo, branch, check, spec, now)
            except ApiError as exc:
                ok = False if exc.status == 404 else None
                res = Result(check.type, ok, f"{'not found' if ok is False else 'API error'}: HTTP {exc.status}")
            res.params = dict(check.params)
            results.append(res)
    if any(r.ok is False for r in results):
        status = OFFLINE
    elif any(r.ok is None for r in results):
        status = UNKNOWN
    else:
        status = ONLINE
    # Evidence: prefer the first passing/failing check that carries a run URL, else any URL.
    ev = next((r for r in results if r.type == "workflow_success" and r.url), None) or next(
        (r for r in results if r.url), None)
    owner, _, name = spec.repo.partition("/")
    summary = "; ".join(f"{'ok' if r.ok else 'FAIL' if r.ok is False else '?'}: {r.detail}" for r in results)
    return {
        "schema_version": SCHEMA_VERSION,
        "agent": spec.name,
        "repo": name,
        "owner": owner,
        "role": spec.role,
        "status": status,
        "invariant": spec.invariant,
        "invariant_result": summary,
        "checked_at": iso(now),
        "default_branch": branch,
        "run_url": ev.url if ev else "",
        "sha": ev.sha if ev else "",
        "evidence_at": ev.timestamp if ev else "",
        "checks": [r.as_dict() for r in results],
    }


def firm_status(statuses: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(statuses)
    online = sum(1 for s in statuses if s["status"] == ONLINE)
    offline = sum(1 for s in statuses if s["status"] == OFFLINE)
    unknown = total - online - offline
    if total == 0:
        state = "no data"
    elif offline:
        state = OFFLINE
    elif unknown:
        state = "degraded"
    else:
        state = ONLINE
    return {"state": state, "total": total, "online": online, "offline": offline, "unknown": unknown}


def run_heartbeat(client: Client, specs: list[AgentSpec], out_dir: str | Path, now: datetime | None = None) -> list[dict[str, Any]]:
    """Check every agent and write ``<out_dir>/status/<name>.json``. Returns the status docs."""
    now = now or datetime.now(timezone.utc)
    status_dir = Path(out_dir) / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    docs = []
    for spec in specs:
        doc = run_agent(client, spec, now)
        (status_dir / f"{spec.name}.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        docs.append(doc)
    return docs
