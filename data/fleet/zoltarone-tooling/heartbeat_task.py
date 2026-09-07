#!/usr/bin/env python3
"""Hourly fleet-lane heartbeat for node zoltarone. Run by Task Scheduler.

Contract, from fleet\\zoltarone-cowork\\20260904-1850-post-merge-plan.md §3:

  2. "It must stamp a heartbeat on failure too, in a finally block. A node that
     heartbeats only when healthy is indistinguishable from one that is off."

So the probe running is NOT the success condition -- a heartbeat file existing
with a fresh timestamp is. If fleet_probe.py fails, dies, or is missing, this
still writes a stamp carrying the error, because "no heartbeat" and "heartbeat
saying the probe broke" are very different facts to the fleet and the second one
is the one worth having.

Fleet lane only. This NEVER writes a review cycle (CLAUDE.md §5) and never runs
git -- an unattended push is not something a scheduled task should do.

Liveness is published two ways:
  1. data/fleet/heartbeat/zoltarone.json in the clone -- for the courier to
     commit on its next session
  2. OneDrive fleet\\heartbeat\\zoltarone.json -- readable by both Cowork brains
     immediately, with no clone and no git

stdlib only, like fleet_probe.py.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from pathlib import Path as pathlib_Path

WORKSPACE = Path(__file__).resolve().parent
CLONE = WORKSPACE / "zoltar-ranks"
PROBE = CLONE / "dashboard" / "fleet_probe.py"
HEARTBEAT = CLONE / "data" / "fleet" / "heartbeat" / "zoltarone.json"
ONEDRIVE = Path(r"C:\Users\zolta\OneDrive\ZoltarUnlimited\fleet\heartbeat\zoltarone.json")
RUNLOG = WORKSPACE / "heartbeat_runs.jsonl"

NODE_ID = "zoltarone"
AGENTS = "zoltarone/claude-code"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def boot_time_iso() -> str | None:
    """Uptime, so a reader can tell a post-reboot stamp from a pre-reboot one.
    This is the whole point of the reboot liveness test."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')"],
            capture_output=True, text=True, timeout=30)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def resolve_probe() -> pathlib_Path | None:
    """The probe must NOT depend on which branch the clone is sitting on.

    dashboard/fleet_probe.py exists only on fleet/join-zoltarone. The order
    executor returns the clone to main after every cycle, so a heartbeat that
    reads the in-clone copy went degraded on every fire the moment the executor
    ran. The workspace copy is branch-independent; prefer it, and keep the
    in-clone copy as a fallback. --root still points at the clone so the stamp
    lands in the repo either way.
    """
    for cand in (WORKSPACE / "fleet_probe.py", PROBE):
        if cand.exists():
            return cand
    return None


def run_probe() -> tuple[bool, str]:
    probe = resolve_probe()
    if probe is None:
        return False, f"probe missing at both {WORKSPACE / 'fleet_probe.py'} and {PROBE}"
    try:
        out = subprocess.run(
            [sys.executable, str(probe), "--heartbeat",
             "--node-id", NODE_ID, "--agents", AGENTS, "--root", str(CLONE)],
            capture_output=True, text=True, timeout=180, cwd=str(CLONE))
    except subprocess.TimeoutExpired:
        return False, "probe timed out after 180s"
    except OSError as e:
        return False, f"probe could not start: {e}"
    if out.returncode != 0:
        return False, f"probe exit {out.returncode}: {(out.stderr or '').strip()[:400]}"
    return True, (out.stdout or "").strip()


def fallback_stamp(reason: str, boot: str | None) -> dict:
    """The heartbeat that gets written when the probe could not be trusted."""
    return {
        "schema_version": 1,
        "node_id": NODE_ID,
        "at": now_iso(),
        "load": {"cpu_pct": None, "ram_free_gb": None},
        "power": {"has_battery": None, "on_ac": None, "charge_pct": None},
        "repo": {"branch": None, "head_sha": None, "dirty": None},
        "agents": [],
        "degraded": True,
        "error": reason,
        "written_by": "heartbeat_task.py fallback",
        "boot_at": boot,
    }


