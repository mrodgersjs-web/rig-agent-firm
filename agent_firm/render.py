"""Render badge.json (shields endpoint) and dashboard.svg from status documents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from .heartbeat import firm_status

COLORS = {"online": "#238636", "offline": "#da3633", "unknown": "#9e6a03"}
BADGE_COLORS = {"online": "brightgreen", "offline": "red", "degraded": "yellow", "no data": "lightgrey"}


def load_statuses(status_dir: str | Path) -> list[dict[str, Any]]:
    docs = []
    for f in sorted(Path(status_dir).glob("*.json")):
        docs.append(json.loads(f.read_text()))
    return docs


def make_badge(statuses: list[dict[str, Any]]) -> dict[str, Any]:
    fs = firm_status(statuses)
    message = "no data" if fs["total"] == 0 else f"{fs['online']}/{fs['total']} online"
    return {"schemaVersion": 1, "label": "agent firm", "message": message, "color": BADGE_COLORS[fs["state"]]}


STYLE = (
    "<style>text{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;fill:#c9d1d9}"
    ".t{font-size:28px;font-weight:700;fill:#f0f6fc}.s{font-size:14px;fill:#8b949e}"
    ".r{font-size:16px;font-weight:600;fill:#f0f6fc}.k{font-size:12px;fill:#58a6ff}"
    ".c{font-size:12px}.m{font-size:11px;fill:#8b949e}</style>"
)


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def make_dashboard(statuses: list[dict[str, Any]]) -> str:
    fs = firm_status(statuses)
    cols, cw, ch, gap, x0, y0 = 3, 340, 250, 30, 60, 130
    rows = max(1, -(-len(statuses) // cols))
    height = y0 + rows * (ch + gap) + 40
    latest = max((s["checked_at"] for s in statuses), default="never")
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="{height}" viewBox="0 0 1200 {height}" role="img" aria-label="Agent firm dashboard">',
        STYLE,
        f'<rect width="1200" height="{height}" fill="#0d1117"/>',
        '<text class="t" x="60" y="55">AGENT FIRM</text>',
        f'<text class="s" x="60" y="85">{fs["online"]}/{fs["total"]} online · firm status: {escape(fs["state"])} · last check {escape(latest)}</text>',
    ]
    for i, s in enumerate(statuses):
        x = x0 + (i % cols) * (cw + gap)
        y = y0 + (i // cols) * (ch + gap)
        color = COLORS.get(s["status"], COLORS["unknown"])
        out.append(f'<g transform="translate({x},{y})">')
        out.append(f'<rect width="{cw}" height="{ch}" rx="8" fill="#161b22" stroke="#30363d"/>')
        out.append(f'<rect width="8" height="{ch}" rx="4" fill="{color}"/>')
        out.append(f'<text class="r" x="24" y="34">{escape(s["repo"])}</text>')
        out.append(f'<text class="k" x="24" y="54">{escape(s["agent"].upper())}</text>')
        out.append(f'<text class="r" x="{cw - 24}" y="34" text-anchor="end" fill="{color}" style="fill:{color}">'
                   f'{escape(s["status"].upper())}</text>')
        for j, c in enumerate(s.get("checks", [])[:6]):
            mark = "✓" if c["ok"] else "✗" if c["ok"] is False else "?"
            out.append(f'<text class="c" x="24" y="{84 + j * 28}">{mark} {escape(_clip(c["detail"], 50))}</text>')
        sha = s.get("sha", "")[:7]
        out.append(f'<text class="m" x="24" y="{ch - 28}">evidence: {escape(sha or "none")} '
                   f'{escape(s.get("evidence_at", ""))}</text>')
        out.append(f'<text class="m" x="24" y="{ch - 12}">checked {escape(s["checked_at"])}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_outputs(statuses: list[dict[str, Any]], out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    badge = out / "badge.json"
    svg = out / "dashboard.svg"
    badge.write_text(json.dumps(make_badge(statuses), indent=2) + "\n")
    svg.write_text(make_dashboard(statuses))
    return badge, svg
