"""The daily alignment anchor — §3 of `docs/ALIGNMENT_ANCHOR.md`.

**Which trade date's close does `ranks.close_price` carry on a morning build?**

Assumed, never measured, and it sets the origin for every forward return in the
repo. Phase 2 joins two clock domains for the first time: ranks are tz-naive
America/Chicago, providers return a UTC or ET grid. A one-hour or one-session
misalignment would not degrade the timing study's 0.157% MDE — it would
**manufacture** a large, clean, entirely spurious effect, and it would pass every
test in the repo because both sides are internally consistent.

Three things this does that a looser version would get wrong:

* **RAW prices.** `YFinanceProvider(adjusted=False)`. Adjusted history is
  restated backwards for every dividend, so a 2026-03 close pulled today differs
  from what the model saw by the sum of subsequent dividends — the anchor would
  fail for reasons having nothing to do with the clock.
* **A real session grid.** `T-1` is the prior *trading session*, not
  `date - 1 day`. A naive decrement is wrong on every Monday and every
  post-holiday session, which presents as ~25% noise rather than as an error —
  the worst shape a bug can have.
* **Per-date reporting.** A pooled 97% can be 19 clean dates and one
  catastrophic one, and a DST bug affects exactly one date.

The decision tolerance is **5 bps**, not PLAN §2c's 50. The reconciliation test
asks *is this the right price*; the anchor asks *is this the right observation*.
At 50 bps roughly a third of symbol-days have |daily return| < 0.5%, so close(T)
and close(T-1) both "match" and the test cannot separate them. A tolerance wide
enough to absorb the error you are looking for is worthless.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date, timedelta

import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io
from zoltar_ranks.sources.prices import ProviderUnavailable, YFinanceProvider

log = logging.getLogger("alignment_anchor")

#: Reported at three tolerances so the shape is visible; the verdict is stated on
#: the tightest. 5 bps is float round-tripping plus vendor differences on the
#: consolidated close, both well under it.
TOLERANCES = (0.0005, 0.0050, 0.0200)
DECISION_TOL = 0.0005

#: Always-traded names, intersected, to build the session grid. Not a holiday
#: list: a calendar package disagreeing with the provider is itself the finding.
CALENDAR_SYMBOLS = ("SPY", "AAPL", "MSFT")

#: The US open in the archive's wall clock. `classify_run` labels `morning` as
#: hour < 9.0, so the class STRADDLES the open and the two halves are reported
#: separately -- if they disagree, `morning_ranks` is not a homogeneous class.
OPEN_HOUR_CT = 8.5

PASS_RATE = 0.95
PASS_SEPARATION = 0.20


def wilson(k: int, n: int, z: float = 1.959963985) -> tuple[float, float]:
    """95% interval on a proportion. Rule 6: no bare rate."""
    if n == 0:
        return (float("nan"), float("nan"))
    p, z2 = k / n, z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def session_grid(provider: YFinanceProvider, start: date, end: date) -> list[date]:
    """Trading sessions, from the provider's own returned bars."""
    df = provider.daily(list(CALENDAR_SYMBOLS), start, end)
    per_symbol = [set(g["trade_date"]) for _, g in df.groupby("symbol")]
    if not per_symbol:
        raise RuntimeError("no calendar bars returned; cannot build a session grid")
    return sorted(set.intersection(*per_symbol))


def morning_runs(con, limit: int, extra_dates: list[str]) -> pd.DataFrame:
    """The sample, fixed by rule so it cannot be tuned on the result.

    The `daily` feed only -- append-only, not the rolling buffer.
    """
    df = con.execute("""
        SELECT DISTINCT run_ts, available_at
        FROM ranks_pit
        WHERE run_kind = 'morning' AND feed = 'daily'
        ORDER BY available_at DESC
    """).df()
    df["run_ts"] = pd.to_datetime(df["run_ts"])
    df["available_at"] = pd.to_datetime(df["available_at"])
    recent = df.head(limit)
    wanted = {pd.Timestamp(d).date() for d in extra_dates}
    pinned = df[df["run_ts"].dt.date.isin(wanted)]
    out = pd.concat([recent, pinned]).drop_duplicates(subset=["run_ts"])
    return out.sort_values("run_ts").reset_index(drop=True)


