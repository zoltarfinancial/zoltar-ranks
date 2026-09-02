"""Write `data/results/dashboard_data.json` — the one file the two workstreams meet at.

Contract: `docs/DASHBOARD.md`. The dashboard never touches DuckDB, imports
`zoltar_ranks`, or reads Parquet; this file is the whole interface.

**Only the sections that are actually measured are emitted.** `signal_health`,
`benchmarks` and `timing` need Phases 4-6, which do not exist, so they are
absent rather than zero-filled. The contract requires that: the dashboard
renders an explicit "not yet measured" state, and a zero or an empty chart would
be a claim we have not earned. `sections_absent` names them, with the reason, so
the page can say *why* rather than just showing a gap.

One trap worth naming, because it is the same one that has bitten this repo
twice: **freshness must not be computed from `run_ts`.** Upstream forward-stamps
the evening retrain, so the newest `run_ts` in the archive is routinely in the
FUTURE (`2026-09-03 00:00:00` while it is 2026-09-02). "Hours since" then goes
negative, the dashboard's ~24h staleness alarm never fires, and a dead harvester
looks perfectly healthy. Freshness comes from `available_at` (rule 5).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io

log = logging.getLogger("export_dashboard_data")

SCHEMA_VERSION = 2
REPO_ROOT = Path(__file__).resolve().parents[3]

#: Bound the SHAP payload. 327 snapshots x 3 segments x every feature would be a
#: multi-megabyte file the page has to parse on every load, for a drift view that
#: only needs the leading features and the recent past.
SHAP_SNAPSHOTS = 60
SHAP_TOP_N = 10

#: Why each unbuilt section is missing. Shown by the dashboard instead of a gap.
ABSENT_REASONS = {
    "signal_health": "Phase 4 (analysis/metrics.py, analysis/stats.py) not built - "
                     "IC without a confidence interval would violate rule 6.",
    "benchmarks": "Phase 3 (analysis/backtest.py) not built. The no_same_bar and "
                  "no_run_ts_execution gates must exist before any benchmark "
                  "number is quoted.",
    "timing": "Phase 6 not built. It also depends on the Phase 2 clock-alignment "
              "anchor (docs/ALIGNMENT_ANCHOR.md).",
}


# ------------------------------- archive health -------------------------------

def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v is not None else None)


def archive_health(con, cfg: Config) -> dict:
    feeds = [
        {"feed": f, "risk_bucket": rb, "runs": runs,
         "first_run": _iso(first), "last_run": _iso(last), "rows": rows}
        for f, rb, runs, first, last, rows in con.execute("""
            SELECT feed, risk_bucket, count(DISTINCT run_ts), min(run_ts),
                   max(run_ts), count(*)
            FROM ranks GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
    ]

    last_run_ts, last_available_at = con.execute(
        "SELECT max(run_ts), max(available_at) FROM ranks_pit").fetchone()

    now = datetime.now()
    hours = round((now - last_available_at).total_seconds() / 3600, 2) \
        if last_available_at is not None else None

    status_path = cfg.results_dir / "last_run_status.json"
    last_harvest_at, failed_steps = None, []
    if status_path.exists():
        try:
            st = json.loads(status_path.read_text(encoding="utf-8"))
            last_harvest_at = st.get("finished_at")
            failed_steps = st.get("failed") or []
        except Exception as exc:            # noqa: BLE001
            log.warning("could not read %s: %s", status_path, exc)

    er = con.execute("""SELECT count(DISTINCT as_of_date), min(as_of_date),
                               max(as_of_date) FROM expected_returns""").fetchone()
    shap_n = con.execute("SELECT count(DISTINCT snapshot_ts) FROM shap_summary").fetchone()[0]
    segments = [r[0] for r in con.execute(
        "SELECT DISTINCT segment FROM shap_summary ORDER BY 1").fetchall()]

    return {
        "feeds": feeds,
        "last_harvest_at": last_harvest_at,
        "failed_steps": failed_steps,
        "last_run_ts": _iso(last_run_ts),
        "last_available_at": _iso(last_available_at),
        # From available_at, NOT run_ts. See the module docstring: the newest
        # run_ts is routinely in the future because upstream forward-stamps the
        # evening retrain, which would make this negative and disarm the alarm.
        #
        # The name matters as much as the computation. `hours_since_last_run_ts`
        # was computed correctly and named after the trap, so any consumer that
        # did not also read `freshness_basis` would read the name and believe it.
        # schema_version 2 renames it; the old key is a DEPRECATED ALIAS carried
        # for one version so the console does not break mid-rename.
        "hours_since_fresh": hours,
        "hours_since_last_run_ts": hours,   # DEPRECATED alias, remove in v3
        "freshness_basis": "available_at",
        "expected_returns": {"as_of_dates": er[0], "first": _iso(er[1]), "last": _iso(er[2])},
        "shap": {"snapshots": shap_n, "segments": segments},
    }


# --------------------------------- hypotheses ---------------------------------

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _clean(cell: str) -> str | None:
    """Strip markdown emphasis and code ticks; empty becomes None, never ''."""
    s = re.sub(r"[*`]", "", cell).strip()
    return s or None


