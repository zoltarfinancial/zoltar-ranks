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
from zoltar_ranks.sources.prices import YFinanceProvider

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


if __name__ == "__main__":
    sys.exit(main())
