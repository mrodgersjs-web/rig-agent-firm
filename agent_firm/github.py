"""Minimal GitHub REST client (stdlib only) plus a fixture-backed client for tests.

Reads are GET-only. The single write the firm performs is creating an issue
(``post``), and that path is allow-listed: merge, review-approval, auto-merge and
GraphQL endpoints are refused here, before any network call (see ``assert_write_allowed``).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API = "https://api.github.com"

# Keys removed when recording fixtures: large nested objects the engine never reads.
_DROP_KEYS = {
    "repository", "head_repository", "actor", "triggering_actor", "head_commit",
    "referencing_workflows", "author", "committer", "uploader", "owner", "user",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str, path: str = ""):
        super().__init__(f"HTTP {status} for {path}: {message}")
        self.status = status
        self.path = path


def fixture_name(path: str) -> str:
    """Map an API path (with query) to a flat, filesystem-safe fixture file name."""
    safe = re.sub(r"[^A-Za-z0-9._=-]+", "_", path.strip("/"))
    return safe + ".json"


def prune(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: prune(v)
            for k, v in obj.items()
            if k not in _DROP_KEYS and not (k.endswith("_url") and k != "html_url")
        }
    if isinstance(obj, list):
        return [prune(v) for v in obj]
    return obj


# The only write endpoint any role may call: create an issue on a repo.
WRITE_ALLOWED = re.compile(r"^repos/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/issues$")
# Never callable, whatever the allow-list says: merges stay with the human owner.
WRITE_FORBIDDEN = re.compile(r"(/merge$|/merges$|/auto-merge|^graphql$|/reviews|/pulls/\d+/update-branch)")


class WriteRefused(Exception):
    """Raised before any network call when code tries a write outside the allow-list."""


def assert_write_allowed(method: str, path: str) -> None:
    path = path.strip("/").split("?", 1)[0]
    if WRITE_FORBIDDEN.search(path):
        raise WriteRefused(f"{method} {path}: merges and approvals stay with the human owner")
    if method != "POST" or not WRITE_ALLOWED.match(path):
        raise WriteRefused(f"{method} {path}: not on the write allow-list (only POST repos/<o>/<r>/issues)")


class GitHubClient:
    """GET-only client. ``get(path)`` returns decoded JSON; raises ApiError on non-2xx."""

    def __init__(self, token: str | None = None, record_dir: str | Path | None = None, base: str = API):
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base = base
        self.record_dir = Path(record_dir) if record_dir else None
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)

    def get(self, path: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rig-agent-firm-heartbeat",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = self.base + "/" + path.lstrip("/")
        last: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode(errors="replace")[:200]
                if exc.code >= 500 and attempt < 2:
                    last = ApiError(exc.code, body, path)
                    time.sleep(1 + attempt)
                    continue
                if exc.code == 403 and "rate limit" in body.lower():
                    raise ApiError(429, "rate limited", path) from exc
                raise ApiError(exc.code, body, path) from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last = ApiError(0, str(exc), path)
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise last from exc
        else:  # pragma: no cover - loop always breaks or raises
            raise last or ApiError(0, "unknown", path)
        if self.record_dir:
            (self.record_dir / fixture_name(path)).write_text(json.dumps(prune(data), indent=1, sort_keys=True) + "\n")
        return data

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        """Create an issue. Any other write is refused by ``assert_write_allowed``."""
        assert_write_allowed("POST", path)
        if not self.token:
            raise ApiError(401, "writing needs GITHUB_TOKEN or GH_TOKEN with issues:write on the target repo", path)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "rig-agent-firm-improver",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        url = self.base + "/" + path.lstrip("/")
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code, exc.read().decode(errors="replace")[:200], path) from exc


class FixtureClient:
    """Serves recorded responses from a directory. A missing fixture is a 404."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.posted: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> Any:
        f = self.dir / fixture_name(path)
        if not f.exists():
            raise ApiError(404, "no recorded fixture", path)
        data = json.loads(f.read_text())
        if isinstance(data, dict) and set(data) == {"__error__"}:
            raise ApiError(int(data["__error__"]), "recorded error", path)
        return data

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        """Record the write instead of sending it, after the same allow-list check as the live client."""
        assert_write_allowed("POST", path)
        self.posted.append((path, payload))
        return {"number": len(self.posted), "html_url": f"https://github.com/{path.split('/', 1)[1]}/{len(self.posted)}"}
