"""Thin DuckDB helpers. DuckDB is the system of record for the archive."""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

SCHEMA_SQL = Path(__file__).with_name("schema.sql")


def split_statements(script: str) -> list[str]:
    """Split a .sql file into individual statements, ignoring `--` comments.

    DuckDB's Python `execute()` takes one statement at a time, so we split here
    rather than relying on driver behaviour that varies across versions.
    """
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def connect(db_path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        for stmt in split_statements(SCHEMA_SQL.read_text()):
            con.execute(stmt)
    return con


def upsert_new_rows(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame,
                    key_cols: list[str]) -> tuple[int, int]:
    """Insert rows whose primary key is new. Returns (rows_seen, rows_inserted)."""
    if df.empty:
        return 0, 0
    schema_cols = list(con.execute(f"DESCRIBE {table}").df()["column_name"])
    cols = [c for c in schema_cols if c in df.columns]
    staged = df[cols]
    con.register("_staging", staged)
    on = " AND ".join(f"t.{k} IS NOT DISTINCT FROM s.{k}" for k in key_cols)
    before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    con.execute(f"""
        INSERT INTO {table} ({', '.join(cols)})
        SELECT {', '.join('s.' + c for c in cols)}
        FROM (SELECT DISTINCT ON ({', '.join(key_cols)}) * FROM _staging) s
        WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {on})
    """)
    after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    con.unregister("_staging")
    return len(staged), after - before


def export_parquet(con: duckdb.DuckDBPyConnection, table: str, out_dir: Path,
                   partition_expr: str | None = None) -> Path:
    out_dir = Path(out_dir) / table
    out_dir.mkdir(parents=True, exist_ok=True)
    if partition_expr:
        con.execute(f"""
            COPY (SELECT *, {partition_expr} AS ym FROM {table})
            TO '{out_dir.as_posix()}'
            (FORMAT PARQUET, PARTITION_BY (ym), OVERWRITE_OR_IGNORE 1)
        """)
    else:
        con.execute(f"COPY {table} TO '{(out_dir / (table + '.parquet')).as_posix()}' (FORMAT PARQUET)")
    return out_dir
