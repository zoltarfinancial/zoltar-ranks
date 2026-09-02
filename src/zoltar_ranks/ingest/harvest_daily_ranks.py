"""Phase 1b: recover intraday history from `daily_ranks/` per-build PROD files.

Why this exists
---------------
`production/*_latest.pkl` is rewritten in place, so git gives us only as many
snapshots of it as there are commits touching it -- and the coverage walk needs
just 2-4 of those. `daily_ranks/` instead keeps **one file per build**, so
upstream has preserved ~114 independent snapshots of the same rolling `all_*`
buffer. Each holds ~200 run timestamps taken at a different moment, and their
union reaches materially further back than anything `production/` still carries.

Three things that are NOT true of these files, each learned the hard way:

* **They are not per-run files.** Despite the run stamp in the filename, each is
  a full history panel (153-200 distinct run timestamps, 160k-238k rows) with
  exactly the same 10 columns as `production/*_latest.pkl`. `normalize()`
  applies unchanged.
* **`run_ts` comes from the `Date` column, never the filename.** The filename
  stamp is frequently absent from the file's own data -- for the nightly retrain
  it is routinely ~24h off (FINDINGS F4). Using it as `run_ts` would invent run
  timestamps that join to nothing.
* **The filename stamp is still valuable** -- as `available_at`. It is the build
  time, which is tighter than `committed_at` (that carries push lag). Neither is
  safe alone, because nightly uses two stamping conventions, so `ranks_pit` takes
  `min(build_stamp, committed_at, run_ts)`.

RULE 4: only `*_PROD_*` files are read. The `*_rankings_*` files -- including
every `-{PREMARKET,MORNING,...,AFTERCLOSE} UPDATE` variant -- contain in-sample
`source='train'` rows and are never opened here. They appear in this repo only as
filenames, in the F4 session census.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.ingest.incomplete import Incomplete
from zoltar_ranks.ingest.harvest_ranks import RANK_KEYS, coverage_walk, normalize
from zoltar_ranks.sources.git_archive import Snapshot, UpstreamMirror, _run

log = logging.getLogger("harvest_daily_ranks")

#: Only the rolling-buffer variants. The non-`all` per-build files were measured
#: to reach no further back than the archive already holds (2025-10-01), so they
#: would cost ~2.4 GB of blob fetches for zero new run timestamps.
FEED = "daily_ranks"
PROD_RE = re.compile(r"^daily_ranks/(all_)(low|high)_risk_PROD_(\d{8})_(\d{6})\.pkl$")


def added_prod_files(mirror: UpstreamMirror) -> list[tuple[str, Snapshot, datetime]]:
    """Every `daily_ranks/all_*_risk_PROD_*.pkl` ever added, with its add-commit.

    `--diff-filter=A` so files later deleted from HEAD are still found; upstream
    prunes this directory, and the pruned ones are exactly the history we want.
    """
    out = _run(["git", "log", "--diff-filter=A", "--name-only", "--no-renames",
                "--format=C\t%H\t%cI", "--", "daily_ranks/"], cwd=mirror.dir)
    rows: list[tuple[str, Snapshot, datetime]] = []
    sha = committed = None
    for line in out.splitlines():
        if line.startswith("C\t"):
            _, sha, iso = line.split("\t")
            committed = datetime.fromisoformat(iso).replace(tzinfo=None)
            continue
        path = line.strip()
        if not path or sha is None:
            continue
        m = PROD_RE.match(path)
        if not m:
            continue
        _, bucket, d, t = m.groups()
        build = datetime.strptime(d + t, "%Y%m%d%H%M%S")
        rows.append((bucket, Snapshot(sha=sha, committed_at=committed, path=path), build))
    return rows



def _read_all(mirror: UpstreamMirror, snaps, bucket: str, unreadable: Incomplete):
    """Read every build. The default, because coverage_walk is UNSOUND here.

    MEASURED 2026-09-01. coverage_walk assumes an older commit's blob reaches
    further back than a newer one -- true for `production/*_latest.pkl`, false
    for these. The `all_*` buffer holds ~200 slots, and a slot is a *run*, not a
    day: in the sparse era 200 runs span ~200 days, in the dense era ~16. So
    reach is non-monotonic in build time. Observed:

        build 2026-07-22 -> reaches 2026-03-12
        build 2026-08-07 -> reaches 2026-07-03
        build 2026-08-31 -> reaches 2026-06-10

    Binary search over that ordering is meaningless. Run on this data the walk
    fetched 2 of 114 builds and inserted 0 rows; 5 randomly chosen skipped builds
    then turned out to hold 205 run timestamps it had missed, reaching 2 months
    further back. Hence: read them all.
    """
    for snap in snaps:
        try:
            yield snap, normalize(mirror.read_pickle(snap.sha, snap.path),
                                  bucket, FEED, snap.sha)
        except Exception as exc:                       # noqa: BLE001
            # One bad blob must not abandon the other 227, but it must not be
            # mistaken for success either: the caller counts these and exits
            # non-zero, because a silently short backfill is a silently short
            # archive, and nothing downstream can tell the difference.
            unreadable.record(f"{snap.path}@{snap.sha[:8]}", exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=["backfill", "daily"], default="daily")
    ap.add_argument("--config", default=None)
    ap.add_argument("--strategy", choices=["full", "coverage"], default="full",
                    help="'full' reads every build (default -- see MEASURED note "
                         "below); 'coverage' binary-searches and is UNSOUND here")
    ap.add_argument("--max-fetches", type=int, default=20,
                    help="coverage strategy only")
    ap.add_argument("--no-sync", action="store_true",
                    help="skip the mirror fetch and work from blobs already local. "
                         "The operator is ASSERTING the mirror is current -- this "
                         "will silently miss new upstream builds, so it is only for "
                         "re-running a backfill whose blobs are already cached.")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)
    mirror = UpstreamMirror(cfg.upstream_url, cfg.mirror_dir, cfg.upstream_branch)
    if args.no_sync:
        log.warning("--no-sync: mirror NOT refreshed; new upstream builds will be missed")
    else:
        mirror.ensure()

    files = added_prod_files(mirror)
    if not files:
        # Not a quiet no-op. Upstream has had 228 of these since 2026-07-18, so
        # zero means the directory moved, the naming changed, or PROD_RE stopped
        # matching -- each of which silently freezes this feed forever.
        log.error("no daily_ranks/all_*_PROD_* files found upstream: the source "
                  "moved, was renamed, or PROD_RE no longer matches. Not treating "
                  "an empty source as success.")
        return 1
    log.info("daily_ranks: %d PROD files, %d distinct build stamps",
             len(files), len({b for _, _, b in files}))

    con = duckdb_io.connect(cfg.duckdb_path)
    grand_seen = grand_ins = 0
    unreadable = Incomplete('harvest_daily_ranks', log)

    for bucket in ("low", "high"):
        sel = [(s, b) for bk, s, b in files if bk == bucket]
        if not sel:
            continue
        # Newest build first: coverage_walk assumes newest-first and reaches back.
        sel.sort(key=lambda sb: sb[1], reverse=True)
        snaps = [s for s, _ in sel]
        build_of = {s.sha + "|" + s.path: b for s, b in sel}
        path_of = {s.sha: s.path for s in snaps}

        log.info("all_%s_risk_PROD: %d builds -> %s", bucket, len(snaps), args.strategy)
        seen_b = ins_b = 0
        if args.strategy == "coverage":
            source = coverage_walk(mirror, snaps[0].path, snaps, bucket, FEED,
                                   max_fetches=args.max_fetches,
                                   path_of=lambda s: s.path)
        else:
            source = _read_all(mirror, snaps, bucket, unreadable)
        for snap, df in source:
            build = build_of[snap.sha + "|" + snap.path]
            seen, ins = duckdb_io.upsert_new_rows(con, "ranks", df, RANK_KEYS)
            con.execute(
                "INSERT OR REPLACE INTO harvest_manifest "
                "(file_path, commit_sha, committed_at, rows_seen, rows_inserted, build_stamp) "
                "VALUES (?,?,?,?,?,?)",
                [snap.path, snap.sha, snap.committed_at, seen, ins, build])
            newest = df["run_ts"].max()
            if build < newest and (newest - build).total_seconds() > 60:
                # Expected for the nightly retrain (~+24h). Anywhere else it would
                # mean the file is named at build START, which makes build_stamp
                # optimistic as an availability bound -- report, never silently use.
                log.info("  %s: newest Date %s is %.2fh AFTER build stamp %s",
                         snap.path.split("/")[-1], newest,
                         (newest - build).total_seconds() / 3600, build)
            log.info("  %s  build=%s seen=%d new=%d",
                     snap.path.split("/")[-1], build, seen, ins)
            seen_b += seen
            ins_b += ins
        log.info("all_%s_risk_PROD: staged=%d inserted=%d", bucket, seen_b, ins_b)
        grand_seen += seen_b
        grand_ins += ins_b

    log.info("daily_ranks rows staged=%d inserted=%d", grand_seen, grand_ins)
    con.close()
    return unreadable.exit_code(intended=len(files))


if __name__ == "__main__":
    sys.exit(main())
