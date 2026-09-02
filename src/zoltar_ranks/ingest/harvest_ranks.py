"""Phase 1: build a point-in-time rank archive from upstream git history.

Run modes
---------
backfill : coverage walk over upstream history -- fetch HEAD, then binary search
           for the oldest commit whose blob still reaches back to what HEAD
           covers, and repeat. 2-4 fetches per file instead of one per day.
           Do this ONCE. It is the only way to recover intraday runs that the
           rolling 200-timestamp `all_*` buffer has already dropped.
daily    : fetch HEAD only, which normally carries everything new. Falls back to
           a coverage walk automatically if a gap against the archive is
           detected. Cheap. Run after every upstream push (or every 30 min).

Both modes are idempotent: the manifest records every (file, commit) pair
already processed, and inserts are keyed on (run_ts, symbol, risk_bucket).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.sources.git_archive import Snapshot, UpstreamMirror

log = logging.getLogger("harvest_ranks")

RANK_KEYS = ["run_ts", "symbol", "risk_bucket"]

UPSTREAM_COLS = {
    "Date": "run_ts",
    "Symbol": "symbol",
    "Score": "score",
    "Low_Risk_Score": "score",
    "High_Risk_Score": "score",
    "Score_Sharpe": "score_sharpe",
    "Low_Risk_Score_Sharpe": "score_sharpe",
    "High_Risk_Score_Sharpe": "score_sharpe",
    "Score_HoldPeriod": "score_holdperiod",
    "Low_Risk_Score_HoldPeriod": "score_holdperiod",
    "High_Risk_Score_HoldPeriod": "score_holdperiod",
    "Close_Price": "close_price",
    "Cap_Size": "cap_size",
    "Sector": "sector",
    "Industry": "industry",
    "source": "src_split",
}


def classify_run(ts: pd.Timestamp) -> str:
    """Label a run timestamp by when in the day it was produced (CDT wall clock).

    Upstream stamps the nightly placeholder build with the NEXT calendar day at
    00:00:00, so that is its own class and must never be treated as a run you
    could have traded on at midnight.
    """
    if ts.hour == 0 and ts.minute == 0 and ts.second == 0:
        return "placeholder"
    h = ts.hour + ts.minute / 60.0
    if h < 9.0:
        return "morning"
    if h < 15.5:
        return "intraday"
    return "nightly"


def normalize(df: pd.DataFrame, risk_bucket: str, feed: str, sha: str) -> pd.DataFrame:
    missing = {"Date", "Symbol"} - set(df.columns)
    if missing:
        raise ValueError(f"snapshot missing required columns: {missing}")
    out = df.rename(columns={k: v for k, v in UPSTREAM_COLS.items() if k in df.columns}).copy()
    keep = ["run_ts", "symbol", "score", "score_sharpe", "score_holdperiod",
            "close_price", "cap_size", "sector", "industry", "src_split"]
    for c in keep:
        if c not in out.columns:
            out[c] = None
    out = out[keep]
    out["run_ts"] = pd.to_datetime(out["run_ts"])
    out["risk_bucket"] = risk_bucket
    out["feed"] = feed
    out["run_kind"] = out["run_ts"].map(classify_run)
    out["first_seen_sha"] = sha
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    return out.drop_duplicates(subset=RANK_KEYS)


def coverage_walk(mirror: UpstreamMirror, file_path: str, snaps: list[Snapshot],
                  risk_bucket: str, feed: str, max_fetches: int = 20,
                  path_of=None):
    """Yield (snapshot, normalized_df) covering all recoverable history, cheaply.

    Each blob is 20-24 MB and already carries up to 200 run timestamps, so
    fetching one commit per day would move ~6 GB to learn almost nothing new.

    The blob at an older commit reaches FURTHER BACK in run_ts than the blob at a
    newer commit, because `all_*` is a rolling buffer that drops its oldest
    timestamps. So: fetch HEAD, note the oldest run_ts it covers, then binary
    search the commit list for the OLDEST commit whose blob still reaches up to
    that floor (so coverage stays contiguous), fetch it, and repeat. That is
    O(log n) fetches -- typically 2-4 per file instead of 68.

    `snaps` must be newest-first. Blobs are cached locally by git after the first
    read, so probe re-reads cost no network.

    `path_of` maps a Snapshot to the path to read, for sources where the path
    varies per snapshot instead of the commit. `daily_ranks/` is exactly that
    shape: one file per build rather than one file rewritten across commits.
    Defaults to the fixed `file_path`, so existing callers are unaffected.
    """
    resolve = path_of or (lambda _snap: file_path)
    if not snaps:
        return
    n = len(snaps)
    span_cache: dict[int, tuple] = {}

    def load(idx: int):
        snap = snaps[idx]
        return normalize(mirror.read_pickle(snap.sha, resolve(snap)), risk_bucket, feed, snap.sha)

    def span(idx: int) -> tuple:
        """(min_run_ts, max_run_ts) of the blob at snaps[idx]; None if unreadable."""
        if idx not in span_cache:
            try:
                df = load(idx)
                span_cache[idx] = (df["run_ts"].min(), df["run_ts"].max())
            except Exception as exc:
                log.warning("unreadable %s@%s: %s", resolve(snaps[idx]),
                            snaps[idx].sha[:8], str(exc)[:140])
                span_cache[idx] = None
        return span_cache[idx]

    head_span = span(0)
    if head_span is None:
        return
    yield snaps[0], load(0)
    covered_from = head_span[0]
    last_idx, fetches = 0, 1
    log.info("  HEAD covers %s .. %s", head_span[0], head_span[1])

    while last_idx < n - 1 and fetches < max_fetches:
        # Oldest index k > last_idx whose blob max_run_ts still reaches covered_from.
        oldest_span = span(n - 1)
        if oldest_span and oldest_span[1] >= covered_from:
            k = n - 1
        else:
            lo, hi = last_idx, n - 1     # span(lo) reaches; span(hi) does not
            while hi - lo > 1:
                mid = (lo + hi) // 2
                s = span(mid)
                if s and s[1] >= covered_from:
                    lo = mid
                else:
                    hi = mid
            k = lo
            if k == last_idx:
                # Unbridgeable hole: the buffer rolled faster than commits were
                # made. Jump past it anyway to salvage the disjoint older block.
                k = hi
                log.warning("  history gap before %s -- salvaging disjoint block at %s",
                            covered_from, snaps[k].sha[:8])
        s = span(k)
        if s is None:
            break
        yield snaps[k], load(k)
        fetches += 1
        log.info("  extended coverage back to %s (fetch %d)", s[0], fetches)
        if s[0] >= covered_from and k == last_idx:
            break
        covered_from = min(covered_from, s[0])
        last_idx = k


def already_done(con, file_path: str) -> set[str]:
    rows = con.execute(
        "SELECT commit_sha FROM harvest_manifest WHERE file_path = ?", [file_path]
    ).fetchall()
    return {r[0] for r in rows}


def _record(con, file_path: str, snap: Snapshot, df: pd.DataFrame) -> tuple[int, int]:
    seen, ins = duckdb_io.upsert_new_rows(con, "ranks", df, RANK_KEYS)
    con.execute(
        "INSERT OR REPLACE INTO harvest_manifest "
        "(file_path, commit_sha, committed_at, rows_seen, rows_inserted) VALUES (?,?,?,?,?)",
        [file_path, snap.sha, snap.committed_at.replace(tzinfo=None), seen, ins],
    )
    log.info("  %s @ %s %s  seen=%d new=%d", file_path.split('/')[-1], snap.sha[:8],
             snap.committed_at.date(), seen, ins)
    return seen, ins


def harvest_file(con, mirror: UpstreamMirror, file_path: str, risk_bucket: str,
                 feed: str, snaps: list[Snapshot], strategy: str = "explicit") -> tuple[int, int]:
    """`snaps` newest-first. strategy='coverage' walks history cheaply; anything
    else fetches exactly the snapshots given."""
    done = already_done(con, file_path)
    seen_total = ins_total = 0
    if strategy == "coverage":
        for snap, df in coverage_walk(mirror, file_path, [s for s in snaps if s.sha not in done],
                                      risk_bucket, feed):
            s, i = _record(con, file_path, snap, df)
            seen_total += s
            ins_total += i
        return seen_total, ins_total

    todo = [s for s in sorted(snaps, key=lambda x: x.committed_at) if s.sha not in done]
    for snap in todo:
        try:
            raw = mirror.read_pickle(snap.sha, file_path)
        except Exception as exc:  # a commit may predate the file
            log.warning("skip %s@%s: %s", file_path, snap.sha[:8], str(exc)[:160])
            continue
        s, i = _record(con, file_path, snap, normalize(raw, risk_bucket, feed, snap.sha))
        seen_total += s
        ins_total += i
    return seen_total, ins_total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harvest point-in-time ranks from upstream git history")
    ap.add_argument("--mode", choices=["backfill", "daily"], default="daily")
    ap.add_argument("--since", default=None,
                    help="git --since expression, e.g. 2026-08-01. Backfill only.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--export-parquet", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = Config.load(args.config)

    mirror = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    log.info("syncing upstream mirror at %s", cfg.mirror_dir)
    mirror.ensure()

    con = duckdb_io.connect(cfg.duckdb_path)
    grand_seen = grand_ins = 0
    for file_path, (risk_bucket, feed) in cfg.rank_files.items():
        snaps = mirror.commits_touching(file_path, since=args.since)
        if not snaps:
            log.warning("no commits touch %s", file_path)
            continue
        if args.mode == "backfill":
            log.info("%s: %d commits in history -> coverage walk", file_path, len(snaps))
            seen, ins = harvest_file(con, mirror, file_path, risk_bucket, feed,
                                     snaps, strategy="coverage")
        else:
            # Daily mode: HEAD alone normally suffices. The daily feed is
            # append-only (HEAD holds everything) and the `all` feed's HEAD blob
            # still carries the last ~200 run timestamps (~10 trading days). Only
            # if HEAD does not reach back to what we already have do we walk.
            head = snaps[0]
            df = normalize(mirror.read_pickle(head.sha, file_path), risk_bucket, feed, head.sha)
            have_max = con.execute(
                "SELECT max(run_ts) FROM ranks WHERE feed = ? AND risk_bucket = ?",
                [feed, risk_bucket]).fetchone()[0]
            seen, ins = _record(con, file_path, head, df)
            if have_max is not None and df["run_ts"].min() > pd.Timestamp(have_max):
                log.warning("%s: gap detected (HEAD reaches back only to %s, archive ends %s)"
                            " -> coverage walk", file_path, df["run_ts"].min(), have_max)
                s2, i2 = harvest_file(con, mirror, file_path, risk_bucket, feed,
                                      snaps[1:], strategy="coverage")
                seen += s2
                ins += i2
        grand_seen += seen
        grand_ins += ins

    summary = con.execute("""
        SELECT risk_bucket, run_kind, count(DISTINCT run_ts) AS runs,
               min(run_ts) AS first_run, max(run_ts) AS last_run, count(*) AS rows
        FROM ranks GROUP BY 1,2 ORDER BY 1,2
    """).df()
    log.info("archive now holds:\n%s", summary.to_string(index=False))
    log.info("rows staged=%d inserted=%d", grand_seen, grand_ins)

    if args.export_parquet:
        out = duckdb_io.export_parquet(con, "ranks", cfg.archive_dir,
                                       partition_expr="strftime(run_ts, '%Y-%m')")
        log.info("exported parquet to %s", out)
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
