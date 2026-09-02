"""The heartbeat contract: `scripts/daily.py` must leave an honest record.

The build monitor's §08 is derived entirely from `data/results/run_history.jsonl`.
Three ways that record can lie, each tested here:

* **A run that only logs successes** is indistinguishable from a job that is not
  running. The append happens in a `finally` block.
* **A run stamped `daily`** makes an intraday re-score and the evening retrain
  capture indistinguishable. The mode comes from the wall clock.
* **A naive timestamp** is ambiguous across the November DST boundary, and the
  file is read by a monitor that may not run on this machine.

Plus the one that would fabricate an alarm rather than hide one: a row count of
0 must mean "zero rows", never "I could not tell".
"""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]
CDT = ZoneInfo("America/Chicago")


def _load_daily():
    """`scripts/` is not a package, so load the module by path."""
    spec = importlib.util.spec_from_file_location("daily_mod", REPO / "scripts" / "daily.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


daily = _load_daily()


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=CDT)


# ------------------------------- derive_mode -------------------------------

@pytest.mark.parametrize("when,expected", [
    ("2026-09-02 07:00", "premarket"),   # scheduler start
    ("2026-09-02 08:59", "premarket"),
    ("2026-09-02 09:00", "intraday"),    # boundary, FINDINGS F4
    ("2026-09-02 15:29", "intraday"),
    ("2026-09-02 15:30", "evening"),     # boundary: AFTERCLOSE observed from 15:27
    ("2026-09-02 21:30", "evening"),     # scheduler end
    ("2026-09-05 10:00", "weekend"),     # Saturday
    ("2026-09-06 10:00", "weekend"),     # Sunday
])
def test_derive_mode_boundaries_pure(when, expected):
    assert daily.derive_mode(_at(when)) == expected


def test_derive_mode_boundaries_match_the_manifest_pure():
    """The manifest's job windows and derive_mode must not drift apart.

    `emit_build_status.py --check` cross-checks declared modes against the
    history, but it cannot catch both sides agreeing on a mode string that means
    different hours. This can.
    """
    import yaml
    manifest = yaml.safe_load((REPO / "data" / "build" / "manifest.yaml").read_text(encoding="utf-8"))
    jobs = {j["id"]: j for j in manifest["jobs"]}
    expected = {"harvest_premarket": (7, 9), "harvest_intraday": (9, 15.5),
                "harvest_evening": (15.5, 21.5)}
    for job_id, (start, end) in expected.items():
        assert jobs[job_id]["start_hour"] == start, f"{job_id} start_hour drifted"
        assert jobs[job_id]["end_hour"] == end, f"{job_id} end_hour drifted"
    # and the mode each window declares is the one derive_mode returns inside it
    for job_id, mode in [("harvest_premarket", "premarket"),
                         ("harvest_intraday", "intraday"),
                         ("harvest_evening", "evening")]:
        j = jobs[job_id]
        assert j["modes"] == [mode]
        mid = (j["start_hour"] + j["end_hour"]) / 2
        probe = _at("2026-09-02 00:00") .replace(hour=int(mid), minute=int(60 * (mid % 1)))
        assert daily.derive_mode(probe) == mode


def test_all_derived_modes_are_declared_pure():
    for when in ["2026-09-02 07:00", "2026-09-02 10:00", "2026-09-02 20:00",
                 "2026-09-05 10:00"]:
        assert daily.derive_mode(_at(when)) in daily.MODES


# ------------------------------ the heartbeat ------------------------------

@pytest.fixture()
def run_daily(tmp_path, monkeypatch):
    """Run `daily.main()` with stubbed steps against a throwaway results dir."""
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"duckdb_path: {(tmp_path / 'x.duckdb').as_posix()}\n"
        f"results_dir: {(tmp_path / 'results').as_posix()}\n", encoding="utf-8")

    emitted: list[int] = []
    monkeypatch.setattr(daily, "_emit_monitor", lambda: emitted.append(1))

    def run(steps, argv=None):
        monkeypatch.setattr(daily, "STEPS", steps)
        rc = daily.main((argv or []) + ["--config", str(cfg_path)])
        hist = tmp_path / "results" / "run_history.jsonl"
        lines = [json.loads(x) for x in hist.read_text(encoding="utf-8").splitlines() if x.strip()]
        return rc, lines, emitted
    return run


def test_history_is_appended_on_success(run_daily):
    rc, lines, _ = run_daily([("ranks", lambda argv: 0)])
    assert rc == 0
    assert len(lines) == 1 and lines[0]["failed"] == []


