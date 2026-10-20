"""Repo-hygiene assertions checked into the repo and run by `pytest -q`.

These mirror the polish brief items 1-4 and are committed alongside
the files they check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# 1. CODEOWNERS


def test_codeowners_contains_owner_handle() -> None:
    codeowners = (ROOT / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    active = [
        line for line in codeowners.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert active, ".github/CODEOWNERS has no active rules"
    assert any("* @mrodgersjs-web" in line for line in active), (
        ".github/CODEOWNERS must contain a `* @mrodgersjs-web` rule"
    )


# 2. copilot-instructions.md


def test_copilot_instructions_present_and_below_size_ceiling() -> None:
    copilot = ROOT / ".github" / "copilot-instructions.md"
    assert copilot.exists(), ".github/copilot-instructions.md missing"
    size = copilot.stat().st_size
    assert size <= 8000, f"copilot-instructions.md is {size} bytes; the ceiling is 8000"


@pytest.mark.parametrize("word", ["pytest"])
def test_copilot_instructions_names_test_command(word: str) -> None:
    copilot = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert word in copilot, (
        f".github/copilot-instructions.md must mention the canonical test command {word!r}"
    )


def test_copilot_instructions_preserves_heartbeat_schedule() -> None:
    """The polish brief said the heartbeat schedule must not change.
    The copilot file should pin it so a future edit does not drift it.
    """
    copilot = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert "0 * * * *" in copilot, (
        "copilot-instructions.md must pin the heartbeat cron as '0 * * * *'"
    )


# 3. README


def test_readme_has_workflow_status_badge_url() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    pattern = re.compile(
        r"https://img\.shields\.io/github/actions/workflow/status/"
        r"mrodgersjs-web/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\?[^\s)]*\bbranch="
    )
    assert pattern.search(readme), (
        "README must cite a shields workflow-status badge URL"
    )


def test_readme_has_rodgersintelligence_footer() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "rodgersintelligence.com" in readme, (
        "README must contain the rodgersintelligence.com footer"
    )


def test_readme_self_clone_uses_org() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    clones = [line for line in readme.splitlines() if "git clone" in line]
    assert clones, "README has no `git clone` line"
    for line in clones:
        assert "mrodgersjs-web/rig-agent-firm" in line, (
            f"`git clone` line does not target mrodgersjs-web/rig-agent-firm: {line!r}"
        )


# 4. workflows


def _iter_workflow_jobs(text: str) -> list[tuple[str, str]]:
    jobs: list[tuple[str, str]] = []
    match = re.search(r"(?m)^jobs:\s*$", text)
    if not match:
        return jobs
    after_jobs = text[match.end():]
    job_pat = re.compile(r"(?m)^  (\w[\w-]*):\s*$")
    boundary_pat = re.compile(r"(?m)\n  (\w[\w-]*):\s*$")
    for job_match in job_pat.finditer(after_jobs):
        name = job_match.group(1)
        boundary = boundary_pat.search(after_jobs, job_match.end())
        block_end = boundary.start() if boundary else len(after_jobs)
        jobs.append((name, after_jobs[job_match.start():block_end]))
    return jobs


def test_every_workflow_job_has_timeout_minutes() -> None:
    workflows = list((ROOT / ".github" / "workflows").rglob("*.yml"))
    assert workflows, "no workflows found"
    for wf in workflows:
        text = wf.read_text(encoding="utf-8")
        for name, block in _iter_workflow_jobs(text):
            assert re.search(r"(?m)^    timeout-minutes:\s*\d+", block), (
                f"{wf.name} job {name!r} has no `timeout-minutes`"
            )


def test_no_workflow_job_uses_continue_on_error() -> None:
    workflows = list((ROOT / ".github" / "workflows").rglob("*.yml"))
    for wf in workflows:
        text = wf.read_text(encoding="utf-8")
        for _, block in _iter_workflow_jobs(text):
            assert "continue-on-error" not in block, (
                f"{wf.name} job uses `continue-on-error`; remove it"
            )


def test_ci_workflow_runs_on_push_and_pull_request() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^on:\s*$", ci), "ci.yml is missing the `on:` key"
    assert re.search(r"(?m)^\s+push:\s*$", ci), "ci.yml does not trigger on push"
    assert re.search(r"(?m)^\s+pull_request:\s*$", ci), "ci.yml does not trigger on pull_request"


def test_heartbeat_workflow_schedule_unchanged() -> None:
    """The polish brief said the heartbeat cron must not change."""
    hb = (ROOT / ".github" / "workflows" / "heartbeat.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^    - cron:\s*'0 \* \* \* \*'", hb) or re.search(
        r"(?m)^    - cron:\s*\"0 \* \* \* \*\"", hb
    ), "heartbeat.yml cron must remain '0 * * * *'"
