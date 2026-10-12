"""Schema and validator for ``agents/*.yaml`` specs.

A spec is a YAML mapping. Required fields:

- name, dept, repo ("owner/name"), role, boundary, trigger, sla (prose)
- sla_hours: integer window (hours) used as the default freshness limit
- invariant: one-line human description of what "alive" means
- checks: non-empty list of machine checks (see CHECK_TYPES)

Optional: implementation, claim, owner, status_branch, heartbeat_cron,
queue_depth_metric, last_action_metric, and ``improver`` (the improvement-loop
block; see ``validate_improver``). A spec with an ``improver`` block must keep
``merge_policy: human_only`` and ``auto_merge: false`` or it is rejected.
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

# Improvement loop (agents/improver.yaml). Phase order is fixed; SHIP is the human's merge.
IMPROVER_PHASES = ("measure", "pick", "build", "verify", "ship", "record")
IMPROVER_CADENCES = ("nightly",)
IMPROVER_DIMENSIONS = (
    "ci_smoke",
    "tests_coverage",
    "stranger_time",
    "readme_quality",
    "hero_image",
    "release_cadence",
    "issue_age",
    "security_basics",
)
IMPROVER_KEYS = {"cadence", "cron", "phases", "scope", "scorecard", "pick", "merge_policy", "auto_merge"}

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
    improver: dict[str, Any] | None = None

    @property
    def slug(self) -> str:
        return self.dept


def validate_improver(block: Any, source: str = "<spec>") -> list[str]:
    """Validate the ``improver`` block. The never-auto-merge rule is enforced here."""
    where = f"{source}: improver"
    if not isinstance(block, dict):
        return [f"{where} must be a mapping"]
    errs: list[str] = []
    for k in sorted(set(block) - IMPROVER_KEYS):
        errs.append(f"{where} unknown key {k!r}")
    for k in sorted(IMPROVER_KEYS - set(block)):
        errs.append(f"{where} missing required key {k!r}")
    if block.get("merge_policy") != "human_only":
        errs.append(f"{where}.merge_policy must be 'human_only' (merges stay with the human owner)")
    if block.get("auto_merge") is not False:
        errs.append(f"{where}.auto_merge must be false (the Improver only opens issues and PRs)")
    if "cadence" in block and block["cadence"] not in IMPROVER_CADENCES:
        errs.append(f"{where}.cadence must be one of {', '.join(IMPROVER_CADENCES)}")
    cron = block.get("cron")
    if "cron" in block and (not isinstance(cron, str) or not CRON_RE.match(cron.strip())):
        errs.append(f"{where}.cron must have 5 fields, got {cron!r}")
    if "phases" in block and tuple(block["phases"] or ()) != IMPROVER_PHASES:
        errs.append(f"{where}.phases must be exactly {list(IMPROVER_PHASES)} in that order")
    dims = block.get("scorecard")
    if "scorecard" in block:
        if not isinstance(dims, list) or not dims:
            errs.append(f"{where}.scorecard must be a non-empty list of dimensions")
        else:
            for d in dims:
                if d not in IMPROVER_DIMENSIONS:
                    errs.append(f"{where}.scorecard unknown dimension {d!r} (known: {', '.join(IMPROVER_DIMENSIONS)})")
            if len(set(dims)) != len(dims):
                errs.append(f"{where}.scorecard has duplicate dimensions")
    scope = block.get("scope")
    if "scope" in block:
        if not isinstance(scope, dict):
            errs.append(f"{where}.scope must be a mapping")
        else:
            if not isinstance(scope.get("owner"), str) or not scope["owner"].strip():
                errs.append(f"{where}.scope.owner is required")
            labels = scope.get("labels")
            if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and x for x in labels):
                errs.append(f"{where}.scope.labels must be a non-empty list of strings")
            if scope.get("visibility", "public") not in ("public", "all"):
                errs.append(f"{where}.scope.visibility must be 'public' or 'all'")
    pick = block.get("pick")
    if "pick" in block:
        if not isinstance(pick, dict):
            errs.append(f"{where}.pick must be a mapping")
        else:
            n = pick.get("max_issues_per_night")
            if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
                errs.append(f"{where}.pick.max_issues_per_night must be a positive integer")
            if not isinstance(pick.get("issue_label"), str) or not pick["issue_label"].strip():
                errs.append(f"{where}.pick.issue_label must be a non-empty string")
    return errs


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
    if "improver" in data:
        errs.extend(validate_improver(data["improver"], source))
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
        improver=data.get("improver"),
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
