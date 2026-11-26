#!/usr/bin/env python3
"""Strict MCP 2 (protocol revision 2026-07-28) results-driven server for this repository (stdio).

Stdlib only — zero external dependencies required.
Exposes verified results only: sealed proofs, deterministic lattice score, and a
Gate-D request wrapper. No tool here executes an outward effect; execution stays
behind typed human approval (org execution-owner standard, row 8).

Strict MCP 2 contract:
- Answers `server/discover` advertising `supportedVersions: ["2026-07-28"]`
- Requires `_meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"`
- Rejects legacy `initialize` and legacy versions with JSON-RPC error `-32022`
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2026-07-28"
META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
UNSUPPORTED_PROTOCOL_VERSION = -32022

ROOT = Path(__file__).resolve().parents[1]
RIG_LOOP = Path.home() / "Developer/rig-capabilities/bin/rig-loop"
SERVER_INFO = {"name": "rig-results", "version": "2.0.0"}


def _j(**payload: Any) -> str:
    return json.dumps({"schema": "rig.results.v1", "protocol": PROTOCOL_VERSION, **payload}, default=str)


def tool_results(_args: dict[str, Any]) -> str:
    """Sealed proofs and result ledger entries for this repo (verified only)."""
    proofs_dir = ROOT / ".rig/proofs"
    proofs = sorted(p.name for p in proofs_dir.glob("*.json")) if proofs_dir.exists() else []
    return _j(repo=ROOT.name, proofs=proofs)


def tool_score(_args: dict[str, Any]) -> str:
    """Deterministic RIG lattice score for this repository."""
    if not RIG_LOOP.exists():
        return _j(error="platform rig-loop unavailable", repo=ROOT.name)
    run = subprocess.run([str(RIG_LOOP), "lattice", "score", str(ROOT)], capture_output=True, text=True, cwd=ROOT)
    return _j(repo=ROOT.name, exit=run.returncode, output=(run.stdout + run.stderr)[-2000:])


def tool_request_effect(args: dict[str, Any]) -> str:
    """Record a Gate-D request. Returns the pending request id; never executes."""
    action = str(args.get("action") or "")
    target = str(args.get("target") or "")
    reason = str(args.get("reason") or "")
    if not action or not target or not reason:
        raise ValueError("action, target, and reason are required")
    if not RIG_LOOP.exists():
        return _j(error="platform rig-loop unavailable")
    run = subprocess.run(
        [str(RIG_LOOP), "gate-d", "request", "--action", action, "--target", target, "--reason", reason],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return _j(exit=run.returncode, stdout=run.stdout[-800:], stderr=run.stderr[-400:])


TOOLS = sorted(
    [
        {
            "name": "results",
            "description": "Sealed proofs and result ledger entries for this repo (verified only).",
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "score",
            "description": "Deterministic RIG lattice score for this repository.",
            "annotations": {"readOnlyHint": True, "destructiveHint": False},
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "request_effect",
            "description": "Record a Gate-D request. Returns the pending request id; never executes.",
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["action", "target", "reason"],
                "additionalProperties": False,
            },
        },
    ],
    key=lambda t: str(t["name"]),
)

HANDLERS = {
    "results": tool_results,
    "score": tool_score,
    "request_effect": tool_request_effect,
}


def _result(rid: Any, body: dict[str, Any], cacheable: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        **body,
        "resultType": "complete",
        "_meta": {META_SERVER_INFO: SERVER_INFO},
    }
    if cacheable:
        out["ttlMs"] = 300000
        out["cacheScope"] = "public"
    return {"jsonrpc": "2.0", "id": rid, "result": out}


def _unsupported(rid: Any, requested: str, message: str = "Unsupported protocol version") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "error": {
            "code": UNSUPPORTED_PROTOCOL_VERSION,
            "message": message,
            "data": {"supported": [PROTOCOL_VERSION], "requested": requested},
        },
    }


def dispatch(msg: dict[str, Any]) -> dict[str, Any] | None:
    if "id" not in msg:
        return None
    rid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    if method == "initialize":
        req_ver = str(params.get("protocolVersion") or "")
        return _unsupported(
            rid,
            req_ver,
            "Legacy initialize handshake is not supported; use MCP 2 (2026-07-28) server/discover and per-request _meta",
        )

    if method == "server/discover":
        meta = params.get("_meta")
        if isinstance(meta, dict) and META_PROTOCOL_VERSION in meta:
            req_ver = str(meta.get(META_PROTOCOL_VERSION) or "")
            if req_ver != PROTOCOL_VERSION:
                return _unsupported(rid, req_ver)
        return _result(
            rid,
            {
                "supportedVersions": [PROTOCOL_VERSION],
                "capabilities": {"tools": {"listChanged": False}},
                "instructions": f"Strict MCP 2 ({PROTOCOL_VERSION}) results server for {ROOT.name}.",
            },
            cacheable=True,
        )

    meta = params.get("_meta")
    req_ver = str(meta.get(META_PROTOCOL_VERSION) or "") if isinstance(meta, dict) else ""
    if req_ver != PROTOCOL_VERSION:
        return _unsupported(rid, req_ver)

    if method == "tools/list":
        return _result(rid, {"tools": TOOLS}, cacheable=True)

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not isinstance(name, str) or name not in HANDLERS:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"Unknown tool: {name!r}"}}
        try:
            text = HANDLERS[name](arguments)
            return _result(rid, {"content": [{"type": "text", "text": text}], "isError": False}, cacheable=False)
        except Exception as exc:
            return _result(rid, {"content": [{"type": "text", "text": str(exc)}], "isError": True}, cacheable=False)

    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve_forever() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}) + "\n")
            sys.stdout.flush()
            continue
        if isinstance(msg, dict):
            resp = dispatch(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, separators=(",", ":"), default=str) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    serve_forever()