def _prev_session(grid: list[date], d: date, back: int) -> date | None:
    """The `back`-th trading session strictly before `d`."""
    idx = [i for i, g in enumerate(grid) if g < d]
    return grid[idx[-back]] if len(idx) >= back else None


def compare(cfg: Config, limit: int = 15, extra_dates: list[str] | None = None,
            max_symbols: int | None = 400) -> dict:
    extra_dates = extra_dates or []
    provider = YFinanceProvider(cfg.price_cache_dir, adjusted=False)
    con = duckdb_io.connect(cfg.duckdb_path, read_only=True)
    try:
        runs = morning_runs(con, limit, extra_dates)
        if runs.empty:
            raise RuntimeError("no morning runs in the daily feed")
        lo = runs["run_ts"].min().date() - timedelta(days=14)
        hi = runs["run_ts"].max().date() + timedelta(days=3)
        grid = session_grid(provider, lo, hi)

        rows = []
        for _, r in runs.iterrows():
            run_ts = r["run_ts"]
            ranks = con.execute("""
                SELECT symbol, close_price FROM ranks
                WHERE run_ts = ? AND risk_bucket = 'low'
                  AND close_price IS NOT NULL AND close_price > 0
                ORDER BY symbol
            """, [run_ts]).df()
            if max_symbols:
                ranks = ranks.head(max_symbols)
            if ranks.empty:
                continue

            t = run_ts.date()
            same = t if t in grid else None
            t1 = _prev_session(grid, t, 1)
            t2 = _prev_session(grid, t, 2)
            span_lo = min(d for d in (same, t1, t2) if d)
            span_hi = max(d for d in (same, t1, t2) if d)
            px = provider.daily(list(ranks["symbol"]), span_lo, span_hi)
            if px.empty:
                log.warning("no provider bars for %s", run_ts)
                continue

            hour = run_ts.hour + run_ts.minute / 60
            for cand_name, cand_date, field in (
                    ("close_T_minus_1", t1, "close"),
                    ("close_T", same, "close"),
                    ("close_T_minus_2", t2, "close"),
                    ("open_T", same, "open")):
                if cand_date is None:
                    continue
                sub = px[px["trade_date"] == cand_date][["symbol", field]]
                m = ranks.merge(sub, on="symbol", how="inner")
                if m.empty:
                    continue
                dev = (m["close_price"] / m[field] - 1.0).abs()
                for tol in TOLERANCES:
                    k = int((dev <= tol).sum())
                    lo_ci, hi_ci = wilson(k, len(dev))
                    rows.append({
                        "run_ts": str(run_ts), "trade_date": str(t),
                        "subset": "pre_open" if hour < OPEN_HOUR_CT else "post_open",
                        "candidate": cand_name, "candidate_date": str(cand_date),
                        "tolerance_bps": round(tol * 10000),
                        "n": int(len(dev)), "matched": k,
                        "rate": k / len(dev), "ci_low": lo_ci, "ci_high": hi_ci,
                        "median_abs_dev": float(dev.median()),
                    })
    finally:
        con.close()

    detail = pd.DataFrame(rows)
    return {"detail": detail, "verdict": verdict(detail)}