#: A number the cell LEADS with, e.g. "0.157%/run (n=63, ~9.9%/yr)" -> 0.157.
#: Anchored to the start on purpose: a loose search would read
#: "TBD (n~64 paired days)" as an MDE of 64, which is not a missing value but a
#: fabricated one -- far worse, because it would render as a real number.
_LEADING_NUM_RE = re.compile(r"\s*(-?\d+(?:\.\d+)?)\s*%?")


def _num(cell: str | None) -> float | None:
    """The leading number if the cell starts with one; otherwise None, never 0."""
    if not cell:
        return None
    m = _LEADING_NUM_RE.match(cell.replace(",", ""))
    return float(m.group(1)) if m else None


def _ci(cell: str | None) -> tuple[float | None, float | None]:
    if not cell:
        return None, None
    nums = _NUM_RE.findall(cell)
    return (float(nums[0]), float(nums[1])) if len(nums) >= 2 else (None, None)


def hypotheses(path: Path) -> list[dict]:
    """Parse the register. `docs/HYPOTHESES.md` stays the single source of truth.

    The row count is the FDR denominator (rule 7), so every row is emitted --
    including rejected ones. Dropping a rejected row here would silently shrink
    the denominator and inflate every corrected p-value on the page.
    """
    if not path.exists():
        log.warning("hypothesis register not found at %s", path)
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 10 or not re.fullmatch(r"H\d+[a-z]?", cells[0].strip()):
            continue        # header, separator, or a table that is not the register
        ci_low, ci_high = _ci(_clean(cells[8]))
        rows.append({
            "id": _clean(cells[0]),
            "date_registered": _clean(cells[1]),
            "statement": _clean(cells[2]),
            "lever": _clean(cells[3]),
            "test": _clean(cells[4]),
            "mde": _num(_clean(cells[5])),
            "mde_raw": _clean(cells[5]),     # 'TBD' and prose survive as text
            "status": _clean(cells[6]),
            "result": _num(_clean(cells[7])),
            "ci_low": ci_low,
            "ci_high": ci_high,
            "fdr_p": _num(_clean(cells[9])),
        })
    return rows


# --------------------------------- shap drift ---------------------------------

def shap_drift(con) -> list[dict]:
    return [
        {"snapshot_ts": _iso(ts), "segment": seg, "feature": feat,
         "mean_abs_shap": round(v, 6), "rank": int(rk)}
        for ts, seg, feat, v, rk in con.execute(f"""
            WITH recent AS (
                SELECT DISTINCT snapshot_ts FROM shap_summary
                ORDER BY snapshot_ts DESC LIMIT {SHAP_SNAPSHOTS}
            ), agg AS (
                SELECT s.snapshot_ts, s.segment, s.feature,
                       avg(abs(s.value)) AS mean_abs_shap
                FROM shap_summary s JOIN recent r USING (snapshot_ts)
                GROUP BY 1, 2, 3
            )
            SELECT snapshot_ts, segment, feature, mean_abs_shap, rank_
            FROM (SELECT *, row_number() OVER (PARTITION BY snapshot_ts, segment
                                               ORDER BY mean_abs_shap DESC) AS rank_
                  FROM agg)
            WHERE rank_ <= {SHAP_TOP_N}
            ORDER BY snapshot_ts DESC, segment, rank_""").fetchall()
    ]


# ----------------------------------- driver -----------------------------------

def build_payload(cfg: Config) -> dict:
    con = duckdb_io.connect(cfg.duckdb_path)
    try:
        payload = {
            # tz-naive America/Chicago, matching the archive and the contract.
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "schema_version": SCHEMA_VERSION,
            "archive_health": archive_health(con, cfg),
            "hypotheses": hypotheses(REPO_ROOT / "docs" / "HYPOTHESES.md"),
            "shap_drift": shap_drift(con),
            "sections_absent": ABSENT_REASONS,
        }
    finally:
        con.close()
    return payload


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_atomic(path: Path, payload: dict) -> None:
    """Temp file then replace, so the dashboard never reads a half-written file."""
    _write_text_atomic(path, json.dumps(payload, indent=2))


def write_js(path: Path, payload: dict) -> None:
    """The `file://` feed: a `<script src>` works where `fetch` does not.

    Written from the SAME payload object as the JSON, in the same call, so the
    two can never disagree. Both live in `data/results/` -- generated feeds are
    the backend's to write, and nothing but Cowork writes under `dashboard/`.
    """
    _write_text_atomic(path, "window.__ZOLTAR_DATA__ = "
                       + json.dumps(payload, indent=2) + ";\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", default="daily")      # accepted, unused: daily.py passes it
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)
    payload = build_payload(cfg)
    out = cfg.results_dir / "dashboard_data.json"
    write_atomic(out, payload)
    write_js(cfg.results_dir / "dashboard_data.js", payload)
    log.info("wrote %s (+ .js, schema v%d): %d feed row(s), %d hypothesis row(s), "
             "%d shap row(s); absent: %s", out, SCHEMA_VERSION,
             len(payload["archive_health"]["feeds"]), len(payload["hypotheses"]),
             len(payload["shap_drift"]), ", ".join(sorted(ABSENT_REASONS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
