"""Schema and validator for ``agents/*.yaml`` specs.

A spec is a YAML mapping. Required fields:

- name, dept, repo ("owner/name"), role, boundary, trigger, sla (prose)
- sla_hours: integer window (hours) used as the default freshness limit
- invariant: one-line human description of what "alive" means
- checks: non-empty list of machine checks (see CHECK_TYPES)

Optional: implementation, claim, owner, status_branch, heartbeat_cron,
queue_depth_metric, last_action_metric.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REQUIRED_STR = ("name", "dept", "repo", "role", "boundary", "trigger", "sla", "invariant")
OPTIONAL_STR = (
    "implementation",
    "claim",
    "owner",
    "status_branch",
    "heartbeat_cron",
    "queue_depth_metric",
    "last_action_metric",
)

# check type -> (required params, optional params)
CHECK_TYPES: dict[str, tuple[set[str], set[str]]] = {
    "file_exists": ({"path"}, {"executable"}),
    "workflow_success": ({"workflow"}, {"max_age_hours"}),
    "release_exists": (set(), {"max_age_hours"}),
    "commit_within": (set(), {"hours"}),
}

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CRON_RE = re.compile(r"^\S+( \S+){4}$")


@dataclass
class Check:
    type: str
    params: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        extra = ", ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        return f"{self.type}({extra})" if extra else self.type


@dataclass
class AgentSpec:
    name: str
    dept: str
    repo: str
    role: str
    boundary: str
    trigger: str
    sla: str
    sla_hours: int
    invariant: str
    checks: list[Check]
    claim: str = ""
    owner: str = ""
    path: str = ""

    @property
    def slug(self) -> str:
        return self.dept


def validate_spec(data: Any, source: str = "<spec>") -> list[str]:
    """Return a list of human-readable problems (empty list = valid)."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return [f"{source}: top level must be a mapping"]
    for key in REQUIRED_STR:
        v = data.get(key)
        if not isinstance(v, str) or not v.strip():
            errs.append(f"{source}: '{key}' is required and must be a non-empty string")
    for key in OPTIONAL_STR:
        if key in data and not isinstance(data[key], str):
            errs.append(f"{source}: '{key}' must be a string")
    repo = data.get("repo")
    if isinstance(repo, str) and repo.strip():
        if not REPO_RE.match(repo):
            errs.append(f"{source}: 'repo' must look like owner/name, got {repo!r}")
        elif data.get("owner") and repo.split("/")[0] != data["owner"]:
            errs.append(f"{source}: 'owner' {data['owner']!r} does not match repo owner")
        if isinstance(data.get("dept"), str) and REPO_RE.match(repo) and repo.split("/")[1] != data["dept"]:
            errs.append(f"{source}: 'dept' must equal the repo name ({repo.split('/')[1]})")
    hours = data.get("sla_hours")
    if isinstance(hours, bool) or not isinstance(hours, int) or hours <= 0:
        errs.append(f"{source}: 'sla_hours' is required and must be a positive integer")
    cron = data.get("heartbeat_cron")
    if isinstance(cron, str) and not CRON_RE.match(cron.strip()):
        errs.append(f"{source}: 'heartbeat_cron' must have 5 fields, got {cron!r}")
    checks = data.get("checks")
    if not isinstance(checks, list) or not checks:
        errs.append(f"{source}: 'checks' is required and must be a non-empty list")
    else:
        for i, c in enumerate(checks):
            where = f"{source}: checks[{i}]"
            if not isinstance(c, dict) or not isinstance(c.get("type"), str):
                errs.append(f"{where} must be a mapping with a string 'type'")
                continue
            ctype = c["type"]
            if ctype not in CHECK_TYPES:
                errs.append(f"{where} unknown type {ctype!r} (known: {', '.join(sorted(CHECK_TYPES))})")
                continue
            required, optional = CHECK_TYPES[ctype]
            params = {k: v for k, v in c.items() if k != "type"}
            for k in sorted(required - params.keys()):
                errs.append(f"{where} ({ctype}) missing required param '{k}'")
            for k in sorted(params.keys() - required - optional):
                errs.append(f"{where} ({ctype}) unknown param '{k}'")
            for k in ("path", "workflow"):
                if k in params and (not isinstance(params[k], str) or not params[k].strip()):
                    errs.append(f"{where} ({ctype}) '{k}' must be a non-empty string")
            if "path" in params and isinstance(params["path"], str) and params["path"].startswith("/"):
                errs.append(f"{where} (file_exists) 'path' must be relative")
            if "executable" in params and not isinstance(params["executable"], bool):
                errs.append(f"{where} (file_exists) 'executable' must be true or false")
            for k in ("max_age_hours", "hours"):
                if k in params and (isinstance(params[k], bool) or not isinstance(params[k], int) or params[k] <= 0):
                    errs.append(f"{where} ({ctype}) '{k}' must be a positive integer")
    return errs


def _to_spec(data: dict[str, Any], path: str) -> AgentSpec:
    checks = [Check(c["type"], {k: v for k, v in c.items() if k != "type"}) for c in data["checks"]]
    return AgentSpec(
        name=data["name"],
        dept=data["dept"],
        repo=data["repo"],
        role=" ".join(data["role"].split()),
        boundary=" ".join(data["boundary"].split()),
        trigger=" ".join(data["trigger"].split()),
        sla=" ".join(data["sla"].split()),
        sla_hours=data["sla_hours"],
        invariant=data["invariant"],
        checks=checks,
        claim=data.get("claim", ""),
        owner=data.get("owner", ""),
        path=path,
    )


def load_specs(directory: str | Path) -> tuple[list[AgentSpec], list[str]]:
    """Load and validate every ``*.yaml`` in *directory*. Returns (specs, errors)."""
    directory = Path(directory)
    specs: list[AgentSpec] = []
    errors: list[str] = []
    files = sorted(directory.glob("*.yaml"))
    if not files:
        return [], [f"{directory}: no *.yaml agent specs found"]
    for f in files:
        try:
            data = yaml.safe_load(f.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"{f}: invalid YAML: {exc}")
            continue
        errs = validate_spec(data, str(f))
        if errs:
            errors.extend(errs)
            continue
        specs.append(_to_spec(data, str(f)))
    seen_names: dict[str, str] = {}
    seen_repos: dict[str, str] = {}
    for s in specs:
        if s.name in seen_names:
            errors.append(f"{s.path}: duplicate agent name {s.name!r} (also {seen_names[s.name]})")
        seen_names[s.name] = s.path
        if s.repo in seen_repos:
            errors.append(f"{s.path}: repo {s.repo} already owned by {seen_repos[s.repo]} (one repo, one role)")
        seen_repos[s.repo] = s.path
    return specs, errors