def verdict(detail: pd.DataFrame) -> dict:
    """§3.5's decision rule. Reports; it does not reconcile or widen."""
    if detail.empty:
        return {"state": "NO_DATA", "note": "no comparisons were produced"}
    d = detail[detail["tolerance_bps"] == round(DECISION_TOL * 10000)]
    piv = d.pivot_table(index=["run_ts", "subset"], columns="candidate",
                        values="rate", aggfunc="first")
    prior = piv.get("close_T_minus_1")
    same = piv.get("close_T")
    if prior is None:
        return {"state": "NO_DATA", "note": "close(T-1) never resolved"}
    same = same if same is not None else pd.Series(0.0, index=piv.index)

    per_date = [{"run_ts": i[0], "subset": i[1],
                 "p_prior": float(prior.loc[i]),
                 "p_same": float(same.loc[i]) if i in same.index else None}
                for i in piv.index]
    worst = min(per_date, key=lambda r: r["p_prior"])
    sep = float((prior - same.reindex(prior.index).fillna(0)).min())

    if (same.reindex(prior.index).fillna(0) > prior).any():
        state, note = "STOP_LOOKAHEAD", (
            "close(T) matches better than close(T-1) on at least one date. A "
            "morning build would be carrying a price it could not have known; "
            "that invalidates Phases 3 and 6, not just Phase 2.")
    elif worst["p_prior"] >= PASS_RATE and sep >= PASS_SEPARATION:
        state, note = "PASS", "anchor is close(T-1) on every date"
    else:
        state, note = "STOP_AMBIGUOUS", (
            f"close(T-1) reaches only {worst['p_prior']:.3f} on "
            f"{worst['run_ts']} (need {PASS_RATE}), separation {sep:.3f} "
            f"(need {PASS_SEPARATION}). Report the nearest-field diagnostic; do "
            f"NOT widen the tolerance to manufacture a pass.")
    return {"state": state, "note": note, "min_p_prior": worst["p_prior"],
            "min_separation": sep, "dates": len(per_date), "per_date": per_date}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--max-symbols", type=int, default=400)
    ap.add_argument("--dates", nargs="*", default=[],
                    help="extra run dates to pin (DST and holiday-adjacent)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    cfg = Config.load(args.config)
    res = compare(cfg, limit=args.limit, extra_dates=args.dates,
                  max_symbols=args.max_symbols)
    detail, v = res["detail"], res["verdict"]

    dec = detail[detail["tolerance_bps"] == round(DECISION_TOL * 10000)]
    print(f"\nDAILY ANCHOR  decision tolerance {round(DECISION_TOL*10000)} bps\n")
    summary = (dec.groupby("candidate")
                  .agg(dates=("run_ts", "nunique"), n=("n", "sum"),
                       matched=("matched", "sum"))
                  .assign(rate=lambda d: d["matched"] / d["n"])
                  .sort_values("rate", ascending=False))
    for cand, row in summary.iterrows():
        lo, hi = wilson(int(row["matched"]), int(row["n"]))
        print(f"  {cand:<18} {row['rate']:7.4f}  95% CI [{lo:.4f}, {hi:.4f}]  "
              f"n={int(row['n']):,} over {int(row['dates'])} date(s)")

    print(f"\n  per-date close(T-1) rate, worst first:")
    for r in sorted(v.get("per_date", []), key=lambda r: r["p_prior"])[:8]:
        print(f"    {r['run_ts'][:19]}  {r['subset']:<9}  "
              f"prior={r['p_prior']:.4f}  same={r['p_same']}")

    print(f"\n  VERDICT  {v['state']}\n           {v['note']}\n")
    if args.out:
        payload = {"verdict": v, "detail": detail.to_dict(orient="records")}
        p = cfg.results_dir / args.out
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"  wrote {p}")
    return 0 if v["state"] == "PASS" else 1




# =============================================================================
# THE INTRADAY ANCHOR -- docs/ALIGNMENT_ANCHOR.md section 4, order c-0002.1
# =============================================================================
#
# Does an intraday rank's `close_price`, read at its `available_at` as
# America/Chicago wall clock, land on the minute bar the build actually saw?
#
# This is the only irreversible measurement on the board: yfinance serves
# 1-minute bars ~29 days back (measured 2026-09-10: 08-12 served, 08-11 refused),
# so the dense era (2026-08-19 onward) stops being measurable around 2026-09-17.
# The raw bars are therefore FETCHED AND PERSISTED FIRST, per request window,
# before any analysis -- the measurement can be redone from the local cache
# forever; the bars cannot be re-fetched after the window closes.

import json as _json

DENSE_ERA_START = date(2026, 8, 19)
INTRADAY_CACHE = "intraday_1m"


def _intraday_cache_dir(cfg: Config) -> Path:
    from pathlib import Path as _P
    d = _P(cfg.price_cache_dir) / "yfinance" / INTRADAY_CACHE
    d.mkdir(parents=True, exist_ok=True)
    return d


