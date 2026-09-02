"""Phase 1a: archive SHAP feature attributions.

`production/combined_SHAP_summary_{Large,Mid,Small}_latest.pkl` are WIDE frames
indexed by SYMBOL (the segment's top-scoring names), with ~160-180 WOE-binned
feature columns plus one object column, 'Feature Category'. Verified shapes on
2026-09-01: Large 26x160, Mid 26x180, Small 33x171.

The files carry no internal timestamp, so `snapshot_ts` is the commit time. They
are small (40-60 KB), so every commit is read.

Why this matters: SHAP drift is the mechanism link in hypothesis H8 -- periods
when feature importance shifts are candidate explanations for periods of IC
decay, and a live drift measure is a candidate risk-off signal.
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.ingest.incomplete import Incomplete
from zoltar_ranks.sources.git_archive import UpstreamMirror

log = logging.getLogger("harvest_shap")
SHAP_KEYS = ["snapshot_ts", "segment", "symbol", "feature"]
LABEL_KEYS = ["snapshot_ts", "segment", "symbol", "label_name"]


def normalize(df: pd.DataFrame, segment: str, snapshot_ts, sha: str):
    """Return (values_long, labels_long). Index must be the symbol."""
    # The index must carry symbols. Do not test for `dtype == object`: pandas 3
    # gives string-backed indexes their own dtype and that check silently fails.
    # Test for what actually breaks us instead — a positional index.
    if isinstance(df.index, pd.RangeIndex) or pd.api.types.is_numeric_dtype(df.index):
        raise ValueError(
            f"SHAP frame for {segment} has a positional index, expected symbols. "
            "Upstream changed its format - re-read docs/FINDINGS.md F6 before proceeding.")
    wide = df.copy()
    wide.index = wide.index.astype(str).str.strip().str.upper()
    wide.index.name = "symbol"

    numeric_cols = [c for c in wide.columns if pd.api.types.is_numeric_dtype(wide[c])]
    object_cols = [c for c in wide.columns if c not in numeric_cols]

    values = (wide[numeric_cols].reset_index()
              .melt(id_vars="symbol", var_name="feature", value_name="value"))
    values["snapshot_ts"] = snapshot_ts
    values["segment"] = segment
    values["first_seen_sha"] = sha
    values = values.dropna(subset=["value"]).drop_duplicates(subset=SHAP_KEYS)

    labels = (wide[object_cols].reset_index()
              .melt(id_vars="symbol", var_name="label_name", value_name="label_value")
              if object_cols else
              pd.DataFrame(columns=["symbol", "label_name", "label_value"]))
    if not labels.empty:
        labels["snapshot_ts"] = snapshot_ts
        labels["segment"] = segment
        labels["label_value"] = labels["label_value"].astype(str)
        labels = labels.drop_duplicates(subset=LABEL_KEYS)
    return values, labels


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest SHAP summaries from upstream git history")
    ap.add_argument("--mode", choices=["backfill", "daily"], default="daily")
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-prefetch", action="store_true",
                    help="skip the bulk small-blob fetch during backfill (slower, "
                         "but keeps the mirror at ~1 MB instead of ~326 MB)")
    ap.add_argument("--prefetch-limit", default="600k",
                    help="max blob size for the bulk fetch (default 600k)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(args.config)
    mirror = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    mirror.ensure()
    if args.mode == "backfill" and not args.no_prefetch:
        # These files change on nearly every commit, so the backfill reads ~1,000
        # distinct small blobs. One bulk fetch turns ~8 minutes of round trips
        # into ~15 seconds. See UpstreamMirror.prefetch_small_blobs.
        log.info("bulk-fetching blobs under %s (one time, grows the mirror cache "
                 "to a few hundred MB under data/cache/)", args.prefetch_limit)
        mirror.prefetch_small_blobs(args.prefetch_limit)
    incomplete = Incomplete('harvest_shap', log)
    con = duckdb_io.connect(cfg.duckdb_path)

    total_new = 0
    for file_path, segment in cfg.shap_files.items():
        snaps = mirror.commits_touching(file_path)
        if not snaps:
            log.warning("no commits touch %s", file_path)
            continue
        done = {r[0] for r in con.execute(
            "SELECT commit_sha FROM harvest_manifest WHERE file_path = ?", [file_path]).fetchall()}
        todo = [s for s in snaps if s.sha not in done]
        if args.mode == "daily":
            todo = todo[:5]
        todo = sorted(todo, key=lambda s: s.committed_at)
        log.info("%s: %d commits, %d to read", file_path, len(snaps), len(todo))
        for snap in todo:
            ts = snap.committed_at.replace(tzinfo=None)
            try:
                values, labels = normalize(
                    mirror.read_pickle(snap.sha, file_path), segment, ts, snap.sha)
            except Exception as exc:
                incomplete.record(f"{file_path}@{snap.sha[:8]}", exc)
                continue
            seen, ins = duckdb_io.upsert_new_rows(con, "shap_summary", values, SHAP_KEYS)
            if not labels.empty:
                duckdb_io.upsert_new_rows(con, "shap_labels", labels, LABEL_KEYS)
            con.execute(
                "INSERT OR REPLACE INTO harvest_manifest "
                "(file_path, commit_sha, committed_at, rows_seen, rows_inserted) VALUES (?,?,?,?,?)",
                [file_path, snap.sha, ts, seen, ins])
            total_new += ins
            if ins:
                log.info("  %s %s %s symbols=%d new=%d", segment, snap.sha[:8], ts.date(),
                         values["symbol"].nunique(), ins)

    summary = con.execute("""
        SELECT segment, count(DISTINCT snapshot_ts) AS snapshots,
               count(DISTINCT symbol) AS symbols, count(DISTINCT feature) AS features,
               min(snapshot_ts) AS first, max(snapshot_ts) AS last
        FROM shap_summary GROUP BY 1 ORDER BY 1
    """).df()
    log.info("shap_summary:\n%s", summary.to_string(index=False))
    log.info("new rows: %d", total_new)
    con.close()
    return incomplete.exit_code()


if __name__ == "__main__":
    sys.exit(main())
