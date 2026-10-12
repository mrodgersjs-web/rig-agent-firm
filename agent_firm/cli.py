"""Command line: ``agent-firm validate | heartbeat | render | improver {measure,pick,verify,record}``."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import improver as imp
from .github import ApiError, FixtureClient, GitHubClient, WriteRefused
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
        unknown = wanted - {s.dept for s in specs}
        if unknown:
            print(f"error: unknown agents in --only: {', '.join(sorted(unknown))}", file=sys.stderr)
            return None
        specs = [s for s in specs if s.dept in wanted]
    return specs


def cmd_validate(args) -> int:
    specs = _load(args.agents)
    if specs is None:
        return 2
    for s in specs:
        print(f"ok  {s.dept:<14} {s.name:<10} {len(s.checks)} checks")
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


def _client(args):
    if getattr(args, "fixtures", None):
        return FixtureClient(args.fixtures)
    if not (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")):
        print("warning: no GITHUB_TOKEN/GH_TOKEN set; unauthenticated GitHub allows 60 requests/hour and hides "
              "security settings, so the scorecard may be partial (see 'partial' in the output)", file=sys.stderr)
    return GitHubClient(record_dir=getattr(args, "record", None))


def _improver_spec(args):
    try:
        return imp.load_improver(args.agents, args.loop)
    except imp.ImproverConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def cmd_improver_measure(args) -> int:
    spec = _improver_spec(args)
    if spec is None:
        return 2
    now = parse_ts(args.now) if args.now else None
    repos = [r.strip() for r in args.repos.split(",") if r.strip()] if args.repos else None
    try:
        card = imp.measure(_client(args), spec, now=now, repos=repos)
    except ApiError as exc:
        print(f"error: could not list repos: {exc}", file=sys.stderr)
        return 3
    out = Path(args.out)
    js = imp.write_json(out / "scorecard.json", card)
    md = out / "scorecard.md"
    md.write_text(imp.scorecard_markdown(card))
    for r in card["repos"]:
        low = imp.lowest_dimension(r, card["dimensions"])
        lowtxt = f"{low[0]}={low[1]:g}" if low else "-"
        print(f"{imp._fmt(r['overall']):>5}  {r['repo']:<42} lowest {lowtxt}{'  PARTIAL' if r['partial'] else ''}")
    print(f"scored {len(card['repos'])} repos; wrote {js} and {md}")
    if card["partial"] and not args.allow_partial:
        print("error: some GitHub API calls failed (rate limit or 5xx); scorecard is partial", file=sys.stderr)
        return 3
    return 0


def cmd_improver_pick(args) -> int:
    spec = _improver_spec(args)
    if spec is None:
        return 2
    card = json.loads(Path(args.scorecard).read_text())
    client = _client(args) if args.open_issue else None
    try:
        res = imp.pick(card, spec, client=client, open_issue=args.open_issue)
    except (imp.ImproverConfigError, WriteRefused, ApiError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    js = imp.write_json(out / "picks.json", res)
    md = out / "picks.md"
    md.write_text(imp.picks_markdown(res))
    for c in res["chosen"]:
        print(f"pick  {c['repo']:<42} {c['dimension']}={c['score']:g}  -> {c['title']}")
    for o in res["opened"]:
        print(f"opened {o['html_url']}")
    print(f"{len(res['chosen'])} chosen, {len(res['queued'])} queued, {len(res['skipped'])} skipped"
          f"{' (dry run)' if res['dry_run'] else ''}; wrote {js} and {md}")
    return 0


def cmd_improver_verify(args) -> int:
    m = re.match(r"^([\w.-]+/[\w.-]+)#(\d+)$", args.pr)
    if not m:
        print("error: PR must look like owner/repo#123", file=sys.stderr)
        return 2
    res = imp.verify_pr(_client(args), m.group(1), int(m.group(2)))
    print(json.dumps(res, indent=2))
    return 0 if res["ready_to_ship"] else 1


def cmd_improver_record(args) -> int:
    rc = imp.record(json.loads(Path(args.before).read_text()), json.loads(Path(args.after).read_text()))
    out = Path(args.out)
    js = imp.write_json(out / "receipt.json", rc)
    md = out / "receipt.md"
    md.write_text(imp.receipt_markdown(rc))
    n = sum(len(r["changes"]) for r in rc["repos"])
    print(f"{n} score changes across {len(rc['repos'])} repos; wrote {js} and {md}")
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
    im = sub.add_parser("improver", help="nightly improvement loop: measure, pick, verify, record (never merges)")
    isub = im.add_subparsers(dest="phase", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--agents", default="agents")
    common.add_argument("--loop", default=".rig/loop.yaml")
    common.add_argument("--fixtures", help="read recorded API responses from this directory instead of GitHub")
    common.add_argument("--record", help="save pruned live API responses to this directory")
    ms = isub.add_parser("measure", parents=[common], help="score every in-scope repo; write scorecard.json/.md")
    ms.add_argument("--out", default="out/improver")
    ms.add_argument("--repos", default="", help="comma-separated repos to score instead of discovering by label")
    ms.add_argument("--now", help="override the clock (ISO 8601, for fixtures)")
    ms.add_argument("--allow-partial", action="store_true", help="exit 0 even if some API calls failed")
    ms.set_defaults(fn=cmd_improver_measure)
    pk = isub.add_parser("pick", parents=[common], help="lowest dimension per repo -> scoped issue drafts")
    pk.add_argument("--scorecard", default="out/improver/scorecard.json")
    pk.add_argument("--out", default="out/improver")
    pk.add_argument("--open-issue", action="store_true",
                    help="actually open the chosen issue(s) (needs a token with issues:write); default is a dry run")
    pk.set_defaults(fn=cmd_improver_pick)
    vf = isub.add_parser("verify", parents=[common], help="check a PR's CI and review state (read-only)")
    vf.add_argument("pr", help="owner/repo#number")
    vf.set_defaults(fn=cmd_improver_verify)
    rc = isub.add_parser("record", help="before/after receipt from two scorecards")
    rc.add_argument("--before", required=True)
    rc.add_argument("--after", required=True)
    rc.add_argument("--out", default="out/improver")
    rc.set_defaults(fn=cmd_improver_record)
    args = p.parse_args(argv)
    return args.fn(args)