def dense_universe(con) -> list[str]:
    """Every symbol in any dense-era intraday run -- the runs' own universe (§4.2)."""
    return [r[0] for r in con.execute("""
        SELECT DISTINCT symbol FROM ranks
        WHERE run_kind = 'intraday' AND run_ts >= ?
        ORDER BY 1""", [DENSE_ERA_START]).fetchall()]


def fetch_intraday_bars(cfg: Config, symbols: list[str], start: date, end: date,
                        refresh: bool = False) -> dict:
    """Fetch 1-minute bars window by window (8 days each) and persist each one.

    Returns {"windows": [...]} describing, per window, rows, symbols served and
    any ProviderUnavailable requests. A window already on disk is reused, so an
    interrupted fetch resumes instead of starting over.
    """
    from pathlib import Path as _P
    prov = YFinanceProvider(cfg.price_cache_dir, adjusted=False)
    span = prov.MAX_INTRADAY_DAYS["1min"]
    cache = _intraday_cache_dir(cfg)
    out = {"requested_symbols": len(symbols), "windows": []}
    w0 = start
    while w0 <= end:
        w1 = min(w0 + timedelta(days=span), end + timedelta(days=1))   # exclusive
        stem = f"{w0}_{w1}"
        pq, meta = cache / f"{stem}.parquet", cache / f"{stem}.json"
        if pq.exists() and meta.exists() and not refresh:
            info = _json.loads(meta.read_text(encoding="utf-8"))
            info["from_cache"] = True
        else:
            try:
                bars, cov = prov.fetch_intraday(symbols, w0, w1 - timedelta(days=1), "1min")
                bars.to_parquet(pq, index=False)
                info = {"start": str(w0), "end_exclusive": str(w1),
                        "rows": int(len(bars)),
                        "symbols_served": int(bars["symbol"].nunique()),
                        "symbols_missing": sorted(cov.missing),
                        "sessions": sorted({str(d) for d in bars["ts"].dt.date}),
                        "unavailable": cov.unavailable, "from_cache": False}
            except ProviderUnavailable as exc:
                info = {"start": str(w0), "end_exclusive": str(w1), "rows": 0,
                        "symbols_served": 0, "sessions": [],
                        "unavailable": [{"start": str(w0), "end_exclusive": str(w1),
                                         "symbols": len(symbols),
                                         "reason": f"ProviderUnavailable: {exc}"}],
                        "from_cache": False}
            meta.write_text(_json.dumps(info, indent=2), encoding="utf-8")
        out["windows"].append(info)
        log.info("window %s..%s: %s rows, %s symbols, %d unavailable request(s)%s",
                 info["start"], info["end_exclusive"], info["rows"],
                 info["symbols_served"], len(info["unavailable"]),
                 " (cache)" if info.get("from_cache") else "")
        w0 = w1
    return out


def load_intraday_bars(cfg: Config) -> pd.DataFrame:
    parts = sorted(_intraday_cache_dir(cfg).glob("*.parquet"))
    if not parts:
        return pd.DataFrame(columns=["ts", "symbol", "close"])
    df = pd.concat([pd.read_parquet(p, columns=["ts", "symbol", "close"]) for p in parts],
                   ignore_index=True)
    return df.drop_duplicates(["symbol", "ts"]).sort_values(["symbol", "ts"])


# ---------------------------------------------------------------------------
# The measurement. EVERY constant below was fixed before any bar was examined,
# and is written into the output JSON as `pre_registered`, so a reader can check
# the rules were not chosen to fit the result.
# ---------------------------------------------------------------------------

import numpy as _np

#: Fine offset grid, minutes. The spec's coarse grid ({0, +-1, +-30, +-60, +-120,
#: +-300, +-360}) can only SELECT a cell; the order asks for a measured offset
#: WITH a confidence interval, which needs a fine grid.
FINE_GRID = tuple(range(-90, 91))
#: The clock-domain hypotheses (spec 4.3): 0, ET-vs-CT (+-1h), a double
#: conversion (+-2h), UTC-vs-CT with and without DST (+-5h, +-6h).
TZ_CELLS = (0, 60, -60, 120, -120, 300, -300, 360, -360)
#: A bar more than this far before the target is a stale price, not "the price
#: at the target". Thin names with no trade in 10 minutes drop out of that cell.
BAR_TOLERANCE_MIN = 10
#: Runs whose available_at is at least 10 minutes inside the regular session
#: (08:30-15:00 CT). PRECLOSE runs after 15:00 read a post-close price and would
#: measure the close, not the clock.
SESSION_WINDOW_CT = ((8, 40), (14, 50))
MIN_SYMBOLS_PER_CELL = 50
POWER_RATIO = 3.0          # spec 4.4: MARD(+-1h) >= 3 x MARD(best), else no power
PRECISION_BPS = 25.0       # spec 4.4
PASS_OFFSET_MIN = 1.0      # +-1 bar: the spec names +-1 min as benign labelling
BOOT_B = 10_000
BOOT_SEED = 20260910


