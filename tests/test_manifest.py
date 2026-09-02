"""WORK-idempotency: a second consecutive run must read ZERO blobs.

This is a different invariant from row-idempotency, and the distinction is the
whole point. `tests/test_harvest.py` proves re-running inserts no new ROWS.
`harvest_daily_ranks` passed that test while re-reading all 228 builds
(~4.8 GB, ~866k rows staged, 290.6 s of a 309.5 s tick) every 30 minutes,
because it parsed `--mode` and never read it. Rows were idempotent; work was not.

So these tests assert on the **blob-read count**, instrumented, never inferred
from timing.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from zoltar_ranks.db import duckdb_io
from zoltar_ranks.ingest import manifest
from zoltar_ranks.sources.git_archive import Snapshot, UpstreamMirror

SHA_A = "a" * 40
SHA_B = "b" * 40


# ----------------------------- the helper itself -----------------------------

@pytest.fixture()
def con(tmp_path):
    c = duckdb_io.connect(tmp_path / "t.duckdb")
    yield c
    c.close()


def _record(con, file_path, sha):
    con.execute("INSERT OR REPLACE INTO harvest_manifest "
                "(file_path, commit_sha, committed_at, rows_seen, rows_inserted) "
                "VALUES (?,?,?,?,?)",
                [file_path, sha, dt.datetime(2026, 9, 1, 12, 0), 1, 1])


def test_already_read_returns_recorded_pairs(con):
    _record(con, "daily_ranks/x.pkl", SHA_A)
    assert manifest.already_read(con, ["daily_ranks/x.pkl"]) == {("daily_ranks/x.pkl", SHA_A)}


def test_already_read_is_empty_for_no_paths(con):
    assert manifest.already_read(con, []) == set()


def test_unread_drops_only_the_recorded_ones(con):
    _record(con, "daily_ranks/x.pkl", SHA_A)
    cands = [("daily_ranks/x.pkl", SHA_A), ("daily_ranks/y.pkl", SHA_B)]
    todo = manifest.unread(con, cands, key=lambda c: c)
    assert todo == [("daily_ranks/y.pkl", SHA_B)]


def test_unread_matches_on_the_PAIR_not_the_path(con):
    """`production/*_latest.pkl` is rewritten in place: same path, new sha, new work."""
    _record(con, "production/low_risk_PROD_latest.pkl", SHA_A)
    cands = [("production/low_risk_PROD_latest.pkl", SHA_B)]
    assert manifest.unread(con, cands, key=lambda c: c) == cands, (
        "a rewritten file at a NEW commit is unread work and must not be skipped")


def test_force_ignores_the_manifest(con):
    _record(con, "daily_ranks/x.pkl", SHA_A)
    cands = [("daily_ranks/x.pkl", SHA_A)]
    assert manifest.unread(con, cands, key=lambda c: c, force=True) == cands


# ------------------ the harvester, run twice, blobs counted ------------------

def _panel(build: dt.datetime) -> pd.DataFrame:
    """Minimal frame with the columns `normalize()` requires."""
    return pd.DataFrame({
        "Date": [build, build - dt.timedelta(hours=3)],
        "Symbol": ["AAA", "BBB"],
        "Score": [1.0, 2.0],
        "Close_Price": [10.0, 20.0],
    })


@pytest.fixture()
def stub_upstream(tmp_path, monkeypatch):
    """Three fake builds, a counting `read_pickle`, and a throwaway DB."""
    from zoltar_ranks.ingest import harvest_daily_ranks as hdr

    builds = [dt.datetime(2026, 8, 3, 20, 0),
              dt.datetime(2026, 8, 4, 20, 0),
              dt.datetime(2026, 8, 5, 20, 0)]
    files = []
    for i, b in enumerate(builds):
        for bucket in ("low", "high"):
            path = f"daily_ranks/all_{bucket}_risk_PROD_{b:%Y%m%d_%H%M%S}.pkl"
            files.append((bucket,
                          Snapshot(sha=f"{i}{bucket}".ljust(40, "0"),
                                   committed_at=b + dt.timedelta(hours=12),
                                   path=path),
                          b))

    monkeypatch.setattr(UpstreamMirror, "ensure", lambda self: None)
    monkeypatch.setattr(hdr, "added_prod_files", lambda mirror: list(files))

    reads: list[str] = []

    def counting_read(self, sha, path):
        reads.append(f"{path}@{sha[:8]}")
        build = dt.datetime.strptime(path.split("_PROD_")[1][:15], "%Y%m%d_%H%M%S")
        return _panel(build)
    monkeypatch.setattr(UpstreamMirror, "read_pickle", counting_read)

    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(f"duckdb_path: {(tmp_path / 'x.duckdb').as_posix()}\n")
    return hdr, cfg_path, reads, len(files)


def test_second_run_reads_zero_blobs(stub_upstream):
    """THE regression test. Row-idempotency passed here while work-idempotency failed."""
    hdr, cfg_path, reads, n_files = stub_upstream

    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 0
    first = len(reads)
    assert first == n_files, f"first run should read all {n_files} builds, read {first}"

    reads.clear()
    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 0
    assert reads == [], (
        f"second consecutive run performed {len(reads)} blob read(s): {reads[:5]}. "
        f"harvest_manifest already records every one of them as read. Consult it "
        f"BEFORE reading, not only when inserting -- see ingest/manifest.py.")


def test_backfill_mode_is_also_work_idempotent(stub_upstream):
    """Both modes skip. The modes differ in how much is outstanding, not in path."""
    hdr, cfg_path, reads, n_files = stub_upstream
    hdr.main(["--mode", "backfill", "--config", str(cfg_path)])
    reads.clear()
    hdr.main(["--mode", "backfill", "--config", str(cfg_path)])
    assert reads == []


def test_force_re_reads_everything(stub_upstream):
    hdr, cfg_path, reads, n_files = stub_upstream
    hdr.main(["--mode", "backfill", "--config", str(cfg_path)])
    reads.clear()
    hdr.main(["--mode", "backfill", "--force", "--config", str(cfg_path)])
    assert len(reads) == n_files, "--force must ignore the manifest entirely"


def test_a_new_build_is_still_picked_up(stub_upstream, monkeypatch):
    """The skip must not freeze the feed: new work still gets read."""
    hdr, cfg_path, reads, n_files = stub_upstream
    hdr.main(["--mode", "daily", "--config", str(cfg_path)])

    new_build = dt.datetime(2026, 8, 6, 20, 0)
    extra = [(bucket,
              Snapshot(sha=f"9{bucket}".ljust(40, "0"),
                       committed_at=new_build + dt.timedelta(hours=12),
                       path=f"daily_ranks/all_{bucket}_risk_PROD_"
                            f"{new_build:%Y%m%d_%H%M%S}.pkl"),
              new_build) for bucket in ("low", "high")]
    prev = hdr.added_prod_files
    monkeypatch.setattr(hdr, "added_prod_files", lambda mirror: prev(mirror) + extra)

    reads.clear()
    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 0
    assert len(reads) == 2, f"the two new builds must be read; read {reads}"


def test_failed_read_is_retried_on_the_next_run(stub_upstream, monkeypatch):
    """The manifest records successes only, so an unreadable blob comes back.

    This is exactly how the 8 unreadable `all_high` files of 2026-07-21/22/23
    were recovered. A skip built on attempts rather than successes would have
    lost them permanently.
    """
    hdr, cfg_path, reads, n_files = stub_upstream
    real_read = UpstreamMirror.read_pickle

    def fail_high(self, sha, path):
        if "all_high" in path:
            raise RuntimeError("simulated unreadable blob")
        return real_read(self, sha, path)
    monkeypatch.setattr(UpstreamMirror, "read_pickle", fail_high)

    rc = hdr.main(["--mode", "daily", "--config", str(cfg_path)])
    assert rc != 0, "unread files must exit nonzero (ingest/incomplete.py)"

    monkeypatch.setattr(UpstreamMirror, "read_pickle", real_read)
    reads.clear()
    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 0
    assert len(reads) == n_files // 2, (
        f"the failed all_high builds must be retried, not skipped; read {reads}")


def test_zero_new_files_does_not_trip_the_empty_source_guard(stub_upstream):
    """Trap 1: the guard belongs on files DISCOVERED, not files TO READ.

    After the manifest skip, "zero to read" is the normal outcome on 28 of 29
    ticks. A guard on the post-skip list would fire constantly and stop guarding.
    """
    hdr, cfg_path, reads, _ = stub_upstream
    hdr.main(["--mode", "daily", "--config", str(cfg_path)])
    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 0, (
        "a run with nothing new to read is a normal successful run")


def test_empty_upstream_still_fails_loudly(stub_upstream, monkeypatch):
    """...but a genuinely empty source must still be caught."""
    hdr, cfg_path, _, _ = stub_upstream
    monkeypatch.setattr(hdr, "added_prod_files", lambda mirror: [])
    assert hdr.main(["--mode", "daily", "--config", str(cfg_path)]) == 1


# ------------------------- every harvester consults it -------------------------

@pytest.mark.parametrize("modname", ["harvest_daily_ranks", "harvest_er", "harvest_shap"])
def test_harvester_consults_the_manifest_before_reading_pure(modname):
    """Structural guard so the next harvester inherits this instead of re-deriving it."""
    import importlib
    import inspect
    src = inspect.getsource(importlib.import_module(f"zoltar_ranks.ingest.{modname}"))
    assert ("manifest.unread" in src) or ("FROM harvest_manifest" in src), (
        f"{modname} never consults harvest_manifest before reading. The manifest "
        f"is the record of work done -- querying it only when inserting makes the "
        f"harvester row-idempotent but not work-idempotent, which is how "
        f"harvest_daily_ranks re-read 4.8 GB every 30 minutes.")
