#!/usr/bin/env python3
"""Emit data/results/build_status.json for the Zoltar Research Console (SS08-SS11).

Lives in dashboard/ because Cowork owns that folder; it is called from
scripts/daily.py::STEPS and from .git/hooks/post-commit. It only ever READS the
rest of the repo. stdlib only, plus PyYAML if a manifest.yaml is used.

Everything this script writes is DERIVED. The only hand-maintained input is
data/build/manifest.yaml, which declares what *should* exist.

    python dashboard/emit_build_status.py --init    # write a starter manifest
    python dashboard/emit_build_status.py --check   # audit the inputs, exit 1 if broken
    python dashboard/emit_build_status.py           # emit the status file

`--check` is the sync command. Run it whenever you are unsure whether the
monitor is telling the truth: it audits every input the emitter depends on and
names the file and the fix for each one that is missing, stale or mismatched.
It is the fastest way for Claude Code, Andrew and Cowork to agree on the state
of the build without reading each other's transcripts.

See dashboard/BUILD_MONITOR.md for the schema and the derivation rules.

v2 changes (2026-09-02, Cowork):
  * A job declared `built` with zero logged runs now reports `not_built`, not
    `healthy`. That was the one path where the monitor could show green for a
    job that has never executed -- the exact failure it exists to prevent.
  * `expected_24h` is counted over a real 24 hours instead of over the 24 beat
    windows, so it is comparable with `runs_24h`.
  * Naive timestamps are stamped with the configured local zone explicitly and
    reported by --check, rather than silently absorbed.
  * `--check` added.
  * Dead `if True:` removed from activity().
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
BEATS = 24

REPO = Path(__file__).resolve().parents[1]
BUILD_DIR = REPO / "data" / "build"
RUNS_DIR = BUILD_DIR / "runs"
OUT_JSON = REPO / "data" / "results" / "build_status.json"
OUT_JS = REPO / "data" / "results" / "build_status.js"
DATA_JS = REPO / "data" / "results" / "dashboard_data.js"
LEGACY_JS = REPO / "dashboard" / "build_status.js"
PYTEST_REPORT = REPO / "data" / "results" / "pytest_report.json"
RUN_HISTORY = REPO / "data" / "results" / "run_history.jsonl"
LAST_RUN = REPO / "data" / "results" / "last_run_status.json"
POST_COMMIT = REPO / ".git" / "hooks" / "post-commit"

# Gate status goes stale if the suite has not run in this many hours.
STALE_AFTER_H = 36
# A run history with no entry this recent means the scheduler has stopped.
HISTORY_STALE_H = 24


# --------------------------------------------------------------------------- io
def load_manifest(quiet: bool = False) -> dict:
    y, j = BUILD_DIR / "manifest.yaml", BUILD_DIR / "manifest.json"
    if y.exists():
        try:
            import yaml  # type: ignore
            return yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        except ImportError:
            sys.exit(f"{y} exists but PyYAML is not installed. pip install pyyaml, "
                     f"or convert it to {j.name}.")
    if j.exists():
        return json.loads(j.read_text(encoding="utf-8"))
    if quiet:
        return {}
    sys.exit(f"No manifest found at {y}. Run: python {Path(__file__).name} --init")


def now() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None


def parse(ts) -> datetime | None:
    """ISO 8601 -> aware datetime. Naive input is assumed to be machine-local."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.astimezone()


def is_naive(ts) -> bool:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).tzinfo is None
    except (ValueError, TypeError):
        return False


def mtime(p: Path) -> datetime | None:
    return datetime.fromtimestamp(p.stat().st_mtime).astimezone() if p.exists() else None


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


