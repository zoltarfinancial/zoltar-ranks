"""The courier. One command per session; the executor runs it each cycle.

    pull -> ingest git msgs -> ingest OneDrive drops -> dedup by msg_id
         -> detect seq gaps -> repair from whichever transport has them
         -> emit outbound to every reachable transport -> write a receipt

Spec: `docs/fleet-protocol.md`. Stdlib only, no `zoltar_ranks`, no DuckDB.

**The receipt is the deliverable, not a log line.** A node that has not run a
sync has NO receipt, which is visibly different from a receipt saying it read
nothing. That distinction is the entire point: on 2026-09-02 this node's brain
kept writing and its output never left the machine, and for five days nothing in
the fleet could tell "ZoltarLead said nothing" from "ZoltarLead's channel does
not exist." Had a receipt existed, the gap would have been visible the same day.

Two couriers exist and NEITHER spans all three transports. This is one of them:

    git <-> OneDrive   the WORKER (this script)
    OneDrive/files <-> artifact db   the BRAIN, because only a brain has the
                                     Artifact tool

Do not try to build a single agent that does everything; no agent can reach all
three, and pretending otherwise is how the db silently stops being written.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# One module object, always. See the note in fleet_msg.py.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from scripts import fleet_msg as fm                               # noqa: E402
from scripts import fleet_identity as fi                          # noqa: E402

REPO_ROOT = fm.REPO_ROOT
TRANSPORTS = ("git", "onedrive", "db")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _git(*args: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        p = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as exc:                                      # noqa: BLE001
        return 1, "", str(exc)


def load_ledger(node_id: str) -> dict[str, dict]:
    """msg_id -> {observed_at, source}. The RECEIVER's clock, kept OUTSIDE the
    message, because the message is immutable and content-addressed."""
    p = fm.LEDGER_DIR / f"{node_id}.jsonl"
    out: dict[str, dict] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            out[r["msg_id"]] = r
        except Exception:                                         # noqa: BLE001
            continue          # a torn line is skipped, never repaired in place
    return out


def append_ledger(node_id: str, rows: list[dict]) -> None:
    if not rows:
        return
    fm.LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    p = fm.LEDGER_DIR / f"{node_id}.jsonl"
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def sync(node_id: str, agent_id: str, do_pull: bool = True,
         do_push: bool = False) -> dict:
    started = _now()
    receipt: dict = {
        "schema_version": 1, "agent_id": agent_id, "node_id": node_id,
        "at": started, "lamport": None,
        "transports": {}, "ingested": [], "gaps": [], "repaired": [],
        "by": fi.attest(node_id),
    }

    # ---- pull. A failed pull is reported, never silently treated as "no news".
    if do_pull:
        rc, _, err = _git("fetch", "--all", "--prune")
        receipt["git_fetch"] = {"ok": rc == 0, "error": err or None}

    # ---- read every transport, each reporting its own reachability (R4)
    reads: dict[str, dict] = {}
    for t in TRANSPORTS:
        reads[t] = fm.read_transport(t, node_id)
        row = {"state": reads[t]["state"], "msgs_read": reads[t]["msgs_read"]}
        if reads[t].get("reason"):
            row["reason"] = reads[t]["reason"]
        if t == "git":
            rc, head, _ = _git("rev-parse", "--short", "HEAD")
            row["head"] = head if rc == 0 else None
        receipt["transports"][t] = row

    # ---- dedup by msg_id (R6). Same message on three transports = one message.
    by_id: dict[str, dict] = {}
    where: dict[str, set] = {}
    for t, r in reads.items():
        for m in r["msgs"]:
            if not isinstance(m, dict) or not m.get("msg_id"):
                continue
            by_id.setdefault(m["msg_id"], m)
            where.setdefault(m["msg_id"], set()).add(t)

    msgs = list(by_id.values())
    receipt["lamport"] = fm.next_lamport(msgs)

    # ---- ingest: assign observed_at HERE, on the receiver
    ledger = load_ledger(node_id)
    new_rows = []
    for mid, m in by_id.items():
        if mid in ledger:
            continue                                   # R6: idempotent no-op
        new_rows.append({"msg_id": mid, "observed_at": _now(),
                         "source": sorted(where[mid]),
                         "agent_id": m.get("agent_id"), "seq": m.get("seq"),
                         "lamport": m.get("lamport")})
    append_ledger(node_id, new_rows)
    receipt["ingested"] = [r["msg_id"] for r in new_rows]

    # ---- gaps (R5), computed from seq alone. No clock needed.
    receipt["gaps"] = fm.find_gaps(msgs)

    # ---- repair: a message missing from one transport that the writer CLAIMED
    #      to reach is a detectable defect, not silence. Copy bytes, never
    #      re-serialise -- re-serialising would change the id and break R2.
    repaired = []
    for mid, m in by_id.items():
        claimed = set(m.get("fanout") or [])
        present = where[mid]
        for t in sorted(claimed - present):
            if receipt["transports"].get(t, {}).get("state") in ("ok", "empty"):
                ok, err = fm.write_transport(t, node_id, m, fm.canonical(m))
                if ok:
                    repaired.append({"msg_id": mid, "to": t,
                                     "from": sorted(present)})
    receipt["repaired"] = repaired

    # ---- push
    if do_push:
        rc, _, err = _git("push")
        receipt["git_push"] = {"ok": rc == 0, "error": err or None}

    # ---- the receipt itself
    fm.RECEIPT_DIR.joinpath(node_id).mkdir(parents=True, exist_ok=True)
    name = f"{receipt['lamport']:06d}-{started.replace(':', '').replace('-', '')[:15]}.json"
    path = fm.RECEIPT_DIR / node_id / name
    path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    receipt["_path"] = str(path)
    return receipt


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node-id", default="zoltarlead")
    ap.add_argument("--agent-id", default="zoltarlead/claude-code")
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = sync(args.node_id, args.agent_id,
             do_pull=not args.no_pull, do_push=args.push)
    if args.json:
        print(json.dumps(r, indent=2))
        return 0

    print(f"  {r['agent_id']}  lamport={r['lamport']}  "
          f"verified={(r['by'] or {}).get('verified')}")
    for t, row in r["transports"].items():
        n = row.get("msgs_read")
        # `0` and `null` print differently ON PURPOSE. That is the whole fix.
        shown = "null" if n is None else str(n)
        extra = f"  ({row['reason']})" if row.get("reason") else ""
        print(f"    {t:<9} {row['state']:<17} msgs_read={shown}{extra}")
    print(f"  ingested {len(r['ingested'])}  repaired {len(r['repaired'])}  "
          f"gaps {len(r['gaps'])}")
    for g in r["gaps"]:
        print(f"    GAP {g['agent_id']}: held through {g['held_through']}, "
              f"missing seq {g['missing_seq']}")
    for rp in r["repaired"]:
        print(f"    REPAIRED {rp['msg_id']} -> {rp['to']} (from {rp['from']})")
    print(f"  receipt  {r['_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
