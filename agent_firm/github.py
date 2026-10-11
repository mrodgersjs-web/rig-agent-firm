"""Minimal GitHub REST client (stdlib only) plus a fixture-backed client for tests."""

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


class FixtureClient:
    """Serves recorded responses from a directory. A missing fixture is a 404."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    def get(self, path: str) -> Any:
        f = self.dir / fixture_name(path)
        if not f.exists():
            raise ApiError(404, "no recorded fixture", path)
        data = json.loads(f.read_text())
        if isinstance(data, dict) and set(data) == {"__error__"}:
            raise ApiError(int(data["__error__"]), "recorded error", path)
        return data
