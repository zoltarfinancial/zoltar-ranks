"""The scheduled job. Idempotent, loud on failure, safe to run every 30 minutes.

Run order matters: ranks first (that is the perishable data), then the small
artifacts, then whatever downstream steps exist yet. Every step is wrapped so
one failure does not prevent the others from running, but the process exits
non-zero if ANY step failed.

It leaves two records, and the difference between them is the point:

* `data/results/last_run_status.json` -- the latest run, overwritten each time.
* `data/results/run_history.jsonl`    -- **append-only**, one line per run.

`last_run_status.json` alone cannot answer "is this job still running?", because
there is only ever one run in it. The heartbeat is what makes a missed window
visible, and a missed window on the intraday harvest is history that no longer
exists. Both are written in a `finally` block: a job that logs only its
successes is indistinguishable from one that is not running at all, which is
precisely the state the monitor exists to detect.

Usage:
    python scripts/daily.py                 # normal scheduled run
    python scripts/daily.py --backfill      # first run only
    python scripts/daily.py --mode evening  # override the RECORDED mode only
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from zoltar_ranks.config import Config
from zoltar_ranks.ingest import (harvest_daily_ranks, harvest_er, harvest_ranks,
                                 harvest_sessions, harvest_shap)
from zoltar_ranks.analysis import export_dashboard_data

log = logging.getLogger("daily")

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The archive's wall clock. Every recorded timestamp carries an offset, because
#: this file is read by a monitor that may not run on this machine and spans a
#: DST boundary in November.
CDT = ZoneInfo("America/Chicago")

#: Recorded run modes. These are what `data/build/manifest.yaml` matches its jobs
#: against, so the two must not drift -- `dashboard/emit_build_status.py --check`
#: cross-checks them and reports a mode nothing ever writes.
MODES = ("premarket", "intraday", "evening", "weekend", "backfill")


def derive_mode(at: datetime | None = None) -> str:
    """Which run window this is, from the wall clock.

    Boundaries are the run-type boundaries of `docs/FINDINGS.md` F4 and must stay
    in sync with `start_hour`/`end_hour` in `data/build/manifest.yaml`. They are
    deliberately the *session* boundaries, not the scheduler's 07:00-21:30 window:
    the scheduler fires every 30 minutes regardless, and stamping every tick
    `daily` made an intraday re-score and the evening retrain capture
    indistinguishable in the record.
    """
    at = (at or datetime.now(CDT)).astimezone(CDT)
    if at.weekday() > 4:
        return "weekend"
    h = at.hour + at.minute / 60
    if h < 9:
        return "premarket"      # the morning model build
    if h < 15.5:
        return "intraday"       # the 30-minute re-scores
    return "evening"            # AFTERCLOSE UPDATE, the full retrain


#: Which tables each step writes, so "rows added" can be measured rather than
#: reported by the harvester. A count taken from the database cannot drift from
#: what the harvester actually did, and needs no change to five harvesters.
_STEP_TABLES: dict[str, tuple[str, ...]] = {
    "ranks": ("ranks",),
    "daily_ranks": ("ranks",),
    "run_sessions": ("run_sessions",),
    "expected_returns": ("expected_returns",),
    "shap": ("shap_summary", "shap_labels"),
}


def _count_rows(db_path: Path, tables: tuple[str, ...]) -> int | None:
    """Total rows across `tables`, or None if the count cannot be taken.

    **None means "unknown" and must never be rendered as 0.** A zero row count on
    the intraday job is a real and alarming signal; producing one from a locked
    database or a missing table would be a fabricated alarm. The monitor shows a
    dash for null.
    """
    if not tables or not Path(db_path).exists():
        return None
    try:
        import duckdb
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            return sum(con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                       for t in tables)
        finally:
            con.close()
    except Exception:               # noqa: BLE001 - locked, absent, mid-migration
        return None


STEPS = [
    ("ranks", harvest_ranks.main),
    # daily_ranks/ keeps one PROD file per build, so it preserves snapshots of the
    # rolling `all_*` buffer that production/*_latest.pkl has already dropped.
    ("daily_ranks", harvest_daily_ranks.main),
    # Ground-truth session labels from filenames only (Rule 4 safe). The only
    # external check on classify_run(), and the canary tests depend on it.
    ("run_sessions", harvest_sessions.main),
    ("expected_returns", harvest_er.main),
    ("shap", harvest_shap.main),
    # The one file the two workstreams meet at (docs/DASHBOARD.md). Last of the
    # data steps, so it reports on everything above it.
    ("dashboard_data", export_dashboard_data.main),
    # Later phases append here:
    #   ("prices",     refresh_prices.main),
    #   ("benchmark",  run_benchmarks.main),
    #   ("dashboard",  build_dashboard.main),
    #
    # The build-monitor emitter is deliberately NOT a step: it must run AFTER the
    # status write in the `finally` block, or it reports the previous run. See
    # `_emit_monitor` below.
]


def _emit_monitor() -> None:
    """Refresh the build monitor. Never allowed to affect the run's outcome.

    Runs after the status write, so it reports THIS run rather than the previous
    one. A monitor that can fail a harvest is worse than a stale monitor, so
    every failure here is logged and swallowed.
    """
    emitter = REPO_ROOT / "dashboard" / "emit_build_status.py"
    if not emitter.exists():
        log.warning("build monitor emitter not found at %s; console will be stale",
                    emitter)
        return
    try:
        proc = subprocess.run([sys.executable, str(emitter)], cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode == 0:
            log.info("build monitor: %s", (proc.stdout or "").strip().splitlines()[-1:]
                     or "emitted")
        else:
            log.warning("build monitor emitter exited %d: %s",
                        proc.returncode, (proc.stderr or "")[-400:])
    except Exception as exc:            # noqa: BLE001 - never fail the harvest
        log.warning("build monitor emitter failed: %s", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="run every harvester in backfill mode (first run only)")
    ap.add_argument("--mode", default=None, choices=[m for m in MODES if m != "backfill"],
                    help="override the run mode RECORDED in run_history.jsonl "
                         "(premarket|intraday|evening|weekend). Does not change "
                         "harvester behaviour -- the harvesters only understand "
                         "backfill/daily and receive that independently.")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)

    # Two different things that were previously one. `mode` is the record's
    # window label; `harvest_mode` is what the harvesters understand.
    mode = "backfill" if args.backfill else (args.mode or derive_mode())
    harvest_mode = "backfill" if args.backfill else "daily"

    results: dict[str, dict] = {}
    failed: list[str] = []
    try:
        for name, entry in STEPS:
            started = datetime.now(CDT)
            tables = _STEP_TABLES.get(name, ())
            before = _count_rows(cfg.duckdb_path, tables)
            try:
                step_argv = ["--mode", harvest_mode]
                if args.config:
                    step_argv += ["--config", args.config]
                rc = entry(step_argv)
                if rc != 0:
                    raise RuntimeError(f"{name} returned {rc}")
                after = _count_rows(cfg.duckdb_path, tables)
                rows = (after - before) if (before is not None and after is not None) else None
                results[name] = {"ok": True,
                                 "seconds": (datetime.now(CDT) - started).total_seconds(),
                                 "rows": rows}
                log.info("step %s OK (rows=%s)", name, "?" if rows is None else rows)
            except Exception:
                failed.append(name)
                results[name] = {"ok": False, "error": traceback.format_exc()[-2000:],
                                 "seconds": (datetime.now(CDT) - started).total_seconds(),
                                 "rows": None}
                log.error("step %s FAILED\n%s", name, traceback.format_exc())
    finally:
        # In `finally`, not on the happy path: a crash must still leave a beat,
        # because "no line" and "a line saying it failed" mean very different
        # things to the monitor and only one of them is honest.
        counted = [r["rows"] for r in results.values() if r.get("rows") is not None]
        status = {
            "finished_at": datetime.now(CDT).isoformat(timespec="seconds"),
            "mode": mode,
            "failed": failed,
            "rows": sum(counted) if counted else None,
            "steps": results,
        }
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        (cfg.results_dir / "last_run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8")
        # Append, never rewrite -- for the same reason the archive is append-only.
        # A torn line is survivable (the emitter skips unparseable lines); a
        # rewritten history is not.
        with (cfg.results_dir / "run_history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(status, separators=(",", ":")) + "\n")
        log.info("status written to %s (mode=%s)",
                 cfg.results_dir / "last_run_status.json", mode)
        _emit_monitor()

    if failed:
        log.error("FAILED STEPS: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
