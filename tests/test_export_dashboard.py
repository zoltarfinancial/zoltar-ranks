"""The research feed (`docs/DASHBOARD.md`). Two things it must never do.

1. **Compute freshness from `run_ts`.** Upstream forward-stamps the evening
   retrain, so the newest `run_ts` is routinely in the future. "Hours since" then
   goes negative, the ~24h staleness alarm never fires, and a dead harvester
   renders as healthy.
2. **Turn a missing number into a real one.** `TBD (n~64 paired days)` must not
   become an MDE of 64. A fabricated value is worse than a null, because a null
   renders as a dash and a number renders as evidence.
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from zoltar_ranks.analysis import export_dashboard_data as ex
from zoltar_ranks.db import duckdb_io


# ---------------------------- register parsing ----------------------------

def test_mde_leading_number_is_parsed_pure():
    assert ex._num("0.157%/run (n=63, ~9.9%/yr)") == 0.157


@pytest.mark.parametrize("cell", ["TBD", "TBD (n~64 paired days)",
                                  "TBD - **compute before building**", "", None])
def test_non_numeric_mde_stays_null_never_fabricated_pure(cell):
    assert ex._num(cell) is None, (
        f"{cell!r} produced a number. A fabricated MDE renders as evidence; a "
        f"null renders as a dash.")


def test_ci_needs_two_numbers_pure():
    assert ex._ci("[0.09, 0.38]") == (0.09, 0.38)
    assert ex._ci("0.09") == (None, None)
    assert ex._ci(None) == (None, None)


def test_empty_cell_is_none_not_empty_string_pure():
    assert ex._clean("  **  ** ") is None


def test_register_parses_and_keeps_rejected_rows(tmp_path):
    """Rejected rows are the FDR denominator (rule 7). Dropping one inflates
    every corrected p-value on the page."""
    md = tmp_path / "HYPOTHESES.md"
    md.write_text(
        "| ID | Date | Statement | Lever | Test | MDE | Status | Result | 95% CI | FDR p |\n"
        "|----|------|-----------|-------|------|-----|--------|--------|--------|-------|\n"
        "| H1 | 2026-09-01 | a | exit rule | t | TBD | proposed | | | |\n"
        "| H2 | 2026-09-01 | b | entry | t | 0.05 | rejected | -0.01 | [-0.04, 0.02] | 0.9 |\n"
        "| H12a | 2026-09-01 | c | data | t | TBD (n~64) | proposed | | | |\n",
        encoding="utf-8")
    rows = ex.hypotheses(md)
    assert [r["id"] for r in rows] == ["H1", "H2", "H12a"]
    assert any(r["status"] == "rejected" for r in rows), "rejected rows must survive"
    assert rows[0]["mde"] is None and rows[0]["mde_raw"] == "TBD"
    assert rows[1]["mde"] == 0.05 and rows[1]["ci_low"] == -0.04
    assert rows[2]["mde"] is None, "'TBD (n~64)' must not parse as 64"


def test_register_ignores_other_tables(tmp_path):
    md = tmp_path / "H.md"
    md.write_text("| upstream label | n | min | max | a | b | c | d | e | f |\n"
                  "| PREMARKET | 10 | 08:14 | 08:50 | | | | | | |\n", encoding="utf-8")
    assert ex.hypotheses(md) == []


def test_missing_register_is_empty_not_an_error(tmp_path):
    assert ex.hypotheses(tmp_path / "nope.md") == []


# --------------------------- freshness / archive ---------------------------

@pytest.fixture()
def cfg(tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        f"duckdb_path: {(tmp_path / 'x.duckdb').as_posix()}\n"
        f"results_dir: {(tmp_path / 'results').as_posix()}\n", encoding="utf-8")
    from zoltar_ranks.config import Config
    return Config.load(str(cfg_path))


def _seed_forward_stamped(cfg, run_ts: dt.datetime, committed_at: dt.datetime):
    """One row whose run_ts is in the FUTURE relative to when it was knowable."""
    con = duckdb_io.connect(cfg.duckdb_path)
    con.execute("INSERT INTO harvest_manifest (file_path, commit_sha, committed_at) "
                "VALUES ('production/x.pkl', 'sha1', ?)", [committed_at])
    con.execute(
        "INSERT INTO ranks (run_ts, symbol, risk_bucket, score, feed, run_kind, "
        "first_seen_sha) VALUES (?, 'AAA', 'low', 1.0, 'daily', 'placeholder', 'sha1')",
        [run_ts])
    con.close()


def test_freshness_uses_available_at_not_run_ts(cfg):
    """THE regression test. The observed case: run_ts 2026-09-03 00:00:00 while
    the information was available 2026-09-02 08:41."""
    now = dt.datetime.now()
    _seed_forward_stamped(cfg,
                          run_ts=now + dt.timedelta(hours=12),   # forward-stamped
                          committed_at=now - dt.timedelta(hours=2))
    con = duckdb_io.connect(cfg.duckdb_path)
    try:
        health = ex.archive_health(con, cfg)
    finally:
        con.close()
    assert health["freshness_basis"] == "available_at"
    assert health["hours_since_fresh"] > 0, (
        "freshness went negative: computed from a forward-stamped run_ts, the "
        "staleness alarm can never fire and a dead harvester looks healthy")
    assert health["hours_since_fresh"] == pytest.approx(2, abs=0.2)
    # deprecated alias, carried for one version so the console does not break
    assert health["hours_since_last_run_ts"] == health["hours_since_fresh"]
    # both timestamps visible, so the gap is inspectable rather than implied
    assert health["last_run_ts"] > health["last_available_at"]


def test_failed_steps_are_surfaced(cfg):
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "last_run_status.json").write_text(
        json.dumps({"finished_at": "2026-09-02T12:00:00-05:00", "failed": ["ranks"]}),
        encoding="utf-8")
    con = duckdb_io.connect(cfg.duckdb_path)
    try:
        health = ex.archive_health(con, cfg)
    finally:
        con.close()
    assert health["failed_steps"] == ["ranks"]


def test_unreadable_status_file_does_not_crash_the_export(cfg):
    cfg.results_dir.mkdir(parents=True, exist_ok=True)
    (cfg.results_dir / "last_run_status.json").write_text("{ not json", encoding="utf-8")
    con = duckdb_io.connect(cfg.duckdb_path)
    try:
        health = ex.archive_health(con, cfg)
    finally:
        con.close()
    assert health["last_harvest_at"] is None


# ------------------------------ payload / write ------------------------------

def test_unbuilt_sections_are_absent_with_a_reason(cfg):
    payload = ex.build_payload(cfg)
    for section in ("signal_health", "benchmarks", "timing"):
        assert section not in payload, (
            f"{section} is emitted but its phase is not built; a zero or empty "
            f"chart is a claim we have not earned")
        assert section in payload["sections_absent"]
        assert payload["sections_absent"][section], "an absent section needs a reason"


def test_js_feed_is_written_from_the_same_payload(cfg):
    """The `file://` feed and the JSON must never disagree."""
    import json as _json
    payload = ex.build_payload(cfg)
    ex.write_atomic(cfg.results_dir / "dashboard_data.json", payload)
    ex.write_js(cfg.results_dir / "dashboard_data.js", payload)
    js = (cfg.results_dir / "dashboard_data.js").read_text(encoding="utf-8")
    assert js.startswith("window.__ZOLTAR_DATA__ = ") and js.rstrip().endswith(";")
    body = js[len("window.__ZOLTAR_DATA__ = "):].rstrip().rstrip(";")
    assert _json.loads(body) == _json.loads(
        (cfg.results_dir / "dashboard_data.json").read_text(encoding="utf-8"))


def test_schema_version_is_2_after_the_freshness_rename(cfg):
    """v2 renamed hours_since_last_run_ts -> hours_since_fresh. The old name was
    computed correctly and named after the trap, so any consumer that did not
    also read freshness_basis would read the name and believe it."""
    assert ex.build_payload(cfg)["schema_version"] == 2


def test_payload_has_the_contracted_top_level_keys(cfg):
    payload = ex.build_payload(cfg)
    assert payload["schema_version"] == ex.SCHEMA_VERSION
    for key in ("generated_at", "archive_health", "hypotheses", "shap_drift"):
        assert key in payload


def test_generated_at_is_tz_naive_chicago(cfg):
    """The contract says tz-naive America/Chicago, unlike run_history.jsonl,
    which is offset-aware. Different files, different rules, both deliberate."""
    stamped = dt.datetime.fromisoformat(ex.build_payload(cfg)["generated_at"])
    assert stamped.tzinfo is None


def test_write_is_atomic_and_leaves_no_temp_file(cfg, tmp_path):
    out = cfg.results_dir / "dashboard_data.json"
    ex.write_atomic(out, {"a": 1})
    assert json.loads(out.read_text(encoding="utf-8")) == {"a": 1}
    assert not list(cfg.results_dir.glob("*.tmp")), "atomic write left a temp file"


def test_write_failure_leaves_the_previous_file_intact(cfg):
    out = cfg.results_dir / "dashboard_data.json"
    ex.write_atomic(out, {"good": True})
    with pytest.raises(TypeError):
        ex.write_atomic(out, {"bad": {1, 2, 3}})     # sets are not JSON
    assert json.loads(out.read_text(encoding="utf-8")) == {"good": True}, (
        "a failed export must not destroy the last good feed")
    assert not list(cfg.results_dir.glob("*.tmp"))


def test_main_writes_the_feed(cfg, tmp_path):
    rc = ex.main(["--mode", "daily", "--config", str(tmp_path / "cfg.yaml")])
    assert rc == 0
    assert (cfg.results_dir / "dashboard_data.json").exists()