def test_history_is_appended_on_failure(run_daily):
    """The one that matters: a job logging only successes looks like a dead job."""
    rc, lines, _ = run_daily([("ranks", lambda argv: 1)])
    assert rc == 1
    assert len(lines) == 1
    assert lines[0]["failed"] == ["ranks"], "a failed run must still leave a beat"


def test_history_is_appended_even_when_a_step_raises(run_daily):
    def boom(argv):
        raise RuntimeError("simulated crash")
    rc, lines, _ = run_daily([("ranks", boom)])
    assert rc == 1 and len(lines) == 1 and lines[0]["failed"] == ["ranks"]


def test_history_appends_never_rewrites(run_daily):
    run_daily([("ranks", lambda argv: 0)])
    rc, lines, _ = run_daily([("ranks", lambda argv: 0)])
    assert len(lines) == 2, "run_history.jsonl is append-only, like the archive"


def test_every_timestamp_carries_an_offset(run_daily):
    _, lines, _ = run_daily([("ranks", lambda argv: 0)])
    stamped = datetime.fromisoformat(lines[0]["finished_at"])
    assert stamped.tzinfo is not None and stamped.utcoffset() is not None, (
        "a naive timestamp is ambiguous across the November DST boundary and "
        "this file is read by a monitor that may not run on this machine")


def test_mode_is_a_real_window_not_daily(run_daily):
    _, lines, _ = run_daily([("ranks", lambda argv: 0)])
    assert lines[0]["mode"] in daily.MODES
    assert lines[0]["mode"] != "daily", (
        "stamping every tick 'daily' makes an intraday re-score and the evening "
        "retrain capture indistinguishable in the record")


def test_mode_override_records_but_does_not_change_harvester_argv(run_daily):
    seen: list[list[str]] = []

    def spy(argv):
        seen.append(list(argv))
        return 0
    _, lines, _ = run_daily([("ranks", spy)], argv=["--mode", "evening"])
    assert lines[0]["mode"] == "evening"
    assert "--mode" in seen[0] and seen[0][seen[0].index("--mode") + 1] == "daily", (
        "harvesters only understand backfill/daily; the recorded window mode "
        "must not leak into their argv")


def test_backfill_records_backfill_and_passes_it_through(run_daily):
    seen: list[list[str]] = []

    def spy(argv):
        seen.append(list(argv))
        return 0
    _, lines, _ = run_daily([("ranks", spy)], argv=["--backfill"])
    assert lines[0]["mode"] == "backfill"
    assert seen[0][seen[0].index("--mode") + 1] == "backfill"


def test_emitter_runs_after_the_status_write(run_daily):
    _, _, emitted = run_daily([("ranks", lambda argv: 0)])
    assert emitted, ("the build monitor emitter must run from daily.py, or the "
                     "console only updates when someone runs it by hand")


def test_emitter_failure_cannot_fail_the_run(tmp_path, monkeypatch):
    """A monitor that can fail a harvest is worse than a stale monitor."""
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"duckdb_path: {(tmp_path / 'x.duckdb').as_posix()}\n"
        f"results_dir: {(tmp_path / 'results').as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(daily, "STEPS", [("ranks", lambda argv: 0)])
    monkeypatch.setattr(daily, "REPO_ROOT", tmp_path)   # emitter path will not exist
    assert daily.main(["--config", str(cfg_path)]) == 0


# ------------------------------- row counts -------------------------------

def test_unknown_row_count_is_null_never_zero(run_daily):
    """`rows: 0` is a real, alarming signal on the intraday job.

    It must never be produced by a missing return value or an unreadable
    database -- a fabricated alarm is as bad as a hidden one.
    """
    _, lines, _ = run_daily([("prices", lambda argv: 0)])   # no _STEP_TABLES entry
    assert lines[0]["steps"]["prices"]["rows"] is None
    assert lines[0]["rows"] is None


def test_count_rows_returns_none_for_a_missing_database(tmp_path):
    assert daily._count_rows(tmp_path / "nope.duckdb", ("ranks",)) is None


def test_count_rows_returns_none_for_an_unknown_table(tmp_path):
    from zoltar_ranks.db import duckdb_io
    db = tmp_path / "x.duckdb"
    duckdb_io.connect(db).close()
    assert daily._count_rows(db, ("no_such_table",)) is None


def test_count_rows_counts_a_real_table(tmp_path):
    from zoltar_ranks.db import duckdb_io
    db = tmp_path / "x.duckdb"
    duckdb_io.connect(db).close()
    assert daily._count_rows(db, ("ranks",)) == 0