def _in_session(ts) -> bool:
    (h0, m0), (h1, m1) = SESSION_WINDOW_CT
    t = ts.hour * 60 + ts.minute
    return h0 * 60 + m0 <= t <= h1 * 60 + m1


def intraday_runs(con) -> pd.DataFrame:
    """Dense-era intraday runs, anchored on available_at (rule 5)."""
    df = con.execute("""
        SELECT DISTINCT run_ts, available_at FROM ranks_pit
        WHERE run_kind = 'intraday' AND run_ts >= ? AND risk_bucket = 'low'
        ORDER BY available_at""", [DENSE_ERA_START]).df()
    df["run_ts"] = pd.to_datetime(df["run_ts"])
    df["available_at"] = pd.to_datetime(df["available_at"])
    df["in_session"] = df["available_at"].map(_in_session)
    df["date"] = df["available_at"].dt.date
    return df


def rank_prices(con, run_ts_list) -> pd.DataFrame:
    df = con.execute("""
        SELECT run_ts, symbol, close_price FROM ranks
        WHERE run_ts IN (SELECT UNNEST(?)) AND risk_bucket = 'low'
          AND close_price IS NOT NULL AND close_price > 0""",
                     [list(run_ts_list)]).df()
    df["run_ts"] = pd.to_datetime(df["run_ts"])
    return df


def deviation_table(pairs: pd.DataFrame, bars: pd.DataFrame, offsets) -> pd.DataFrame:
    """For each (run, offset h): median |close_price / bar_close - 1| over symbols.

    `pairs` has run_ts, available_at, symbol, close_price. The comparison price for
    offset h is the close of the last bar whose OPEN time <= available_at + h
    (spec 4.3), no older than BAR_TOLERANCE_MIN minutes.
    """
    # One timestamp resolution on both sides. DuckDB hands back datetime64[us],
    # parquet round-trips the bars as datetime64[ms], and merge_asof refuses to
    # join them. Caught on the first REAL run -- the synthetic tests were all ns.
    bars = bars[["ts", "symbol", "close"]].copy()
    bars["ts"] = bars["ts"].astype("datetime64[ns]")
    bars = bars.sort_values("ts").reset_index(drop=True)
    left = pairs[["run_ts", "available_at", "symbol", "close_price"]].copy()
    left["available_at"] = left["available_at"].astype("datetime64[ns]")
    left = left.reset_index(drop=True)
    tol = pd.Timedelta(minutes=BAR_TOLERANCE_MIN)
    out = []
    for h in offsets:
        tgt = left.assign(target=left["available_at"] + pd.Timedelta(minutes=h)
                          ).sort_values("target")
        m = pd.merge_asof(tgt, bars, left_on="target", right_on="ts", by="symbol",
                          direction="backward", tolerance=tol)
        m = m.dropna(subset=["close"])
        m = m[m["close"] > 0]
        dev = (m["close_price"] / m["close"] - 1.0).abs()
        g = dev.groupby(m["run_ts"]).agg(["median", "size"]).reset_index()
        g["h"] = h
        out.append(g)
    t = pd.concat(out, ignore_index=True).rename(columns={"median": "mard", "size": "n"})
    t.loc[t["n"] < MIN_SYMBOLS_PER_CELL, "mard"] = _np.nan
    return t


