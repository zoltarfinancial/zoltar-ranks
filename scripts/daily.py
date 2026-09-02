"""The scheduled job. Idempotent, loud on failure, safe to run every 30 minutes.

Run order matters: ranks first (that is the perishable data), then the small
artifacts, then whatever downstream steps exist yet. Every step is wrapped so
one failure does not prevent the others from running, but the process exits
non-zero if ANY step failed and writes data/results/last_run_status.json.

Usage:
    python scripts/daily.py              # normal scheduled run
    python scripts/daily.py --backfill   # first run only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from datetime import datetime

from zoltar_ranks.config import Config
from zoltar_ranks.ingest import (harvest_daily_ranks, harvest_er, harvest_ranks,
                                 harvest_sessions, harvest_shap)

log = logging.getLogger("daily")


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
    # Later phases append here:
    #   ("prices",     refresh_prices.main),
    #   ("benchmark",  run_benchmarks.main),
    #   ("dashboard",  build_dashboard.main),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true",
                    help="run every harvester in backfill mode (first run only)")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)
    mode = "backfill" if args.backfill else "daily"

    results, failed = {}, []
    for name, entry in STEPS:
        started = datetime.now()
        try:
            step_argv = ["--mode", mode]
            if args.config:
                step_argv += ["--config", args.config]
            rc = entry(step_argv)
            if rc != 0:
                raise RuntimeError(f"{name} returned {rc}")
            results[name] = {"ok": True, "seconds": (datetime.now() - started).total_seconds()}
            log.info("step %s OK", name)
        except Exception:
            failed.append(name)
            results[name] = {"ok": False, "error": traceback.format_exc()[-2000:],
                             "seconds": (datetime.now() - started).total_seconds()}
            log.error("step %s FAILED\n%s", name, traceback.format_exc())

    status = {"finished_at": datetime.now().isoformat(), "mode": mode,
              "failed": failed, "steps": results}
    status_path = cfg.results_dir / "last_run_status.json"
    status_path.write_text(json.dumps(status, indent=2))
    log.info("status written to %s", status_path)

    if failed:
        log.error("FAILED STEPS: %s", ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
