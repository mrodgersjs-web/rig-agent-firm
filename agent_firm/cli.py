"""Command line: ``agent-firm validate | heartbeat | render``."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from .github import FixtureClient, GitHubClient
from .heartbeat import firm_status, parse_ts, run_heartbeat
from .render import load_statuses, make_badge, write_outputs
from .schema import load_specs


def _load(agents: str, only: str = ""):
    specs, errors = load_specs(agents)
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return None
    if only:
        wanted = {w.strip() for w in only.split(",") if w.strip()}
        unknown = wanted - {s.name for s in specs}
        if unknown:
            print(f"error: unknown agents in --only: {', '.join(sorted(unknown))}", file=sys.stderr)
            return None
        specs = [s for s in specs if s.name in wanted]
    return specs


def cmd_validate(args) -> int:
    specs = _load(args.agents)
    if specs is None:
        return 2
    for s in specs:
        print(f"ok  {s.name:<10} {s.repo:<28} {len(s.checks)} checks")
    print(f"{len(specs)} agent specs valid")
    return 0


def cmd_heartbeat(args) -> int:
    specs = _load(args.agents, args.only)
    if specs is None:
        return 2
    if args.fixtures:
        client = FixtureClient(args.fixtures)
    else:
        client = GitHubClient(record_dir=args.record)
    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)
    docs = run_heartbeat(client, specs, args.out, now=now)
    for d in docs:
        print(f"{d['status']:<8} {d['repo']:<15} {d['run_url']}")
    # Render from everything on disk so a partial --only run keeps the other agents' last results.
    statuses = load_statuses(Path(args.out) / "status")
    badge, svg = write_outputs(statuses, args.out)
    fs = firm_status(statuses)
    print(f"firm: {fs['state']} ({fs['online']}/{fs['total']} online); wrote {badge} and {svg}")
    if args.fail_on_offline and fs["state"] != "online":
        return 1
    return 0


def cmd_render(args) -> int:
    statuses = load_statuses(Path(args.status))
    badge, svg = write_outputs(statuses, args.out)
    print(f"wrote {badge}: {make_badge(statuses)['message']}; wrote {svg}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="agent-firm", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="validate agents/*.yaml")
    v.add_argument("--agents", default="agents")
    v.set_defaults(fn=cmd_validate)
    h = sub.add_parser("heartbeat", help="check every agent's checks and write status, badge and dashboard")
    h.add_argument("--agents", default="agents")
    h.add_argument("--out", default="out")
    h.add_argument("--only", default="", help="comma-separated agent depts (default: all)")
    h.add_argument("--fixtures", help="read recorded API responses from this directory instead of GitHub")
    h.add_argument("--record", help="save pruned live API responses to this directory")
    h.add_argument("--now", help="override the clock (ISO 8601, for fixtures)")
    h.add_argument("--fail-on-offline", action="store_true", help="exit 1 unless the firm is fully online")
    h.set_defaults(fn=cmd_heartbeat)
    r = sub.add_parser("render", help="rebuild badge.json and dashboard.svg from status files")
    r.add_argument("--status", default="out/status")
    r.add_argument("--out", default="out")
    r.set_defaults(fn=cmd_render)
    args = p.parse_args(argv)
    return args.fn(args)
