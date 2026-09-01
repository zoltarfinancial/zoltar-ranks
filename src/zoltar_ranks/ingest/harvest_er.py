"""Phase 1a: archive the forward expected-return curves.

`production/er_for_last_date{,_live}.pkl` each hold ONE as-of date: 14 forward
horizons x ~1,165 symbols. Upstream overwrites them on every run, so the only
history that exists is in git blobs. Unlike the rank files these are small
(~470 KB), so a per-commit walk is cheap and we simply read every commit.

Shape (verified 2026-09-01):
    period (1..14) | er (float) | Date (datetime, one value) | Symbol
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.sources.git_archive import UpstreamMirror

log = logging.getLogger("harvest_er")
ER_KEYS = ["as_of_date", "symbol", "period", "variant"]


def normalize(df: pd.DataFrame, variant: str, sha: str) -> pd.DataFrame:
    required = {"period", "er", "Date", "Symbol"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"expected-return snapshot missing columns: {missing}")
    out = pd.DataFrame({
        "as_of_date": pd.to_datetime(df["Date"]).dt.date,
        "symbol": df["Symbol"].astype(str).str.strip().str.upper(),
        "period": df["period"].astype(int),
        "er": df["er"].astype(float),
        "variant": variant,
        "first_seen_sha": sha,
    })
    return out.drop_duplicates(subset=ER_KEYS)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest expected-return curves from upstream git history")
    ap.add_argument("--mode", choices=["backfill", "daily"], default="daily")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(args.config)
    mirror = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    mirror.ensure()
    con = duckdb_io.connect(cfg.duckdb_path)

    total_new = 0
    for file_path, variant in cfg.er_files.items():
        snaps = mirror.commits_touching(file_path)
        if not snaps:
            log.warning("no commits touch %s", file_path)
            continue
        done = {r[0] for r in con.execute(
            "SELECT commit_sha FROM harvest_manifest WHERE file_path = ?", [file_path]).fetchall()}
        todo = [s for s in snaps if s.sha not in done]
        if args.mode == "daily":
            todo = todo[:5]          # newest few; backfill covers the rest
        todo = sorted(todo, key=lambda s: s.committed_at)
        log.info("%s: %d commits, %d to read", file_path, len(snaps), len(todo))
        for snap in todo:
            try:
                df = normalize(mirror.read_pickle(snap.sha, file_path), variant, snap.sha)
            except Exception as exc:
                log.warning("skip %s@%s: %s", file_path, snap.sha[:8], str(exc)[:160])
                continue
            seen, ins = duckdb_io.upsert_new_rows(con, "expected_returns", df, ER_KEYS)
            con.execute(
                "INSERT OR REPLACE INTO harvest_manifest "
                "(file_path, commit_sha, committed_at, rows_seen, rows_inserted) VALUES (?,?,?,?,?)",
                [file_path, snap.sha, snap.committed_at.replace(tzinfo=None), seen, ins])
            total_new += ins
            if ins:
                log.info("  %s %s as_of=%s new=%d", file_path.split('/')[-1], snap.sha[:8],
                         df["as_of_date"].iloc[0], ins)

    summary = con.execute("""
        SELECT variant, count(DISTINCT as_of_date) AS as_of_dates,
               min(as_of_date) AS first, max(as_of_date) AS last, count(*) AS rows
        FROM expected_returns GROUP BY 1 ORDER BY 1
    """).df()
    log.info("expected_returns:\n%s", summary.to_string(index=False))
    log.info("new rows: %d", total_new)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
