"""Ground-truth session labels from `daily_ranks/*_rankings_*` FILENAMES.

RULE 4 is not bent here. These files contain in-sample `source='train'` rows and
are **never opened** -- only their names are read, from `git log --name-only`. A
filename is metadata about a run, not a row from it.

Why it earns its place: `classify_run()` assigns a run class from a wall-clock
time, and until this table existed there was nothing to check it against. The
first check found it 99.0% accurate with 5 boundary errors in 513 stamps
(FINDINGS F4). Without this table that function is unfalsifiable, and the whole
timing study keys off its output.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime

import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.sources.git_archive import UpstreamMirror, _run

log = logging.getLogger("harvest_sessions")

SESSION_RE = re.compile(
    r"^daily_ranks/(low|high)_risk_rankings_(\d{8})_(\d{6})-([A-Z]+) UPDATE\.pkl$")
SESSION_KEYS = ["build_stamp", "session_label", "risk_bucket"]


def parse_sessions(mirror: UpstreamMirror) -> pd.DataFrame:
    out = _run(["git", "log", "--diff-filter=A", "--name-only", "--no-renames",
                "--format=C\t%H\t%cI", "--", "daily_ranks/"], cwd=mirror.dir)
    rows, sha, committed = [], None, None
    for line in out.splitlines():
        if line.startswith("C\t"):
            _, sha, iso = line.split("\t")
            committed = datetime.fromisoformat(iso).replace(tzinfo=None)
            continue
        m = SESSION_RE.match(line.strip())
        if not m:
            continue
        bucket, d, t, label = m.groups()
        rows.append({
            "build_stamp": datetime.strptime(d + t, "%Y%m%d%H%M%S"),
            "session_label": label,
            "risk_bucket": bucket,
            "source_filename": line.strip().split("/")[-1],
            "commit_sha": sha,
            "committed_at": committed,
        })
    return pd.DataFrame(rows).drop_duplicates(subset=SESSION_KEYS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["backfill", "daily"], default="daily")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)
    mirror = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    if not args.no_sync:
        mirror.ensure()

    df = parse_sessions(mirror)
    if df.empty:
        log.warning("no session-labelled filenames found upstream")
        return 0
    con = duckdb_io.connect(cfg.duckdb_path)
    seen, ins = duckdb_io.upsert_new_rows(con, "run_sessions", df, SESSION_KEYS)
    con.close()
    log.info("run_sessions seen=%d inserted=%d (%d distinct build stamps)",
             seen, ins, df["build_stamp"].nunique())
    return 0


if __name__ == "__main__":
    sys.exit(main())
