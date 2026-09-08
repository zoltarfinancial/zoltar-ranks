"""The fleet message envelope, and the one entry point that builds it.

Spec: `docs/fleet-protocol.md`. Stdlib only. No `zoltar_ranks` import, no DuckDB,
so nothing here can fail a harvest.

This module owns R2, R3 and R6, and `fleet_sync.py` imports it rather than
re-implementing them — two implementations of a content-addressed id is how they
diverge, and a diverged id silently turns one message into two.

    R2  one envelope, identical bytes, every transport.
        msg_id = sha256(canonical_json(everything except msg_id))[:16]
    R3  order comes from causality, not clocks. `lamport` plus the per-agent
        `seq`/`prev` chain totally orders the bus with every clock wrong.
        `authored_at` is METADATA and is never an ordering key.
    R6  ingesting the same msg_id twice is a no-op.

Why R3 is not theoretical here: ZoltarLead's own System log carries three
Kernel-General "the system time has changed" events at 2026-09-04T00:44:29. The
clock on the machine that writes every review cycle demonstrably moves. Under R3
that makes a wrong clock a wrong *label* and never a wrong *sequence*.

`observed_at` is deliberately NOT in the envelope. It is assigned by the
RECEIVER on ingest and stored in the local ingest ledger, because the message is
immutable and content-addressed — putting a receiver-local timestamp inside it
would change its id per receiver and break R2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import through the `scripts` package, never as a bare top-level module.
# `import fleet_identity` here and `from scripts import fleet_identity` in a
# test create TWO module objects with separate DECLARED_TRANSPORTS, separate
# MSG_DIR and separate everything -- the exact divergence this module exists
# to prevent, one layer down. Caught by tests/test_fleet_bus.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import fleet_identity as fi                          # noqa: E402
MSG_DIR = REPO_ROOT / "fleet" / "msgs"
RECEIPT_DIR = REPO_ROOT / "fleet" / "receipts"
LEDGER_DIR = REPO_ROOT / "fleet" / "ingest"

SCHEMA_VERSION = 1
KINDS = ("note", "question", "work_order", "finding", "decision",
         "status", "ack", "receipt")

#: The four reachability states (R4). `empty` and `unreachable` must NEVER
#: render the same: "I read it and there was nothing" and "I could not read it"
#: are different facts, and conflating them is the defect the whole protocol
#: exists to remove. `msgs_read: 0` and `msgs_read: null` are likewise different.
STATES = ("ok", "empty", "unreachable", "absent-by-design")

#: Nodes whose transport set is a permanent property, not a fault to re-raise.
#: zoltargenesis/cowork runs as a scheduled CLOUD fire with no device at all --
#: five consecutive fires with the remote-devices tool family absent from the
#: session -- and its OneDrive is a different tenant (apod78@ vs
#: zoltarfinancial@), which no amount of retrying will bridge.
DECLARED_TRANSPORTS = {
    "zoltargenesis": {"git": "absent-by-design", "onedrive": "absent-by-design",
                      "db": "ok"},
}


def canonical(obj) -> bytes:
    """One byte-string per logical message. Sorted keys, no incidental spacing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def compute_msg_id(body: dict) -> str:
    b = {k: v for k, v in body.items() if k != "msg_id"}
    return hashlib.sha256(canonical(b)).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ------------------------------- transports -------------------------------

def onedrive_root() -> Path | None:
    base = os.environ.get("OneDrive") or os.environ.get("USERPROFILE", "") + "\\OneDrive"
    p = Path(base) / "ZoltarUnlimited" / "fleet" / "msgs"
    return p if Path(base).exists() else None


def transport_state(name: str, node_id: str) -> tuple[str, str | None, Path | None]:
    """(state, reason, path). Never guesses: a path that is not there is
    `unreachable` WITH the reason, not `empty`."""
    declared = DECLARED_TRANSPORTS.get(node_id, {}).get(name)
    if declared == "absent-by-design":
        return "absent-by-design", f"{name} does not serve {node_id}, and never will", None
    if name == "git":
        return ("ok", None, MSG_DIR) if MSG_DIR.parent.parent.exists() else \
               ("unreachable", f"repo root missing: {REPO_ROOT}", None)
    if name == "onedrive":
        root = onedrive_root()
        if root is None:
            return "unreachable", "OneDrive root not found for this user", None
        return "ok", None, root
    if name == "db":
        # Not a fault and not a failure: the worker lane has no Artifact tool.
        # Declaring it stops the fleet re-raising it every session.
        return ("absent-by-design",
                "no Artifact tool in Claude Code; only a cowork brain reaches the db",
                None)
    return "unreachable", f"unknown transport {name!r}", None