def per_run_offsets(dev: pd.DataFrame) -> pd.DataFrame:
    """Each run's own best fine offset, plus its power ratio (spec 4.4).

    Ties on MARD go to the smaller |h|, so a flat curve reads as 0 rather than
    as a spurious offset -- and the power guard then decides whether that 0 is
    evidence or just a quiet tape.
    """
    rows = []
    fine = set(FINE_GRID)
    for run_ts, g in dev.groupby("run_ts"):
        at = g.set_index("h")["mard"]
        f = g[g["h"].isin(fine)].dropna(subset=["mard"])
        if f.empty:
            continue
        f = f.assign(absh=f["h"].abs()).sort_values(["mard", "absh"])
        best = f.iloc[0]
        sides = [v for v in (at.get(60, _np.nan), at.get(-60, _np.nan)) if v == v]
        side = min(sides) if sides else _np.nan
        # An EXACT best match (median deviation 0 -- over half the symbols hit the
        # bar close to the cent) is the MOST powered case, not an undefined one.
        # Dividing by it gave NaN and marked the best-aligned runs "no power",
        # silently discarding them. Caught by the planted-offset tests. The floor
        # is 1e-9 (0.00001 bps) so the ratio stays finite and JSON-safe.
        ratio = (side / max(best["mard"], 1e-9)) if (side == side and side > 0) else _np.nan
        rows.append({"run_ts": run_ts, "offset_min": int(best["h"]),
                     "mard_best": float(best["mard"]), "n_best": int(best["n"]),
                     "mard_0": float(at.get(0, _np.nan)),
                     "power_ratio": float(ratio) if ratio == ratio else None,
                     "powered": bool(ratio == ratio and ratio >= POWER_RATIO),
                     "at_grid_edge": abs(int(best["h"])) == max(FINE_GRID)})
    return pd.DataFrame(rows)


def cluster_bootstrap_median(per_run: pd.DataFrame, b: int = BOOT_B,
                             seed: int = BOOT_SEED) -> tuple[float, float]:
    """95% CI on the median per-run offset, resampling DATES.

    Runs within one date are not independent -- one session's tape moves them
    together -- so resampling runs would understate the interval in the
    flattering direction.
    """
    groups = [g["offset_min"].to_numpy() for _, g in per_run.groupby("date")]
    if len(groups) < 2:
        return (float("nan"), float("nan"))
    rng = _np.random.default_rng(seed)
    stats = _np.empty(b)
    for i in range(b):
        pick = rng.integers(0, len(groups), len(groups))
        stats[i] = _np.median(_np.concatenate([groups[j] for j in pick]))
    lo, hi = _np.quantile(stats, [0.025, 0.975])
    return float(lo), float(hi)


def intraday_verdict(per_run: pd.DataFrame, dev: pd.DataFrame, ci: tuple) -> dict:
    """The pre-registered decision rule, applied in this order."""
    p = per_run[per_run["powered"]] if not per_run.empty else per_run
    dates = sorted(p["date"].unique()) if not p.empty else []
    if len(dates) < 3:
        return {"state": "NO_DATA", "note": f"only {len(dates)} powered date(s)"}

    # 1. clock domain: pooled per date over the TZ cells; argmin must be h = 0
    tz_by_date, bad = {}, []
    pr = dev[dev["run_ts"].isin(p["run_ts"])].merge(p[["run_ts", "date"]], on="run_ts")
    for d, g in pr[pr["h"].isin(TZ_CELLS)].groupby("date"):
        cells = g.groupby("h")["mard"].median().dropna()
        win = int(cells.idxmin())
        tz_by_date[str(d)] = {"argmin_h": win,
                              "mard_bps": {int(k): round(float(v) * 1e4, 2)
                                           for k, v in cells.items()}}
        if win != 0:
            bad.append(str(d))
    if bad:
        return {"state": "STOP_TIMEZONE", "tz_by_date": tz_by_date,
                "note": (f"a clock-domain hypothesis beats h=0 on {bad}. Rule 9: Phase 6 "
                         f"stops; the winning offset is NOT applied.")}

    # 2. precision at the measured offset
    est = float(_np.median(p["offset_min"]))
    mard_at = float(p["mard_best"].median()) * 1e4
    if mard_at > PRECISION_BPS:
        return {"state": "STOP_AMBIGUOUS", "tz_by_date": tz_by_date,
                "note": f"median MARD at the best offset is {mard_at:.1f} bps > {PRECISION_BPS}"}

    # 3. the offset itself
    lo, hi = ci
    if -PASS_OFFSET_MIN <= lo and hi <= PASS_OFFSET_MIN:
        state, note = "PASS", (f"clock domain correct on every powered date; offset "
                               f"{est:+.1f} min, 95% CI [{lo:+.1f}, {hi:+.1f}] within one bar")
    else:
        state, note = "LAG_MEASURED", (
            f"clock domain correct on every powered date (h=0 beats every timezone "
            f"hypothesis), but close_price corresponds to available_at {est:+.1f} min, "
            f"95% CI [{lo:+.1f}, {hi:+.1f}] -- more than one bar. REPORTED, NOT APPLIED; "
            f"how Phase 6 treats it is Andrew's decision.")
    return {"state": state, "note": note, "tz_by_date": tz_by_date,
            "offset_estimate_min": est, "mard_at_best_bps": round(mard_at, 3)}


