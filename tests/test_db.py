"""Tests for the DuckDB layer.

These exist because of a real failure: `split_statements` dropped only lines
that *began* with `--`, so the trailing comment on `corporate_actions.ratio`
("-- split ratio (new/old); NULL for dividends") was split on its own semicolon.
That truncated the CREATE TABLE mid-column-list, and every harvester died at
`connect()` with "syntax error at end of input". Nothing in the suite executed
schema.sql, so the whole backfill was blocked by a bug no test could see.

The lesson worth keeping: assert against the REAL schema.sql, not a fixture
copy of it. A fixture would not have contained the offending comment.
"""
from __future__ import annotations

import pytest

from zoltar_ranks.db import duckdb_io
from zoltar_ranks.db.duckdb_io import SCHEMA_SQL, split_statements

EXPECTED_TABLES = {
    "harvest_manifest", "ranks", "expected_returns", "shap_summary",
    "shap_labels", "prices_daily", "prices_intraday", "corporate_actions",
}
EXPECTED_VIEWS = {"morning_ranks"}


# ----------------------------- the splitter -----------------------------

def test_split_statements_ignores_semicolon_inside_comment_pure():
    """The exact regression: a `;` in a trailing comment must not split."""
    sql = (
        "CREATE TABLE t (\n"
        "    a DOUBLE,   -- split ratio (new/old); NULL for dividends\n"
        "    b DOUBLE\n"
        ");\n"
    )
    stmts = split_statements(sql)
    assert len(stmts) == 1, f"comment semicolon split the statement: {stmts}"
    assert "b DOUBLE" in stmts[0]
    assert "NULL for dividends" not in stmts[0], "comment text leaked into SQL"


def test_split_statements_keeps_semicolon_inside_string_literal_pure():
    sql = "INSERT INTO t VALUES ('a;b'); SELECT 1;"
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].endswith("('a;b')")


def test_split_statements_keeps_comment_marker_inside_string_pure():
    sql = "SELECT '-- not a comment' AS x; SELECT 2;"
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert "not a comment" in stmts[0]


def test_split_statements_handles_doubled_quote_escape_pure():
    sql = "SELECT 'it''s; fine' AS x; SELECT 2;"
    stmts = split_statements(sql)
    assert len(stmts) == 2, f"escaped quote mis-parsed: {stmts}"


def test_split_statements_drops_full_line_comments_pure():
    sql = "-- leading comment\nSELECT 1;\n-- trailing comment\n"
    assert split_statements(sql) == ["SELECT 1"]


def test_schema_sql_splits_into_parseable_statements_pure():
    """Every statement in the real schema must be self-contained."""
    stmts = split_statements(SCHEMA_SQL.read_text())
    assert stmts, "schema.sql produced no statements"
    for s in stmts:
        assert s.count("(") == s.count(")"), f"unbalanced parens, statement truncated:\n{s}"
        assert s.upper().startswith(("CREATE", "ALTER")), f"not a complete statement:\n{s}"


# --------------------------- schema.sql executes ---------------------------

def test_connect_creates_every_table_and_view_pure(tmp_path):
    """The test that would have caught the outage: actually run schema.sql."""
    con = duckdb_io.connect(tmp_path / "t.duckdb")
    try:
        got = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()
    assert EXPECTED_TABLES <= got, f"missing tables: {sorted(EXPECTED_TABLES - got)}"
    assert EXPECTED_VIEWS <= got, f"missing views: {sorted(EXPECTED_VIEWS - got)}"


def test_connect_is_idempotent_pure(tmp_path):
    """schema.sql runs on every connect; a second one must not raise."""
    path = tmp_path / "t.duckdb"
    duckdb_io.connect(path).close()
    duckdb_io.connect(path).close()


def test_corporate_actions_has_its_full_column_list_pure(tmp_path):
    """The truncated statement lost every column after `ratio`."""
    con = duckdb_io.connect(tmp_path / "t.duckdb")
    try:
        cols = {r[0] for r in con.execute("DESCRIBE corporate_actions").fetchall()}
    finally:
        con.close()
    assert cols == {"ex_date", "symbol", "kind", "ratio", "amount", "provider"}


# ------------------------------ upsert semantics ------------------------------