def read_transport(name: str, node_id: str) -> dict:
    """Read every message a transport holds. Reports its own reachability."""
    state, reason, path = transport_state(name, node_id)
    out = {"state": state, "reason": reason, "msgs_read": None, "msgs": []}
    if state != "ok" or path is None:
        return out
    try:
        if not path.exists():
            # The directory not existing yet is a MEASUREMENT of zero, not a
            # failure to read -- the transport is reachable, it just holds
            # nothing. This is precisely the empty/unreachable distinction.
            out.update(state="empty", msgs_read=0)
            return out
        msgs = []
        for f in sorted(path.glob("*.json")):
            try:
                msgs.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception as exc:                              # noqa: BLE001
                out.setdefault("bad", []).append(f"{f.name}: {exc}")
        out.update(state="ok" if msgs else "empty", msgs_read=len(msgs), msgs=msgs)
    except Exception as exc:                                      # noqa: BLE001
        out.update(state="unreachable", reason=str(exc), msgs_read=None)
    return out


def write_transport(name: str, node_id: str, msg: dict, raw: bytes) -> tuple[bool, str | None]:
    """Write the SAME BYTES. Never re-serialise: that is what keeps ids equal."""
    state, reason, path = transport_state(name, node_id)
    if state not in ("ok", "empty") or path is None:
        return False, reason or state
    try:
        path.mkdir(parents=True, exist_ok=True)
        (path / filename(msg)).write_bytes(raw)
        return True, None
    except Exception as exc:                                      # noqa: BLE001
        return False, str(exc)


def filename(msg: dict) -> str:
    return f"{msg['lamport']:06d}-{msg['agent_id'].replace('/', '-')}-{msg['msg_id']}.json"


# ------------------------------ the log state ------------------------------

def all_known(node_id: str) -> list[dict]:
    """Every message this node can see, from every reachable transport."""
    seen: dict[str, dict] = {}
    for t in ("git", "onedrive"):
        for m in read_transport(t, node_id)["msgs"]:
            if isinstance(m, dict) and m.get("msg_id"):
                seen.setdefault(m["msg_id"], m)
    return list(seen.values())


def next_lamport(msgs) -> int:
    """max(everything ever seen) + 1. No clock involved."""
    return max([int(m.get("lamport") or 0) for m in msgs], default=0) + 1


def agent_chain(msgs, agent_id: str) -> list[dict]:
    return sorted([m for m in msgs if m.get("agent_id") == agent_id],
                  key=lambda m: int(m.get("seq") or 0))


def next_seq_and_prev(msgs, agent_id: str) -> tuple[int, str | None]:
    chain = agent_chain(msgs, agent_id)
    if not chain:
        return 1, None
    last = chain[-1]
    return int(last["seq"]) + 1, last["msg_id"]


def find_gaps(msgs) -> list[dict]:
    """R5. seq has no gaps by construction, so a hole is a detectable defect.

    Needs no clock and no agreement about time -- only the per-agent counter.
    """
    gaps = []
    agents = {m.get("agent_id") for m in msgs if m.get("agent_id")}
    for a in sorted(x for x in agents if x):
        chain = agent_chain(msgs, a)
        have = {int(m["seq"]) for m in chain if m.get("seq") is not None}
        if not have:
            continue
        missing = sorted(set(range(1, max(have) + 1)) - have)
        if missing:
            gaps.append({"agent_id": a, "held_through": max(have),
                         "missing_seq": missing,
                         "newest_msg_id": chain[-1]["msg_id"]})
    return gaps


# --------------------------------- posting ---------------------------------