def measure_intraday(cfg: Config) -> dict:
    con = duckdb_io.connect(cfg.duckdb_path, read_only=True)
    try:
        runs = intraday_runs(con)
        universe = dense_universe(con)
        sample = runs[runs["in_session"]]
        pairs = rank_prices(con, sample["run_ts"].tolist()).merge(
            sample[["run_ts", "available_at"]], on="run_ts")
    finally:
        con.close()

    bars = load_intraday_bars(cfg)
    if bars.empty:
        raise ProviderUnavailable("no 1-minute bars in the local cache; run with --fetch")
    bar_dates = set(bars["ts"].dt.date)
    pairs = pairs[pairs["available_at"].dt.date.isin(bar_dates)]

    dev = deviation_table(pairs, bars, sorted(set(FINE_GRID) | set(TZ_CELLS)))
    per_run = per_run_offsets(dev).merge(
        sample[["run_ts", "available_at", "date"]], on="run_ts")
    powered = per_run[per_run["powered"]]
    ci = cluster_bootstrap_median(powered)
    verdict = intraday_verdict(per_run, dev, ci)
    curve = (dev[dev["run_ts"].isin(powered["run_ts"])].groupby("h")["mard"]
             .median().dropna() * 1e4)

    metas = [_json.loads(m.read_text(encoding="utf-8"))
             for m in sorted(_intraday_cache_dir(cfg).glob("*.json"))]
    served = set(bars["symbol"].unique())
    per_date = {str(d): {"symbols_with_bars": int(g["symbol"].nunique())}
                for d, g in bars.groupby(bars["ts"].dt.date)}
    for d, g in sample.groupby("date"):
        per_date.setdefault(str(d), {})["runs_in_sample"] = int(len(g))
    for d, g in per_run.groupby("date"):
        per_date.setdefault(str(d), {})["runs_powered"] = int(g["powered"].sum())

    offs = powered["offset_min"].to_numpy()
    q = (lambda x: float(_np.quantile(offs, x))) if len(offs) else (lambda x: None)
    return {
        "schema_version": 1, "order": "c-0002.1",
        "generated_at": pd.Timestamp.now(tz="America/Chicago").isoformat(timespec="seconds"),
        "verdict": verdict,
        "offset_minutes": {
            "estimate": float(_np.median(offs)) if len(offs) else None,
            "ci95": list(ci),
            "ci_method": f"cluster bootstrap over dates, B={BOOT_B}, seed={BOOT_SEED}",
            "mean": float(offs.mean()) if len(offs) else None,
            "quantiles": {"p10": q(0.1), "p25": q(0.25), "p50": q(0.5),
                          "p75": q(0.75), "p90": q(0.9)},
            "share_within_1min": float((_np.abs(offs) <= 1).mean()) if len(offs) else None,
            "sign_convention": ("negative = the price in the rank is from BEFORE "
                                "available_at; positive = after"),
        },
        "sample": {
            "anchor_time": "available_at (rule 5)",
            "dense_era_start": str(DENSE_ERA_START),
            "intraday_runs_total": int(len(runs)),
            "excluded_outside_session_window": int((~runs["in_session"]).sum()),
            "runs_measured": int(len(per_run)),
            "runs_powered": int(len(powered)),
            "runs_no_power": int((~per_run["powered"]).sum()) if len(per_run) else 0,
            "runs_at_grid_edge": int(per_run["at_grid_edge"].sum()) if len(per_run) else 0,
            "run_ts_differs_from_available_at":
                int((sample["run_ts"] != sample["available_at"]).sum()),
            "dates_powered": sorted(str(d) for d in powered["date"].unique()),
            "symbol_run_pairs": int(len(pairs)),
        },
        "coverage": {
            "universe_symbols": len(universe),
            "symbols_with_bars": len(served),
            "symbols_without_bars": sorted(set(universe) - served),
            "sessions_with_bars": sorted(str(d) for d in bar_dates),
            "per_date": dict(sorted(per_date.items())),
            "windows": [{k: m.get(k) for k in ("start", "end_exclusive", "rows",
                                                "symbols_served", "sessions")}
                        for m in metas],
            "provider_unavailable": [u for m in metas for u in m.get("unavailable", [])],
        },
        "precision": {
            "median_mard_at_best_offset_bps":
                round(float(powered["mard_best"].median()) * 1e4, 3) if len(powered) else None,
            "median_mard_at_h0_bps":
                round(float(powered["mard_0"].median()) * 1e4, 3) if len(powered) else None,
            "median_power_ratio":
                float(powered["power_ratio"].median()) if len(powered) else None,
        },
        "curve_median_mard_bps": {int(k): round(float(v), 3) for k, v in curve.items()},
        "per_run": [{"available_at": str(r.available_at), "date": str(r.date),
                     "offset_min": int(r.offset_min),
                     "mard_best_bps": round(r.mard_best * 1e4, 3),
                     "mard_0_bps": round(r.mard_0 * 1e4, 3) if r.mard_0 == r.mard_0 else None,
                     "power_ratio": r.power_ratio, "powered": bool(r.powered),
                     "n": int(r.n_best)}
                    for r in per_run.sort_values("available_at").itertuples()],
        "pre_registered": {
            "fine_grid_min": [min(FINE_GRID), max(FINE_GRID), 1],
            "tz_cells_min": list(TZ_CELLS),
            "bar_tolerance_min": BAR_TOLERANCE_MIN,
            "session_window_ct": [list(x) for x in SESSION_WINDOW_CT],
            "min_symbols_per_cell": MIN_SYMBOLS_PER_CELL, "power_ratio": POWER_RATIO,
            "precision_bps": PRECISION_BPS, "pass_offset_min": PASS_OFFSET_MIN,
            "comparison_price": "close of the last bar with OPEN time <= available_at + h",
            "prices": "RAW (auto_adjust=False), regular session only (prepost=False)",
            "verdict_order": ["NO_DATA (<3 powered dates)", "STOP_TIMEZONE",
                              "STOP_AMBIGUOUS", "PASS (CI within +-1 min)", "LAG_MEASURED"],
        },
    }


def main_intraday(argv=None) -> int:
    ap = argparse.ArgumentParser(description="intraday alignment anchor (c-0002.1)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--fetch", action="store_true", help="fetch/persist bars first")
    ap.add_argument("--out", default="alignment_anchor_intraday.json")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    cfg = Config.load(a.config)
    if a.fetch:
        con = duckdb_io.connect(cfg.duckdb_path, read_only=True)
        syms = dense_universe(con)
        con.close()
        fetch_intraday_bars(cfg, syms, DENSE_ERA_START, date.today())
    res = measure_intraday(cfg)
    out = cfg.results_dir / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(res, indent=2, default=str), encoding="utf-8")
    v, o = res["verdict"], res["offset_minutes"]
    print(f"INTRADAY ANCHOR  {v['state']}")
    print(f"  offset {o['estimate']} min, 95% CI {o['ci95']}  ({o['ci_method']})")
    print(f"  {v.get('note')}")
    print(f"  wrote {out}")
    return 0 if v["state"] in ("PASS", "LAG_MEASURED") else 1


if __name__ == "__main__":
    if "--intraday" in sys.argv:
        sys.argv.remove("--intraday")
        sys.exit(main_intraday())
    sys.exit(main())