# ------------------------------------------------------------------------ runs
def _from_daily(rec: dict) -> dict | None:
    """Normalise a scripts/daily.py status record into a run entry.

    `rows` is taken from a top-level `rows` if daily.py reports one, else summed
    from per-step `rows`. Absent in both, it stays null and the console shows a
    dash -- which is correct: an unreported row count is not a zero row count.
    """
    at = parse(rec.get("finished_at"))
    if not at:
        return None
    failed = rec.get("failed") or []
    steps = rec.get("steps") or {}
    rows = rec.get("rows")
    if rows is None:
        per_step = [s.get("rows") for s in steps.values() if isinstance(s, dict)]
        per_step = [r for r in per_step if isinstance(r, (int, float))]
        rows = sum(per_step) if per_step else None
    return {
        "_at": at,
        "mode": rec.get("mode"),
        "status": "error" if failed else "ok",
        "duration_s": round(sum(s.get("seconds", 0) for s in steps.values()
                                if isinstance(s, dict)), 1) or None,
        "rows": rows,
        "error": ", ".join(failed) or None,
    }


def daily_runs(modes: list[str]) -> list[dict]:
    """Runs of scripts/daily.py whose `mode` is in `modes`.

    Preferred source is data/results/run_history.jsonl, one JSON object per run,
    appended by daily.py in a finally block. last_run_status.json holds only the
    latest run, so it is folded in as the newest entry when history is missing
    or behind. A run history of one line produces 23 `miss` beats -- that is the
    honest reading, not a bug.
    """
    out = []
    if RUN_HISTORY.exists():
        for line in RUN_HISTORY.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = _from_daily(json.loads(line))
            except json.JSONDecodeError:
                continue
            if rec:
                out.append(rec)
    if LAST_RUN.exists():
        try:
            rec = _from_daily(json.loads(LAST_RUN.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            rec = None
        if rec and not any(r["_at"] == rec["_at"] for r in out):
            out.append(rec)
    if modes:
        out = [r for r in out if r.get("mode") in modes]
    out.sort(key=lambda r: r["_at"])
    return out


def runs_for(job: dict) -> list[dict]:
    return daily_runs(job["modes"]) if job.get("modes") else read_runs(job["id"])


def read_runs(job_id: str) -> list[dict]:
    p = RUNS_DIR / f"{job_id}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn write is not a reason to fail the whole emit
        rec["_at"] = parse(rec.get("at"))
        if rec["_at"]:
            out.append(rec)
    out.sort(key=lambda r: r["_at"])
    return out


def in_schedule(dt: datetime, job: dict) -> bool:
    """Is this instant inside the job's active window?"""
    days = job.get("days", [0, 1, 2, 3, 4])          # Mon=0
    if dt.weekday() not in days:
        return False
    start = job.get("start_hour")
    end = job.get("end_hour")
    if start is None or end is None:
        return True                                   # daily / one-shot jobs
    return start <= dt.hour + dt.minute / 60 < end


def windows(job: dict, t0: datetime, count: int) -> list[tuple[datetime, datetime]]:
    cad = timedelta(minutes=int(job.get("cadence_minutes") or 1440))
    return [(t0 - cad * (i + 1), t0 - cad * i) for i in range(count - 1, -1, -1)]


def beats_for(job: dict, runs: list[dict], t0: datetime) -> list[str]:
    """24 windows, oldest first, stepping back by the job's cadence."""
    if job.get("status_override") == "not_built" or (not runs and not job.get("built")):
        return ["na"] * BEATS
    out = []
    for start, end in windows(job, t0, BEATS):
        if not in_schedule(start, job):
            out.append("na")
            continue
        hit = [r for r in runs if start <= r["_at"] < end]
        if not hit:
            out.append("miss")
        elif any(r.get("status") == "error" for r in hit):
            out.append("err")
        else:
            out.append("ok")
    return out


def expected_in_24h(job: dict, t0: datetime) -> int:
    """Scheduled windows in the last real 24 hours, so it compares with runs_24h."""
    cad_min = int(job.get("cadence_minutes") or 1440)
    n = max(1, int(24 * 60 / cad_min))
    return sum(1 for start, _ in windows(job, t0, n) if in_schedule(start, job))


def job_entry(job: dict, t0: datetime) -> dict:
    runs = runs_for(job)
    last = runs[-1] if runs else None
    cad = timedelta(minutes=int(job.get("cadence_minutes") or 1440))
    one_shot = job.get("one_shot", False)

    nxt = None
    if last and not one_shot:
        nxt = last["_at"] + cad
        while nxt < t0 and not in_schedule(nxt, job):
            nxt += cad

    # A job with no heartbeat is not built, whatever the manifest claims.
    # `built: true` in the manifest is a statement of intent; the run log is the
    # evidence. Trusting intent over evidence is how a monitor lies.
    never_ran_note = None
    if not runs:
        status = "not_built"
        if job.get("built"):
            never_ran_note = ("declared built in the manifest but no run has ever "
                              "been logged for mode(s) "
                              + ", ".join(job.get("modes") or [job["id"]])
                              + " -- check that the job stamps that mode and appends "
                                "to data/results/run_history.jsonl")
    elif job.get("status_override"):
        status = job["status_override"]
    elif last and last.get("status") == "error":
        status = "failed"
    elif one_shot:
        status = "done"
    elif nxt and t0 > nxt + cad:
        status = "late"
    else:
        status = "healthy"

    day_ago = t0 - timedelta(hours=24)
    recent = [r for r in runs if r["_at"] >= day_ago]
    bts = beats_for({**job, "built": bool(job.get("built")) or bool(runs)}, runs, t0)

    return {
        "id": job["id"],
        "name": job["name"],
        "why": job.get("why", ""),
        "schedule": job.get("schedule", ""),
        "criticality": job.get("criticality", "normal"),
        "status": status,
        "last_run_at": iso(last["_at"]) if last else None,
        "last_status": last.get("status") if last else None,
        "last_duration_s": last.get("duration_s") if last else None,
        "next_expected_at": iso(nxt),
        "rows_added_last": last.get("rows") if last else None,
        "runs_24h": len(recent),
        "expected_24h": expected_in_24h(job, t0),
        "failures_24h": sum(1 for r in recent if r.get("status") == "error"),
        "error": never_ran_note or (last or {}).get("error"),
        "beats": bts,
    }


# ----------------------------------------------------------------------- gates
def pytest_results() -> tuple[dict[str, str], datetime | None]:
    """{nodeid: outcome} from `pytest --json-report`."""
    if not PYTEST_REPORT.exists():
        return {}, None
    try:
        rep = json.loads(PYTEST_REPORT.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, None
    ran = parse(rep.get("created")) or mtime(PYTEST_REPORT)
    if isinstance(rep.get("created"), (int, float)):
        ran = datetime.fromtimestamp(rep["created"]).astimezone()
    return {t.get("nodeid", ""): t.get("outcome", "") for t in rep.get("tests", [])}, ran


def gate_entry(g: dict, tests: dict[str, str], ran: datetime | None, t0: datetime) -> dict:
    node = g.get("test")
    status, last = "not_built", None
    if node:
        matches = [o for nid, o in tests.items() if node in nid]
        if matches:
            last = ran
            if any(o == "failed" for o in matches):
                status = "fail"
            elif all(o == "passed" for o in matches):
                status = "pass"
            else:
                status = "stale"
    if g.get("status_override"):
        status = g["status_override"]
    if status == "pass" and last and (t0 - last) > timedelta(hours=STALE_AFTER_H):
        status = "stale"
    return {
        "id": g["id"], "name": g["name"],
        "severity": g.get("severity", "warning"),
        "status": status,
        "last_run_at": iso(last),
        "detail": g.get("detail", ""),
        "guards": g.get("guards", ""),
    }


# -------------------------------------------------------------------- phases
def proof_state(d: dict, jobs_by_id: dict) -> str:
    """A deliverable that is proven by observed behaviour, not by a file existing.

    Phase 1a does not close because schedule_harvest.ps1 exists -- that file
    existed on 2026-09-01 while the task had silently expired. It closes when the
    job has actually produced an unbroken session of beats. `path.exists()` can
    never express that, so `kind: proof` derives its state from the job's own
    heartbeat instead.
    """
    spec = d.get("proof") or {}
    job = jobs_by_id.get(spec.get("job"))
    if not job:
        return "todo"
    scheduled = [b for b in job["beats"] if b != "na"]
    if not scheduled:
        return "todo"
    if any(b in ("miss", "err") for b in scheduled):
        return "wip" if "ok" in scheduled else "todo"
    if spec.get("full_session") and len(scheduled) < job["expected_24h"]:
        return "wip"
    return "done"


def deliverable_state(d: dict, phase: dict, tests: dict[str, str], jobs_by_id: dict) -> str:
    if d.get("state_override"):
        return d["state_override"]
    if phase.get("status") == "blocked":
        return "blocked"
    if d.get("kind") == "proof":
        return proof_state(d, jobs_by_id)
    p = REPO / d["path"]
    if not p.exists():
        return "todo"
    if d.get("kind") == "test" and d.get("test"):
        outcomes = [o for nid, o in tests.items() if d["test"] in nid]
        return "done" if outcomes and all(o == "passed" for o in outcomes) else "wip"
    return "done" if d.get("complete", True) else "wip"


def phase_entry(ph: dict, tests: dict[str, str], jobs_by_id: dict) -> dict:
    dels = []
    for d in ph.get("deliverables", []):
        dels.append({"name": d["name"], "path": d.get("path", ""),
                     "kind": d.get("kind", "code"),
                     "state": deliverable_state(d, ph, tests, jobs_by_id)})
    score = sum({"done": 1.0, "wip": 0.5}.get(d["state"], 0.0) for d in dels)
    pct = round(100 * score / len(dels)) if dels else (100 if ph.get("status") == "done" else 0)
    return {
        "id": str(ph["id"]), "name": ph["name"], "subtitle": ph.get("subtitle", ""),
        "status": ph.get("status", "queued"), "owner": ph.get("owner", "claude-code"),
        "pct": 100 if ph.get("status") == "done" else pct,
        "critical_path": bool(ph.get("critical_path")),
        "started_at": ph.get("started_at"), "completed_at": ph.get("completed_at"),
        "eta": ph.get("eta"), "note": ph.get("note", ""),
        "deliverables": dels,
    }


# --------------------------------------------------------------------- feeds
def feed_entry(f: dict, t0: datetime) -> dict:
    p = REPO / f["path"]
    m = mtime(p)
    if not m:
        status = "missing"
    elif f.get("max_age_hours") and (t0 - m) > timedelta(hours=float(f["max_age_hours"])):
        status = "stale"
    else:
        status = "ok"
    return {"path": f["path"], "consumed_by": f.get("consumed_by", ""),
            "status": f.get("status_override", status),
            "updated_at": iso(m), "note": f.get("note", "")}


# ---------------------------------------------------------------------- build
def compute_gate(jobs, gates, blockers) -> dict:
    blocking = [g for g in gates if g["severity"] == "blocker"]
    failed = [g for g in blocking if g["status"] == "fail"]
    unbuilt = [g for g in blocking if g["status"] in ("not_built", "stale")]
    crit_bad = [j for j in jobs
                if j["criticality"] == "critical" and j["status"] not in ("healthy", "done")]
    high_bad = [j for j in jobs
                if j["criticality"] == "high" and j["status"] in ("failed", "late", "not_built")]

    if failed:
        return {"state": "red",
                "headline": f"{failed[0]['name']} is failing.",
                "detail": "A blocker gate is red, so every number downstream of it is "
                          "suspect. Stop and find the cause rather than loosening the "
                          "assertion."}
    if crit_bad:
        j = crit_bad[0]
        return {"state": "red",
                "headline": f"{j['name']} is not running ({j['status'].replace('_', ' ')}).",
                "detail": j["error"] or j["why"] or "A critical job is not running."}
    if unbuilt or high_bad:
        parts = []
        if unbuilt:
            parts.append(f"{len(unbuilt)} blocker gate(s) never run or stale")
        if high_bad:
            parts.append(f"{len(high_bad)} high-priority job(s) late or failed")
        return {"state": "amber",
                "headline": "Running, but not yet trustworthy - " + "; ".join(parts) + ".",
                "detail": "Results can be produced, but the assertions that make them "
                          "meaningful are not all in place."}
    return {"state": "green",
            "headline": "All critical jobs running and every blocker gate passing.",
            "detail": f"{len(blockers)} open blocker(s). Results on this page are "
                      f"produced under the full contract suite."}


def activity(manifest, t0) -> list[dict]:
    out = []
    log = git("log", "-15", "--pretty=%H%x1f%cI%x1f%s")
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append({"at": parts[1], "kind": "commit", "actor": "claude-code",
                        "summary": parts[2], "ref": parts[0][:7]})
    for job in manifest.get("jobs", []):
        runs = runs_for(job)
        if runs:
            r = runs[-1]
            ok = r.get("status") == "ok"
            out.append({
                "at": iso(r["_at"]), "kind": "run", "actor": "claude-code",
                "summary": f"{job['id']}: {'ok' if ok else 'ERROR'}"
                           + (f" - {r['rows']} rows" if r.get("rows") else "")
                           + (f" - {r.get('error')}" if not ok else ""),
                "ref": None})
    out.extend(manifest.get("activity", []))
    out.sort(key=lambda e: e.get("at") or "", reverse=True)
    return out[:12]


def build(manifest: dict) -> dict:
    t0 = now()
    tests, ran = pytest_results()
    jobs = [job_entry(j, t0) for j in manifest.get("jobs", [])]
    gates = [gate_entry(g, tests, ran, t0) for g in manifest.get("contract_tests", [])]
    jobs_by_id = {j["id"]: j for j in jobs}
    phases = [phase_entry(p, tests, jobs_by_id) for p in manifest.get("phases", [])]
    feeds = [feed_entry(f, t0) for f in manifest.get("feeds", [])]
    blockers = manifest.get("blockers", [])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso(t0),
        "is_example": False,
        "repo": {
            "name": manifest.get("repo_name", REPO.name),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "head_sha": git("rev-parse", "--short", "HEAD"),
            "head_message": git("log", "-1", "--pretty=%s"),
            "head_at": git("log", "-1", "--pretty=%cI"),
            "dirty": bool(git("status", "--porcelain")),
        },
        "gate": compute_gate(jobs, gates, blockers),
        "jobs": jobs,
        "phases": phases,
        "contract_tests": gates,
        "feeds": feeds,
        "blockers": blockers,
        "activity": activity(manifest, t0),
    }


# ---------------------------------------------------------------------- check
def check() -> int:
    """Audit every input the monitor depends on. Exit 1 if any is broken.

    This is the shared source of truth between the three parties. If it is
    clean, the console is showing reality; if it is not, it names the file and
    the fix. Nothing here inspects build_status.json itself -- a derived file
    cannot vouch for its own inputs.
    """
    t0 = now()
    rows: list[tuple[str, str, str]] = []      # (level, what, detail)

    def ok(what, detail=""):
        rows.append(("OK  ", what, detail))

    def warn(what, detail):
        rows.append(("WARN", what, detail))

    def bad(what, detail):
        rows.append(("FAIL", what, detail))

    # 1. manifest
    manifest = load_manifest(quiet=True)
    if not manifest:
        bad("data/build/manifest.yaml", "missing -- run --init, then edit to mirror docs/PLAN.md")
    else:
        counts = {k: len(manifest.get(k) or []) for k in
                  ("jobs", "phases", "contract_tests", "feeds", "blockers")}
        ok("data/build/manifest.yaml", ", ".join(f"{v} {k}" for k, v in counts.items()))
        try:
            import yaml  # noqa: F401
        except ImportError:
            if (BUILD_DIR / "manifest.yaml").exists():
                bad("PyYAML", "manifest.yaml present but PyYAML not installed: pip install pyyaml")

    # 2. run history
    if not RUN_HISTORY.exists():
        bad("data/results/run_history.jsonl",
            "missing -- scripts/daily.py must APPEND one JSON line per run in a finally "
            "block. Without it the monitor sees at most one run and every beat is a miss.")
    else:
        lines = [l for l in RUN_HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
        recs = [_from_daily(json.loads(l)) for l in lines
                if l.strip().startswith("{")]
        recs = [r for r in recs if r]
        newest = max((r["_at"] for r in recs), default=None)
        if not recs:
            bad("data/results/run_history.jsonl", f"{len(lines)} line(s), none parseable")
        elif newest and (t0 - newest) > timedelta(hours=HISTORY_STALE_H):
            bad("data/results/run_history.jsonl",
                f"{len(recs)} run(s), newest {newest:%Y-%m-%d %H:%M} is more than "
                f"{HISTORY_STALE_H}h old -- the scheduled task has stopped. Check "
                "Get-ScheduledTaskInfo -TaskName ZoltarRanksHarvest for a blank NextRunTime.")
        else:
            ok("data/results/run_history.jsonl",
               f"{len(recs)} run(s), newest {newest:%Y-%m-%d %H:%M}")
        if any(is_naive(json.loads(l).get("finished_at")) for l in lines if l.strip().startswith("{")):
            warn("run_history timestamps",
                 "naive (no UTC offset) -- assumed machine-local. Emit "
                 "datetime.now().astimezone().isoformat() so the file survives a zone change.")

    # 3. last_run_status
    if not LAST_RUN.exists():
        warn("data/results/last_run_status.json", "missing -- daily.py has never completed")
    else:
        try:
            rec = json.loads(LAST_RUN.read_text(encoding="utf-8"))
            ok("data/results/last_run_status.json",
               f"mode={rec.get('mode')} finished_at={rec.get('finished_at')}")
        except json.JSONDecodeError:
            bad("data/results/last_run_status.json", "not valid JSON")

    # 4. THE mismatch check: does every job's declared mode appear in the history?
    seen_modes = {r.get("mode") for r in daily_runs([])}
    seen_modes.discard(None)
    for job in manifest.get("jobs", []):
        modes = job.get("modes")
        if not modes:
            p = RUNS_DIR / f"{job['id']}.jsonl"
            (ok if p.exists() else bad)(
                f"job {job['id']}",
                f"heartbeat {p.relative_to(REPO)}" if p.exists()
                else f"no heartbeat log at {p.relative_to(REPO)}")
            continue
        missing = [m for m in modes if m not in seen_modes]
        if missing:
            bad(f"job {job['id']}",
                f"declares mode(s) {missing} that never appear in the run history "
                f"(history has: {sorted(seen_modes) or 'nothing'}). The job will read "
                f"not_built forever. Either daily.py must stamp that mode or the "
                f"manifest must change.")
        else:
            ok(f"job {job['id']}", f"mode(s) {modes} present in history")

    # 5. pytest report
    if not PYTEST_REPORT.exists():
        bad("data/results/pytest_report.json",
            "missing -- every contract gate reads not_built. Run: pytest tests -q "
            "--json-report --json-report-file=data/results/pytest_report.json")
    else:
        tests, ran = pytest_results()
        age = (t0 - ran) if ran else None
        gates = [g.get("test") for g in manifest.get("contract_tests", []) if g.get("test")]
        unmatched = [g for g in gates if not any(g in nid for nid in tests)]
        detail = f"{len(tests)} test(s), ran {ran:%Y-%m-%d %H:%M}" if ran else f"{len(tests)} test(s)"
        if age and age > timedelta(hours=STALE_AFTER_H):
            warn("data/results/pytest_report.json", detail + f" -- older than {STALE_AFTER_H}h, gates read stale")
        else:
            ok("data/results/pytest_report.json", detail)
        if unmatched:
            warn("contract gates with no test",
                 ", ".join(unmatched) + " -- these read not_built and hold the gate at amber")

    # 6. feeds
    for f in manifest.get("feeds", []):
        e = feed_entry(f, t0)
        (ok if e["status"] == "ok" else (warn if e["status"] == "stale" else bad))(
            f"feed {f['path']}", f"{e['status']}" + (f", updated {e['updated_at']}" if e["updated_at"] else ""))

    # 7. wiring
    if POST_COMMIT.exists():
        ok(".git/hooks/post-commit", "present")
    else:
        warn(".git/hooks/post-commit",
             "missing -- the repo header and activity log only refresh on a harvest run")
    daily_src = (REPO / "scripts" / "daily.py")
    if daily_src.exists():
        txt = daily_src.read_text(encoding="utf-8", errors="ignore")
        (ok if "emit_build_status" in txt else bad)(
            "scripts/daily.py -> emitter",
            "wired into STEPS" if "emit_build_status" in txt
            else "emitter is NOT called from daily.py; the monitor will only "
                 "update when someone runs it by hand")
        (ok if "run_history" in txt else bad)(
            "scripts/daily.py -> run_history.jsonl",
            "appends history" if "run_history" in txt else "does not append run_history.jsonl")
    if git("rev-parse", "--git-dir"):
        ok("git", f"{git('rev-parse', '--abbrev-ref', 'HEAD')} @ {git('rev-parse', '--short', 'HEAD')}"
                  + (" (dirty)" if git("status", "--porcelain") else ""))
    else:
        bad("git", "not a git repo or git not on PATH -- repo header and activity will be blank")

    # 8. outputs
    if LEGACY_JS.exists():
        warn("dashboard/build_status.js",
             "stale copy at the old location -- the console now loads "
             "../data/results/build_status.js. Delete it so it cannot be read by mistake.")
    if not DATA_JS.exists():
        warn("data/results/dashboard_data.js",
             "not written -- SS01-SS07 will fall back to the seed over file://. One extra "
             "line in export_dashboard_data.py: write 'window.__ZOLTAR_DATA__ = ' + payload + ';'")
    for p, why in ((OUT_JSON, "console feed"), (OUT_JS, "file:// feed")):
        m = mtime(p)
        if not m:
            warn(str(p.relative_to(REPO)), f"not written yet ({why})")
        else:
            ok(str(p.relative_to(REPO)), f"{why}, written {m:%Y-%m-%d %H:%M}")

    width = max(len(w) for _, w, _ in rows) if rows else 0
    print(f"\nmonitor input check - {t0:%Y-%m-%d %H:%M %Z}\n")
    for level, what, detail in rows:
        print(f"  [{level}] {what.ljust(width)}  {detail}")
    fails = sum(1 for l, _, _ in rows if l == "FAIL")
    warns = sum(1 for l, _, _ in rows if l == "WARN")
    print(f"\n  {fails} blocking, {warns} warning, {len(rows) - fails - warns} ok\n")
    if fails:
        print("  The console cannot report reality until the FAIL rows are fixed.\n")
    return 1 if fails else 0


STARTER = {
    "repo_name": "zoltar-ranks",
    "jobs": [{
        "id": "harvest_intraday", "name": "Intraday harvest",
        "why": "Pulls all_*_PROD_latest.pkl before the 200-timestamp rolling buffer "
               "discards it. The only irreversible job on the board.",
        "schedule": "*/30 09:00-16:00 CDT, Mon-Fri", "cadence_minutes": 30,
        "start_hour": 9, "end_hour": 16, "days": [0, 1, 2, 3, 4],
        "modes": ["intraday"],
        "criticality": "critical", "built": True,
    }],
    "phases": [], "contract_tests": [], "feeds": [], "blockers": [], "activity": [],
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--init", action="store_true", help="write a starter manifest and exit")
    ap.add_argument("--check", action="store_true",
                    help="audit the monitor's inputs and exit non-zero if any is broken")
    ap.add_argument("--print", dest="show", action="store_true", help="print to stdout too")
    args = ap.parse_args()

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    if args.check:
        sys.exit(check())

    if args.init:
        target = BUILD_DIR / "manifest.json"
        try:
            import yaml  # type: ignore
            target = BUILD_DIR / "manifest.yaml"
            target.write_text(yaml.safe_dump(STARTER, sort_keys=False, allow_unicode=True),
                              encoding="utf-8")
        except ImportError:
            target.write_text(json.dumps(STARTER, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        print(f"wrote {target} - edit it to mirror docs/PLAN.md, then re-run without --init")
        return

    status = build(load_manifest())
    payload = json.dumps(status, indent=1, ensure_ascii=False)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(payload, encoding="utf-8")
    OUT_JS.parent.mkdir(parents=True, exist_ok=True)
    OUT_JS.write_text("window.__ZOLTAR_BUILD__ = " + payload + ";\n", encoding="utf-8")

    g = status["gate"]
    print(f"{OUT_JSON.relative_to(REPO)}  gate={g['state'].upper()}  {g['headline']}")
    if args.show:
        print(payload)


if __name__ == "__main__":
    main()