def build(agent_id: str, kind: str, subject: str, body_text: str,
          to: str = "all", ref: str | None = None,
          needs_reply_from: str | None = None,
          node_id: str = "zoltarlead", by: dict | None = None,
          known: list[dict] | None = None) -> tuple[dict, bytes]:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    known = all_known(node_id) if known is None else known
    seq, prev = next_seq_and_prev(known, agent_id)
    reach = {t: transport_state(t, node_id)[0] for t in ("git", "onedrive", "db")}
    msg = {
        "schema_version": SCHEMA_VERSION,
        "agent_id": agent_id,
        "seq": seq,
        "prev": prev,
        "lamport": next_lamport(known),
        # METADATA. Never an ordering key. See R3.
        "authored_at": now_iso(),
        "kind": kind,
        "to": to,
        "subject": subject,
        "body": body_text,
        "ref": ref,
        "needs_reply_from": needs_reply_from,
        "fanout": [t for t, s in reach.items() if s in ("ok", "empty")],
        "reachable": reach,
        "by": by or fi.attest(node_id),
    }
    msg["msg_id"] = compute_msg_id(msg)
    return msg, canonical(msg)


def verify(msg: dict) -> list[str]:
    """Everything checkable about one envelope, without trusting its author."""
    problems = []
    if msg.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version {msg.get('schema_version')!r}")
    recomputed = compute_msg_id(msg)
    if recomputed != msg.get("msg_id"):
        problems.append(f"msg_id mismatch: stored {msg.get('msg_id')} "
                        f"but content hashes to {recomputed}")
    if msg.get("kind") not in KINDS:
        problems.append(f"kind {msg.get('kind')!r}")
    if not isinstance(msg.get("seq"), int) or msg["seq"] < 1:
        problems.append(f"seq {msg.get('seq')!r}")
    for t, s in (msg.get("reachable") or {}).items():
        if s not in STATES:
            problems.append(f"reachable[{t}] = {s!r}, not one of {STATES}")
    return problems


def post(args) -> int:
    by = fi.attest(args.node_id)
    if by["verified"] is not True and not args.allow_unverified:
        print(f"REFUSING to post: identity not verified ({by.get('reason')}). "
              f"Step 0 of the protocol -- a message whose author cannot be "
              f"attested is worse than no message.", file=sys.stderr)
        return 2
    msg, raw = build(args.agent_id, args.kind, args.subject, args.body,
                     to=args.to, ref=args.ref, node_id=args.node_id, by=by)
    written, failed = [], {}
    for t in ("git", "onedrive"):
        ok, err = write_transport(t, args.node_id, msg, raw)
        (written.append(t) if ok else failed.setdefault(t, err))
    print(f"  msg_id   {msg['msg_id']}")
    print(f"  seq      {msg['seq']}  prev={msg['prev']}  lamport={msg['lamport']}")
    print(f"  written  {written or 'NONE'}")
    for t, e in failed.items():
        print(f"  FAILED   {t}: {e}")
    return 0 if written else 1


def cmd_list(args) -> int:
    msgs = sorted(all_known(args.node_id), key=lambda m: int(m.get("lamport") or 0))
    for m in msgs:
        print(f"  [{m.get('lamport'):>6}] {m.get('agent_id')} seq={m.get('seq')} "
              f"{m.get('kind')}: {m.get('subject')}  ({m.get('msg_id')})")
    print(f"  {len(msgs)} message(s)")
    for g in find_gaps(msgs):
        print(f"  GAP {g['agent_id']}: held through {g['held_through']}, "
              f"missing seq {g['missing_seq']}")
    return 0


def cmd_check(args) -> int:
    msgs = all_known(args.node_id)
    bad = 0
    for m in msgs:
        for p in verify(m):
            print(f"  [FAIL] {m.get('msg_id')}: {p}")
            bad += 1
    gaps = find_gaps(msgs)
    for g in gaps:
        print(f"  [WARN] gap {g['agent_id']} missing seq {g['missing_seq']}")
    print(f"  {len(msgs)} message(s), {bad} invalid, {len(gaps)} gap(s)")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node-id", default="zoltarlead")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("post", help="build an envelope and fan it out")
    p.add_argument("--agent-id", default="zoltarlead/claude-code")
    p.add_argument("--kind", default="note", choices=KINDS)
    p.add_argument("--subject", required=True)
    p.add_argument("--body", required=True)
    p.add_argument("--to", default="all")
    p.add_argument("--ref", default=None)
    p.add_argument("--allow-unverified", action="store_true")
    p.set_defaults(fn=post)

    p = sub.add_parser("list"); p.set_defaults(fn=cmd_list)
    p = sub.add_parser("check"); p.set_defaults(fn=cmd_check)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