def test_upsert_new_rows_is_append_only_and_idempotent_pure(tmp_path):
    """Rule 2: re-inserting the same keys must insert zero rows, never UPDATE."""
    pd = pytest.importorskip("pandas")
    con = duckdb_io.connect(tmp_path / "t.duckdb")
    try:
        df = pd.DataFrame({
            "run_ts": pd.to_datetime(["2026-03-14 07:46:57"] * 2),
            "symbol": ["AAPL", "MSFT"],
            "risk_bucket": ["low", "low"],
            "score": [1.0, 2.0],
        })
        seen, inserted = duckdb_io.upsert_new_rows(con, "ranks", df, ["run_ts", "symbol", "risk_bucket"])
        assert (seen, inserted) == (2, 2)

        # Same keys, DIFFERENT score: must be ignored, not overwritten.
        restated = df.assign(score=[99.0, 99.0])
        seen, inserted = duckdb_io.upsert_new_rows(con, "ranks", restated, ["run_ts", "symbol", "risk_bucket"])
        assert inserted == 0, "re-running a harvester inserted rows"
        kept = con.execute("SELECT score FROM ranks ORDER BY symbol").fetchall()
        assert [r[0] for r in kept] == [1.0, 2.0], "an existing score was restated"
    finally:
        con.close()


# ------------------- rule 5: available_at, not run_ts -------------------

def test_ranks_pit_exposes_availability_columns_pure(tmp_path):
    con = duckdb_io.connect(tmp_path / "t.duckdb")
    try:
        cols = {r[0] for r in con.execute("DESCRIBE ranks_pit").fetchall()}
    finally:
        con.close()
    for c in ("available_at", "stamp_is_forward", "availability_source", "harvest_lag_days"):
        assert c in cols, f"ranks_pit missing {c}"


def test_forward_stamped_row_is_available_before_its_run_ts_pure(tmp_path):
    """FINDINGS F4: upstream stamps some builds LATER than it published them.

    Rule 5's whole point -- a rank published at 19:36 is actionable in extended
    hours, ~13h before the next regular open, even though run_ts says tomorrow.
    available_at must reflect publication, not the stamp.
    """
    pd = pytest.importorskip("pandas")
    con = duckdb_io.connect(tmp_path / "t.duckdb")
    try:
        con.execute("""INSERT INTO harvest_manifest (file_path, commit_sha, committed_at)
                       VALUES ('f.pkl', 'sha_fwd', TIMESTAMP '2026-09-01 19:36:25'),
                              ('f.pkl', 'sha_nrm', TIMESTAMP '2026-09-01 08:00:00')""")
        df = pd.DataFrame({
            "run_ts": pd.to_datetime(["2026-09-02 19:34:49", "2026-09-01 07:46:57"]),
            "symbol": ["FWD", "NRM"],
            "risk_bucket": ["low", "low"],
            "first_seen_sha": ["sha_fwd", "sha_nrm"],
        })
        duckdb_io.upsert_new_rows(con, "ranks", df, ["run_ts", "symbol", "risk_bucket"])
        got = {r[0]: r[1:] for r in con.execute(
            "SELECT symbol, available_at, stamp_is_forward, availability_source FROM ranks_pit").fetchall()}
    finally:
        con.close()

    fwd_at, fwd_flag, fwd_src = got["FWD"]
    assert fwd_flag is True, "forward-stamped row not flagged"
    assert fwd_src == "committed_at"
    assert str(fwd_at) == "2026-09-01 19:36:25", "available_at must be the publication time"
    assert fwd_at < pd.Timestamp("2026-09-02 19:34:49"), "available_at must precede run_ts here"

    nrm_at, nrm_flag, nrm_src = got["NRM"]
    assert nrm_flag is False
    assert nrm_src == "run_ts"
    assert str(nrm_at) == "2026-09-01 07:46:57"


def test_placeholder_carries_no_tradability_verdict_pure():
    """Rule 5 was rewritten: `placeholder` is descriptive, not an exclusion."""
    import pathlib
    text = pathlib.Path("CLAUDE.md").read_text(encoding="utf-8")
    assert "No execution decision may key off `run_ts`" in text
    assert "not tradable at their timestamp" not in text, "old rule 5 still present"
