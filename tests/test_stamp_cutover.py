"""Canary contract tests for the 2026-09-02 stamping-convention cutover.

Per rule 9 these FAIL, they do not warn. Each guards a way the archive could
quietly start meaning something different from what it meant yesterday:

* the forward stamp ends and `classify_run`'s `placeholder` branch goes dead,
* a THIRD convention appears (filename honest but `Date` still forward),
* `classify_run` drifts away from upstream's own session labels.

All three read the local archive. They skip -- loudly -- when it is absent,
because "no archive" is a different failure from "archive changed shape", and
conflating them is how a canary stops being a canary.
"""
from __future__ import annotations

import datetime as dt

import pytest

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io

CUTOVER = dt.datetime(2026, 9, 2)
#: consecutive days with an evening retrain but no 00:00:00 stamp before we call
#: the forward convention over.
DEAD_BRANCH_DAYS = 3


@pytest.fixture(scope="module")
def con():
    cfg = Config.load()
    if not cfg.duckdb_path.exists():
        pytest.skip(f"no archive at {cfg.duckdb_path}; run scripts/setup.ps1")
    c = duckdb_io.connect(cfg.duckdb_path, read_only=True)
    yield c
    c.close()


def test_cutover_date_matches_config():
    """The date is in three places; drift between them re-labels every row."""
    assert Config.load().stamp_cutover_date == CUTOVER.strftime("%Y-%m-%d")


def test_placeholder_branch_not_silently_dead(con):
    """Andrew's forward stamp ends 2026-09-02, killing classify_run's
    `placeholder` branch. This FAILS the first time that is true in the data, so
    it is a decision someone makes, not something inferred from an empty column
    months later.

    When it fires: confirm the cutover happened, then either retire the
    `placeholder` branch or narrow this test to the pre-cutover window. Do not
    delete it.
    """
    rows = con.execute("""
        SELECT CAST(available_at AS DATE) d,
               sum(CASE WHEN CAST(run_ts AS TIME) = TIME '00:00:00' THEN 1 ELSE 0 END) ph
        FROM evening_retrains
        WHERE available_at >= ?
        GROUP BY 1 ORDER BY 1
    """, [CUTOVER]).fetchall()
    if len(rows) < DEAD_BRANCH_DAYS:
        pytest.skip(f"only {len(rows)} post-cutover day(s) of evening retrains; "
                    f"need {DEAD_BRANCH_DAYS} before the branch can be called dead")
    streak = 0
    for _, ph in rows:
        streak = streak + 1 if ph == 0 else 0
    assert streak < DEAD_BRANCH_DAYS, (
        f"No 00:00:00 evening stamp for {streak} consecutive days after "
        f"{CUTOVER:%Y-%m-%d}. The forward convention has ended and "
        f"classify_run()'s `placeholder` branch is now dead code. Confirm, then "
        f"retire the branch or scope this test to the pre-cutover window.")


def test_no_third_stamping_convention(con):
    """Post-cutover, an evening retrain's `run_ts` must be its production time.

    A post-cutover run whose `run_ts` still runs ahead of `available_at` would
    mean a third convention -- filename honest, `Date` still forward -- which no
    other check would catch, and which would corrupt every forward return
    measured from that row.
    """
    bad = con.execute("""
        SELECT run_ts, available_at, availability_source
        FROM evening_retrains
        WHERE available_at >= ? AND stamp_is_forward
        ORDER BY run_ts LIMIT 10
    """, [CUTOVER]).fetchall()
    assert not bad, (
        "Post-cutover evening retrain is STILL forward-stamped -- a third "
        f"convention (filename honest, Date forward). Examples: {bad}")


def test_classify_run_agrees_with_upstream_session_labels(con):
    """`classify_run()` vs upstream's own labels -- the only external check.

    Measured 2026-09-01: 5 disagreements in 513 stamps, all within ~4 minutes of
    a boundary (AFTEROPEN at 08:57-08:59 -> `morning`; AFTERCLOSE at 15:27-15:31
    -> `intraday`). Those are known and accepted. This asserts no disagreement
    appears AWAY from a boundary, which would mean upstream's schedule moved or
    the labels changed meaning.
    """
    n = con.execute("SELECT count(*) FROM run_sessions").fetchone()[0]
    if not n:
        pytest.skip("run_sessions empty; run harvest_sessions")

    expected = {"PREMARKET": "morning", "MORNING": "intraday",
                "AFTEROPEN": "intraday", "AFTERNOON": "intraday",
                "PRECLOSE": "intraday", "AFTERCLOSE": "nightly"}
    rows = con.execute(
        "SELECT DISTINCT build_stamp, session_label FROM run_sessions").fetchall()

    from zoltar_ranks.ingest.harvest_ranks import classify_run
    import pandas as pd

    far = []
    for ts, label in rows:
        got = classify_run(pd.Timestamp(ts))
        if got == expected[label]:
            continue
        h = ts.hour + ts.minute / 60 + ts.second / 3600
        # classify_run's boundaries; a near-boundary miss is the known case.
        if min(abs(h - 9.0), abs(h - 15.5)) > 5 / 60:
            far.append((str(ts), label, got, round(h, 3)))
    assert not far, (
        "classify_run disagrees with an upstream session label AWAY from a "
        f"boundary (>5 min). Upstream's schedule or labels changed: {far[:10]}")


def test_evening_retrains_excludes_the_final_intraday_rescore(con):
    """AFTERCLOSE is bimodal; the 15:27-15:31 cluster is the day's last intraday
    re-score, not a retrain (5 of those 7 runs share a day with a real retrain).
    Including them would make H12b a blend of two processes.
    """
    leaked = con.execute("""
        SELECT DISTINCT run_ts FROM evening_retrains
        WHERE CAST(run_ts AS TIME) <> TIME '00:00:00' AND hour(run_ts) < 18
        ORDER BY 1 LIMIT 10
    """).fetchall()
    assert not leaked, (
        f"final-intraday-rescore runs leaked into evening_retrains: {leaked}")
