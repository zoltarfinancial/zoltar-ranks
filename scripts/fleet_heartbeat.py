"""ZoltarLead's liveness stamp. Writes a stamp on SUCCESS **and** on FAILURE.

Stdlib only. No `zoltar_ranks` import, no DuckDB, so it cannot fail a harvest.

Why a wrapper rather than `fleet_probe.py --heartbeat` on a schedule:

* **The stamp must be written in a `finally`.** A node that only heartbeats when
  healthy is indistinguishable from a node that is off — and that is the whole
  defect this fleet keeps rediscovering. `fleet_probe.py` lives in cowork's lane,
  so the always-write guarantee is added here rather than by editing it.
* **`python3` does not exist on this node.** It resolves to the Microsoft Store
  alias stub. ZoltarGenesis has the mirror-image problem (bare `python` is 2.7).
  A scheduled task must therefore name an absolute interpreter, never `python3`
  or `python`, or it silently never runs — which looks exactly like a dead node.
* **It re-attests every fire.** If this workspace is ever copied to another
  machine, its stamps say `verified: false` rather than impersonating ZoltarLead.

`outcome` is `ok` | `degraded` | `error`, and `notices` is not `errors`: a
satisfied `rename_pending` needs a follow-up but is not a failure, so it must not
drag the fire to `degraded`. Routing it through the error channel made a
successful rename render as degraded on ZoltarGenesis.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

# One module object, always. See the note in fleet_msg.py.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from scripts import fleet_identity as fi                          # noqa: E402
STAMP_DIR = REPO_ROOT / "data" / "fleet" / "heartbeat"
PROBE = REPO_ROOT / "dashboard" / "fleet_probe.py"
NODE_ID = "zoltarlead"


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _git(*args: str) -> str | None:
    try:
        p = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=30)
        return p.stdout.strip() or None if p.returncode == 0 else None
    except Exception:                                          # noqa: BLE001
        return None


def fire(kind: str = "scheduled", node_id: str = NODE_ID) -> dict:
    """One heartbeat fire. Returns the stamp; always writes it."""
    stamp: dict = {
        "schema_version": 1,
        "node_id": node_id,
        "agent_id": f"{node_id}/claude-code",
        "at": _now(),
        "fire_kind": kind,
        "outcome": "error",          # pessimistic default; corrected on success
        "errors": [],
        "notices": [],
        "by": None,
        "repo": {"branch": None, "head_sha": None, "dirty": None},
        "probe": {"ran": False, "returncode": None},
    }
    try:
        # 0. attest before claiming to be this node
        try:
            by = fi.attest(node_id)
            stamp["by"] = by
            if by.get("notice"):
                stamp["notices"].append(by["notice"])
            if by["verified"] is False:
                stamp["errors"].append(
                    f"identity MISMATCH: {by.get('reason')}")
            elif by["verified"] is None:
                stamp["errors"].append(
                    f"identity UNVERIFIED: {by.get('reason')}")
        except Exception as exc:                               # noqa: BLE001
            stamp["errors"].append(f"attest failed: {exc}")

        # 1. repo position. Each metric fails independently -- one dead call
        #    must not null out the others.
        for key, args in (("branch", ("rev-parse", "--abbrev-ref", "HEAD")),
                          ("head_sha", ("rev-parse", "--short", "HEAD"))):
            v = _git(*args)
            stamp["repo"][key] = v
            if v is None:
                stamp["errors"].append(f"git {key} unreadable")
        porcelain = _git("status", "--porcelain")
        stamp["repo"]["dirty"] = None if porcelain is None else bool(porcelain)

        # 2. the probe's own cheap stamp, best-effort
        if PROBE.exists():
            try:
                env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
                p = subprocess.run([sys.executable, str(PROBE), "--heartbeat",
                                    "--node-id", node_id],
                                   cwd=REPO_ROOT, capture_output=True, text=True,
                                   timeout=180, env=env)
                stamp["probe"] = {"ran": True, "returncode": p.returncode}
                if p.returncode != 0:
                    stamp["errors"].append(
                        f"fleet_probe --heartbeat exited {p.returncode}: "
                        f"{(p.stderr or '')[-200:]}")
            except Exception as exc:                           # noqa: BLE001
                stamp["errors"].append(f"fleet_probe failed: {exc}")
        else:
            stamp["errors"].append(f"fleet_probe.py absent at {PROBE}")

        verified = (stamp["by"] or {}).get("verified")
        if not stamp["errors"]:
            stamp["outcome"] = "ok"
        elif verified is True:
            stamp["outcome"] = "degraded"     # alive and attested, something failed
        else:
            stamp["outcome"] = "error"
    except Exception:                                          # noqa: BLE001
        stamp["errors"].append("unhandled: " + traceback.format_exc()[-500:])
        stamp["outcome"] = "error"
    finally:
        # THE contract: this write happens no matter what went wrong above it.
        try:
            STAMP_DIR.mkdir(parents=True, exist_ok=True)
            (STAMP_DIR / f"{node_id}.json").write_text(
                json.dumps(stamp, indent=2) + "\n", encoding="utf-8")
        except Exception:                                      # noqa: BLE001
            traceback.print_exc()
    return stamp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kind", default="scheduled",
                    choices=["scheduled", "boot", "manual"])
    ap.add_argument("--node-id", default=NODE_ID)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    stamp = fire(args.kind, args.node_id)
    if args.json:
        print(json.dumps(stamp, indent=2))
    else:
        print(f"  {stamp['agent_id']}  outcome={stamp['outcome']}  "
              f"verified={(stamp['by'] or {}).get('verified')}  "
              f"branch={stamp['repo']['branch']}@{stamp['repo']['head_sha']}")
        for e in stamp["errors"]:
            print(f"    ERROR  {e}")
        for n in stamp["notices"]:
            print(f"    NOTICE {n}")
    # Exit 0 even when degraded: the scheduled task's own LastTaskResult is not
    # the liveness signal, the stamp is. A non-zero exit here would make Task
    # Scheduler retry-storm on a condition the stamp already records honestly.
    return 0


if __name__ == "__main__":
    sys.exit(main())
