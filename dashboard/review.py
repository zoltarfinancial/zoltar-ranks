#!/usr/bin/env python3
"""The review protocol - SS12 of the Zoltar Research Console.

The shared working surface for the three parties: Cowork writes what should
happen next and why, Claude Code reports what it did, Andrew decides what only
he can decide. Cycles are immutable; everything after is an append; status is
derived. See dashboard/review_protocol.md for the full contract.

  python dashboard/review.py next                     # open items for claude-code
  python dashboard/review.py next --for andrew        # ... or for another party
  python dashboard/review.py post --item c-0007.1 --type done --ref abc1234
  python dashboard/review.py raise --kind finding --title "..." --text "..."
  python dashboard/review.py emit                     # derive review_state.json/.js
  python dashboard/review.py check                    # enforce the invariants
  python dashboard/review.py open-cycle --file c.json # Cowork only: seal a cycle

stdlib only. Nothing here imports zoltar_ranks or opens DuckDB, so it can be
called from any session, any hook, at any time, and cannot fail a harvest.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

REPO = Path(__file__).resolve().parents[1]
REVIEW_DIR = REPO / "data" / "review"
CYCLES_DIR = REVIEW_DIR / "cycles"
INBOX = REVIEW_DIR / "inbox.jsonl"
HEARTBEAT = REVIEW_DIR / "heartbeat.jsonl"
CHARTER = REVIEW_DIR / "charter.json"
OUT_JSON = REPO / "data" / "results" / "review_state.json"
OUT_JS = REPO / "data" / "results" / "review_state.js"
BUILD_STATUS = REPO / "data" / "results" / "build_status.json"

PARTIES = ("cowork", "claude-code", "andrew")
KINDS = ("work_order", "question", "decision", "risk", "finding")
EVENT_TYPES = ("accept", "start", "done", "blocked", "reject", "defer",
               "reply", "note", "reopen", "raise")
# Events that move an item's status. `reply` and `note` deliberately do not.
STATUS_EVENTS = {"accept": "acked", "start": "in_progress", "done": "done",
                 "blocked": "blocked", "reject": "rejected", "defer": "deferred",
                 "reopen": "open"}
CLOSED = ("done", "rejected", "deferred", "answered")
ITEM_RE = re.compile(r"^c-\d{4}\.\d+$")
CYCLE_RE = re.compile(r"^c-\d{4}$")


# --------------------------------------------------------------------- helpers
def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def read_jsonl(p: Path) -> list[dict]:
    """Append-only logs are read leniently: a torn line is skipped, never fixed.

    A crashed writer must not be able to make the whole board unreadable, and
    silently repairing a line in place would break the append-only guarantee
    that the derived status rests on.
    """
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_jsonl(p: Path, rec: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n")


def load_cycles() -> list[dict]:
    if not CYCLES_DIR.exists():
        return []
    out = []
    for f in sorted(CYCLES_DIR.glob("c-*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            print(f"WARNING: {f.name} is not valid JSON and was skipped", file=sys.stderr)
    out.sort(key=lambda c: c.get("cycle_id", ""))
    return out


def load_charter() -> dict:
    if CHARTER.exists():
        try:
            return json.loads(CHARTER.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def next_cycle_id() -> str:
    cs = load_cycles()
    n = max((int(c["cycle_id"].split("-")[1]) for c in cs if CYCLE_RE.match(c.get("cycle_id", ""))),
            default=0)
    return f"c-{n + 1:04d}"


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


# ------------------------------------------------------------------- derivation
def acceptance_paths(text: str) -> list[str]:
    """Repo-relative paths named inside an acceptance string.

    Deliberately conservative: it only picks up things that look like a path with
    a known source extension. A false positive would flag a genuinely finished
    item as unverified, which is worse than not checking at all.
    """
    if not text:
        return []
    pat = re.compile(r"(?<![\w./-])((?:[\w.-]+/)+[\w.-]+\.(?:py|json|jsonl|yaml|yml|md|html|ps1|sql|parquet|duckdb))")
    return sorted(set(pat.findall(text)))


def derive_items(cycles: list[dict], events: list[dict]) -> list[dict]:
    by_item: dict[str, list[dict]] = {}
    for e in events:
        iid = e.get("item")
        if iid:
            by_item.setdefault(iid, []).append(e)
    for evs in by_item.values():
        evs.sort(key=lambda e: e.get("at") or "")

    items = []
    for c in cycles:
        for it in c.get("items", []):
            iid = it.get("id")
            evs = by_item.get(iid, [])
            status = "open"
            needs = None
            for e in evs:
                t = e.get("type")
                if t in STATUS_EVENTS:
                    status = STATUS_EVENTS[t]
                    needs = e.get("needs") if t == "blocked" else None
                elif t == "reply" and it.get("kind") == "question" and e.get("by") == it.get("for"):
                    status = "answered"

            # A `done` claim is not evidence. If the acceptance names an artifact
            # and the artifact is absent, say so rather than closing the item -
            # the same rule as `kind: proof` in the build manifest.
            unverified = []
            if status == "done":
                for rel in acceptance_paths(it.get("acceptance", "")):
                    if not (REPO / rel).exists():
                        unverified.append(rel)

            if status in ("open", "acked", "in_progress"):
                waiting = it.get("for")
            elif status == "blocked":
                waiting = needs or ("andrew" if it.get("for") == "claude-code" else "cowork")
            else:
                waiting = None

            items.append({
                **{k: it.get(k) for k in
                   ("id", "kind", "for", "priority", "title", "why", "acceptance",
                    "depends_on", "estimate", "refs")},
                "cycle": c.get("cycle_id"),
                "opened_at": c.get("opened_at"),
                "status": status,
                "done_unverified": unverified,
                "waiting_on": waiting,
                "events": [{k: e.get(k) for k in ("at", "by", "type", "text", "ref", "needs")}
                           for e in evs],
                "last_activity": (evs[-1].get("at") if evs else c.get("opened_at")),
            })
    return items


def blocked_by(items: list[dict]) -> dict[str, list[str]]:
    """Unmet dependencies, so the board never asks for work that cannot start."""
    status = {i["id"]: i["status"] for i in items}
    out = {}
    for i in items:
        unmet = [d for d in (i.get("depends_on") or [])
                 if status.get(d) not in ("done", "answered", "rejected", "deferred")]
        if unmet:
            out[i["id"]] = unmet
    return out


def heartbeat_state(cadence_minutes: int) -> dict:
    beats = read_jsonl(HEARTBEAT)
    beats = [b for b in beats if parse(b.get("at"))]
    beats.sort(key=lambda b: b["at"])
    last = beats[-1] if beats else None
    streak = 0
    for b in reversed(beats):
        if b.get("quiet"):
            streak += 1
        else:
            break
    t0 = now()
    last_at = parse(last["at"]) if last else None
    nxt = last_at + timedelta(minutes=cadence_minutes) if last_at else None
    # Late only after two missed windows, so a single skipped fire is not an alarm.
    late = bool(nxt and t0 > nxt + timedelta(minutes=cadence_minutes))
    return {
        "last_at": iso(last_at),
        "last_cycle": (last or {}).get("cycle"),
        "next_expected_at": iso(nxt),
        "cadence_minutes": cadence_minutes,
        "quiet_streak": streak,
        "late": late,
        "recent": [{"at": b.get("at"), "quiet": bool(b.get("quiet")),
                    "cycle": b.get("cycle"), "note": b.get("note")}
                   for b in beats[-24:]],
    }


def build_state() -> dict:
    charter = load_charter()
    cycles = load_cycles()
    events = read_jsonl(INBOX)
    items = derive_items(cycles, events)
    unmet = blocked_by(items)

    raised = [e for e in events if e.get("type") == "raise" and not e.get("folded")]
    raised.sort(key=lambda e: e.get("at") or "", reverse=True)

    counts = {p: sum(1 for i in items if i["waiting_on"] == p) for p in PARTIES}
    counts["open"] = sum(1 for i in items if i["status"] not in CLOSED)
    counts["done"] = sum(1 for i in items if i["status"] == "done")
    counts["unverified"] = sum(1 for i in items if i.get("done_unverified"))
    counts["raised"] = len(raised)

    bs = {}
    if BUILD_STATUS.exists():
        try:
            b = json.loads(BUILD_STATUS.read_text(encoding="utf-8"))
            bs = {"gate": b.get("gate", {}).get("state"),
                  "headline": b.get("gate", {}).get("headline"),
                  "generated_at": b.get("generated_at")}
        except json.JSONDecodeError:
            pass

    current = cycles[-1] if cycles else None
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(now()),
        "charter": charter,
        "heartbeat": heartbeat_state(int(charter.get("cadence_minutes") or 120)),
        "build": bs,
        "current_cycle": ({k: current.get(k) for k in
                           ("cycle_id", "opened_at", "opened_by", "quiet",
                            "assessment", "state_snapshot")} if current else None),
        "cycles": [{"cycle_id": c.get("cycle_id"), "opened_at": c.get("opened_at"),
                    "quiet": bool(c.get("quiet")), "assessment": c.get("assessment"),
                    "n_items": len(c.get("items", []))}
                   for c in cycles[-12:]][::-1],
        "items": sorted(items, key=lambda i: (i["status"] in CLOSED,
                                              {"now": 0, "next": 1, "later": 2}.get(i.get("priority"), 3),
                                              i["id"]), reverse=False),
        "unmet_dependencies": unmet,
        "raised": [{k: e.get(k) for k in ("at", "by", "kind", "title", "text", "ref")}
                   for e in raised[:12]],
        "counts": counts,
        "post_endpoint": "/api/review",
        "repo": {"head_sha": git("rev-parse", "--short", "HEAD"),
                 "branch": git("rev-parse", "--abbrev-ref", "HEAD")},
    }


# ------------------------------------------------------------------ subcommands
def cmd_emit(_args) -> int:
    state = build_state()
    payload = json.dumps(state, indent=1, ensure_ascii=False)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(payload, encoding="utf-8")
    OUT_JS.write_text("window.__ZOLTAR_REVIEW__ = " + payload + ";\n", encoding="utf-8")
    c = state["counts"]
    cyc = (state["current_cycle"] or {}).get("cycle_id", "-")
    print(f"data/results/review_state.json  cycle={cyc}  open={c['open']}  "
          f"andrew={c['andrew']}  claude-code={c['claude-code']}  cowork={c['cowork']}"
          + (f"  UNVERIFIED={c['unverified']}" if c["unverified"] else "")
          + (f"  raised={c['raised']}" if c["raised"] else ""))
    return 0


def cmd_next(args) -> int:
    state = build_state()
    who = args.party
    unmet = state["unmet_dependencies"]
    mine = [i for i in state["items"] if i["waiting_on"] == who]
    order = {"now": 0, "next": 1, "later": 2}
    mine.sort(key=lambda i: (order.get(i.get("priority"), 3), i["id"]))

    ch = state.get("charter") or {}
    if ch.get("end_goal"):
        print(f"\nEND GOAL  {ch['end_goal']}")
    if ch.get("critical_path"):
        print(f"CRITICAL PATH  {ch['critical_path']}")
    b = state.get("build") or {}
    if b.get("gate"):
        print(f"BUILD GATE  {b['gate'].upper()} - {b.get('headline','')}")
    cyc = state.get("current_cycle") or {}
    if cyc:
        print(f"\nCYCLE {cyc.get('cycle_id')}  opened {cyc.get('opened_at')}")
        if cyc.get("assessment"):
            print(f"  {cyc['assessment']}")

    if not mine:
        print(f"\nNothing open for {who}. "
              f"({state['counts']['open']} item(s) open overall.)\n")
        return 0

    print(f"\n{len(mine)} open item(s) for {who}:\n" + "-" * 72)
    for i in mine:
        blocked = unmet.get(i["id"])
        flag = ""
        if blocked:
            flag = f"   [BLOCKED BY {', '.join(blocked)}]"
        elif i["status"] == "blocked":
            flag = "   [BLOCKED]"
        print(f"\n{i['id']}  [{(i.get('priority') or '-').upper()}] "
              f"{(i.get('kind') or '').replace('_', ' ')}  <{i['status']}>{flag}")
        print(f"  {i.get('title')}")
        if i.get("why"):
            print(f"  WHY         {i['why']}")
        if i.get("acceptance"):
            print(f"  ACCEPTANCE  {i['acceptance']}")
        if i.get("estimate"):
            print(f"  ESTIMATE    {i['estimate']}")
        if i.get("refs"):
            print(f"  REFS        {', '.join(i['refs'])}")
        if i.get("done_unverified"):
            print(f"  !! reported done but missing: {', '.join(i['done_unverified'])}")
        for e in i["events"][-3:]:
            print(f"    - {(e.get('at') or '')[:16]} {e.get('by')} {e.get('type')}"
                  + (f": {e.get('text')}" if e.get("text") else ""))
    print("\n" + "-" * 72)
    print("Report back:  python dashboard/review.py post --item <id> --type "
          "start|done|blocked|reject --text \"...\" [--ref <sha>] [--needs andrew]")
    print("Raise something new:  python dashboard/review.py raise --kind finding "
          "--title \"...\" --text \"...\"\n")
    return 0


def cmd_post(args) -> int:
    if not ITEM_RE.match(args.item):
        sys.exit(f"bad item id {args.item!r} - expected the form c-0007.1")
    known = {i["id"] for i in derive_items(load_cycles(), [])}
    if args.item not in known:
        sys.exit(f"unknown item {args.item}. Run: python {Path(__file__).name} next --for {args.by}")
    if args.type == "blocked" and not args.needs:
        sys.exit("--type blocked requires --needs <cowork|claude-code|andrew>: "
                 "a block with nobody to unblock it is a black hole.")
    if args.type == "done" and not (args.ref or args.text):
        sys.exit("--type done requires --ref (commit/artifact) or --text saying what landed. "
                 "A completion with no evidence is a claim.")
    rec = {"at": iso(now()), "by": args.by, "cycle": args.item.split(".")[0],
           "item": args.item, "type": args.type, "needs": args.needs,
           "text": args.text, "ref": args.ref}
    append_jsonl(INBOX, rec)
    print(f"posted {args.type} on {args.item} as {args.by}")
    return cmd_emit(args)


def cmd_raise(args) -> int:
    """Open something new without writing a cycle. Folded in by the next heartbeat."""
    rec = {"at": iso(now()), "by": args.by, "cycle": None, "item": None,
           "type": "raise", "kind": args.kind, "for": args.party,
           "title": args.title, "text": args.text, "ref": args.ref, "folded": False}
    append_jsonl(INBOX, rec)
    print(f"raised {args.kind}: {args.title}\n"
          f"It appears in SS12 under 'Raised, not yet in a cycle' and the next "
          f"heartbeat folds it into a cycle.")
    return cmd_emit(args)


def cmd_beat(args) -> int:
    append_jsonl(HEARTBEAT, {"at": iso(now()), "quiet": bool(args.quiet),
                             "cycle": args.cycle, "note": args.note})
    print(f"heartbeat recorded ({'quiet' if args.quiet else 'active'})")
    return cmd_emit(args)


def cmd_open_cycle(args) -> int:
    """Seal a cycle file. Cowork only. Refuses to overwrite - cycles are immutable."""
    src = Path(args.file)
    doc = json.loads(src.read_text(encoding="utf-8"))
    cid = doc.get("cycle_id") or next_cycle_id()
    doc["cycle_id"] = cid
    doc.setdefault("opened_at", iso(now()))
    doc.setdefault("opened_by", "cowork")
    for n, it in enumerate(doc.get("items", []), start=1):
        it.setdefault("id", f"{cid}.{n}")
    dst = CYCLES_DIR / f"{cid}.json"
    if dst.exists():
        sys.exit(f"{dst} already exists. Cycles are immutable - write the next one, "
                 f"or append a note/reopen event instead.")
    CYCLES_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"sealed {dst.relative_to(REPO)} with {len(doc.get('items', []))} item(s)")
    rc = check(quiet=False)
    cmd_emit(args)
    return rc


# ----------------------------------------------------------------------- check
def check(quiet: bool = False) -> int:
    rows: list[tuple[str, str]] = []

    def bad(m):
        rows.append(("FAIL", m))

    def warn(m):
        rows.append(("WARN", m))

    cycles = load_cycles()
    events = read_jsonl(INBOX)

    if not CHARTER.exists():
        bad("data/review/charter.json missing - the board has no end goal to rank work against")
    if not cycles:
        warn("no cycles yet - the first heartbeat writes one")

    seen_ids, seen_cycles = set(), set()
    for c in cycles:
        cid = c.get("cycle_id", "")
        if not CYCLE_RE.match(cid):
            bad(f"cycle id {cid!r} is not of the form c-0007")
        if cid in seen_cycles:
            bad(f"duplicate cycle id {cid}")
        seen_cycles.add(cid)
        if not parse(c.get("opened_at")):
            bad(f"{cid}: opened_at is missing or unparseable")
        elif str(c.get("opened_at"))[-6] not in "+-":
            warn(f"{cid}: opened_at has no UTC offset")
        for it in c.get("items", []):
            iid = it.get("id", "")
            if not ITEM_RE.match(iid):
                bad(f"{cid}: item id {iid!r} is not of the form c-0007.1")
            if iid in seen_ids:
                bad(f"duplicate item id {iid}")
            seen_ids.add(iid)
            if it.get("kind") not in KINDS:
                bad(f"{iid}: kind {it.get('kind')!r} not in {KINDS}")
            if it.get("for") not in PARTIES:
                bad(f"{iid}: for {it.get('for')!r} not in {PARTIES}")
            if "status" in it:
                bad(f"{iid}: carries a status field. Status is derived, never stored.")
            if it.get("kind") == "work_order" and not (it.get("acceptance") or "").strip():
                bad(f"{iid}: work_order with no acceptance - "
                    f"an order without a checkable finish line never closes")
            for dep in it.get("depends_on") or []:
                if not ITEM_RE.match(str(dep)):
                    bad(f"{iid}: depends_on {dep!r} is not an item id")

    for n, e in enumerate(events, start=1):
        if e.get("type") not in EVENT_TYPES:
            bad(f"inbox line {n}: type {e.get('type')!r} not in {EVENT_TYPES}")
        if e.get("by") not in PARTIES:
            bad(f"inbox line {n}: by {e.get('by')!r} not in {PARTIES}")
        if not parse(e.get("at")):
            bad(f"inbox line {n}: at is missing or unparseable")
        if e.get("type") != "raise":
            if not e.get("item"):
                bad(f"inbox line {n}: {e.get('type')} event with no item")
            elif e["item"] not in seen_ids:
                bad(f"inbox line {n}: references unknown item {e['item']}")

    for dep_owner, unmet in blocked_by(derive_items(cycles, events)).items():
        for d in unmet:
            if d not in seen_ids:
                bad(f"{dep_owner}: depends on {d}, which does not exist")

    state = build_state()
    for i in state["items"]:
        if i.get("done_unverified"):
            warn(f"{i['id']} reported done but its acceptance names missing "
                 f"file(s): {', '.join(i['done_unverified'])}")

    hb = state["heartbeat"]
    if hb["late"]:
        warn(f"heartbeat is late - last fired {hb['last_at']}, expected by "
             f"{hb['next_expected_at']}. The scheduled task may have stopped.")

    if not quiet:
        print(f"\nreview protocol check - {now():%Y-%m-%d %H:%M %Z}\n")
        if not rows:
            print(f"  [OK  ] {len(cycles)} cycle(s), {len(seen_ids)} item(s), "
                  f"{len(events)} event(s) - all invariants hold")
        for level, msg in rows:
            print(f"  [{level}] {msg}")
        fails = sum(1 for l, _ in rows if l == "FAIL")
        print(f"\n  {fails} blocking, {len(rows) - fails} warning\n")
    return 1 if any(l == "FAIL" for l, _ in rows) else 0


def cmd_check(_args) -> int:
    return check()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("emit", help="derive review_state.json and .js").set_defaults(fn=cmd_emit)
    sub.add_parser("check", help="enforce the protocol invariants").set_defaults(fn=cmd_check)

    n = sub.add_parser("next", help="print the open items for a party")
    n.add_argument("--for", dest="party", default="claude-code", choices=PARTIES)
    n.set_defaults(fn=cmd_next)

    p = sub.add_parser("post", help="append an event to the inbox")
    p.add_argument("--item", required=True)
    p.add_argument("--type", required=True, choices=[t for t in EVENT_TYPES if t != "raise"])
    p.add_argument("--by", default="claude-code", choices=PARTIES)
    p.add_argument("--text", default=None)
    p.add_argument("--ref", default=None)
    p.add_argument("--needs", default=None, choices=PARTIES)
    p.set_defaults(fn=cmd_post)

    r = sub.add_parser("raise", help="open something new outside a cycle")
    r.add_argument("--kind", default="finding", choices=KINDS)
    r.add_argument("--title", required=True)
    r.add_argument("--text", default=None)
    r.add_argument("--for", dest="party", default="cowork", choices=PARTIES)
    r.add_argument("--by", default="claude-code", choices=PARTIES)
    r.add_argument("--ref", default=None)
    r.set_defaults(fn=cmd_raise)

    b = sub.add_parser("beat", help="record a heartbeat fire")
    b.add_argument("--quiet", action="store_true")
    b.add_argument("--cycle", default=None)
    b.add_argument("--note", default=None)
    b.set_defaults(fn=cmd_beat)

    o = sub.add_parser("open-cycle", help="seal a cycle file (Cowork only)")
    o.add_argument("--file", required=True)
    o.set_defaults(fn=cmd_open_cycle)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
