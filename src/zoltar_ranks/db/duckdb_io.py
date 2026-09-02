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

    The scan is comment- and string-aware on purpose. A trailing `--` comment may
    legitimately contain a semicolon -- `-- split ratio (new/old); NULL for
    dividends` in schema.sql does -- and a naive `.split(";")` cuts the enclosing
    CREATE TABLE in half, which DuckDB reports as the thoroughly unhelpful
    "syntax error at end of input". Quoted literals are tracked so a `;` or `--`
    inside one is left alone.

    Only line comments are recognised; schema.sql uses no `/* */` blocks. Add
    handling here if that changes.
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(script)

    while i < n:
        ch = script[i]
        if quote is not None:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and script[i + 1] == quote:   # doubled -> escaped
                    buf.append(script[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
        elif script.startswith("--", i):
            nl = script.find("\n", i)
            i = n if nl == -1 else nl       # keep the newline; drop the comment
        elif ch == ";":
            out.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1

    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


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