def attest() -> dict:
    """Attest this node's identity every fire, per the identity-kit contract.

    verified is THREE-STATE and null is never collapsed into false:
      true  - a live hostname read matched an attested name (case-insensitive)
      false - a live read matched nothing attested
      null  - the hostname could not be read at all
    'unreadable' and 'wrong machine' are different facts and must stay so.

    identity_file and identity_schema are stamped so a future session never has
    to re-derive which file a failing guard actually read (KB lesson
    handoff__described-intent-as-completed-state).
    """
    ident = WORKSPACE / "fleet" / "identity.json"
    out = {"agent_id": "zoltarone/claude-code", "node_id": NODE_ID,
           "hostname": None, "lane": "claude-code", "verified": None,
           "identity_file": str(ident), "identity_schema": None,
           "at": now_iso()}
    try:
        live = os.environ.get("COMPUTERNAME") or __import__("socket").gethostname()
        out["hostname"] = live or None
    except Exception:
        live = None
    try:
        doc = json.loads(ident.read_text(encoding="utf-8"))
        out["identity_schema"] = doc.get("schema_version")
        names = [n for n in (doc.get("attested_hostnames") or []) if n]
        if not names and doc.get("attested_hostname"):      # v1 fallback
            names = [doc["attested_hostname"]]
        if live and names:
            out["verified"] = live.strip().lower() in {n.strip().lower() for n in names}
        # live unreadable, or nothing to compare against -> stays null
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"          # verified stays null
    return out


def halt_state() -> tuple[bool, str | None]:
    """fleet\\HALT means stopped on purpose. From a phone, 'stopped' and 'died'
    must not look the same, so the pulse reports it rather than going quiet."""
    f = WORKSPACE / "fleet" / "HALT"
    if not f.exists():
        return False, None
    try:
        return True, (f.read_text(encoding="utf-8").strip() or "(empty file)")
    except Exception:
        return True, "(unreadable)"


def main() -> int:
    started = now_iso()
    boot = boot_time_iso()
    ok, detail = False, "not run"

    try:
        ok, detail = run_probe()
    except Exception as e:                      # never let the task die silently
        ok, detail = False, f"unhandled {type(e).__name__}: {e}"
    finally:
        # --- the guarantee: a stamp exists either way ----------------------
        try:
            if ok and HEARTBEAT.exists():
                stamp = json.loads(HEARTBEAT.read_text(encoding="utf-8"))
                stamp["boot_at"] = boot          # annotate, do not fabricate
                stamp["degraded"] = False
            else:
                stamp = fallback_stamp(detail, boot)
            halted, why = halt_state()
            stamp["halted"] = halted
            stamp["halt_reason"] = why
            stamp["by"] = attest()
            HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
        except Exception as e:
            stamp = fallback_stamp(f"stamp write failed: {e}", boot)

        # --- publish where the brains can read it without a clone ---------
        try:
            ONEDRIVE.parent.mkdir(parents=True, exist_ok=True)
            ONEDRIVE.write_text(json.dumps(stamp, indent=2), encoding="utf-8")
            mirrored = True
        except Exception as e:
            mirrored = f"failed: {e}"

        # --- append-only run log; this is the evidence the task fired ------
        try:
            with RUNLOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "started": started, "finished": now_iso(),
                    "probe_ok": ok, "detail": detail[:500],
                    "boot_at": boot, "mirrored": mirrored,
                    "pid": os.getpid(),
                }) + "\n")
        except Exception:
            pass

    print(f"heartbeat {'ok' if ok else 'DEGRADED'} at {stamp['at']} (boot {boot})")
    if not ok:
        print(f"  reason: {detail}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
