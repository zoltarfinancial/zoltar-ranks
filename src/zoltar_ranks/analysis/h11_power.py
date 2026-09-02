"""H11's power calculation, recomputed on the corrected evening-retrain population.

`c-0001.1`. The original figure (MDE 0.157%/run at n=63, written 2026-09-01
21:41) predates the `daily_ranks` backfill by two hours, so it was computed on
~64 `nightly` runs when the archive held essentially no placeholders. It is not
contaminated by the placeholder pointer -- it is simply **stale**, because the
backfill roughly doubled the population and MDE scales as 1/sqrt(n).

Population: `evening_retrains`, which since 2026-09-02 excludes
`run_kind='placeholder'` (a rewritten latest-pointer, FINDINGS F4) and the four
early-mode runs before 18:00 (the day's final intraday re-score, not a retrain).

Three rules this obeys, and they are the whole reason it is a script and not a
number typed into a table:

* **Rule 5** -- pairing is ordered by `available_at`, never `run_ts`. The evening
  retrain is forward-stamped, so ordering by `run_ts` would pair a run with an
  "exit" that was actually published before it.
* **Rule 3** -- the exit is the first observation strictly AFTER the entry's
  `available_at`. No same-bar execution; a zero-latency fill is a bug.
* **Rule 6** -- the MDE carries a bootstrap interval. A power calculation quoted
  as a bare number invites exactly the false precision the plan exists to stop.

The bootstrap here is deliberately simple and belongs in `analysis/stats.py`
once Phase 4 exists. It is reported both iid and moving-block, and the WIDER
interval is the one quoted -- overnight gaps on an overlapping basket are not
independent, and pretending otherwise narrows the interval in the flattering
direction.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

import numpy as np
import pandas as pd

from zoltar_ranks.config import Config
from zoltar_ranks.db import duckdb_io

log = logging.getLogger("h11_power")

#: 80% power, two-sided alpha = 0.05.
Z_ALPHA, Z_BETA = 1.959963985, 0.841621234
MDE_Z = Z_ALPHA + Z_BETA

BOOTSTRAP_DRAWS = 10_000
BLOCK_LEN = 5            # ~a trading week of consecutive evening runs
WINSOR_Q = 0.01

#: RULE 3's latency, and it is not a tuning knob. 27 of the 138 evening runs are
#: forward-stamped with `available_at = committed_at`, and the next observation
#: is the SAME evening's other build, pushed 13-15 seconds later in the same
#: commit. Those pairs return exactly 0.000%: it is the same bar, so pairing them
#: is same-bar execution. They also bias in the FLATTERING direction -- 27
#: zero-variance observations shrink the sd, shrink the MDE, and make the study
#: look better powered than it is. The p25 hold is 11.9 h, so 1 h separates the
#: artifact from every real overnight gap without touching one.
MIN_HOLD_HOURS = 1.0

#: H11's estimand is the OVERNIGHT gap. In the sparse era a symbol's next
#: observation can be months away: two runs here mark 66 and 105 days later, and
#: those two alone move the sd from 0.32% to 0.80%. That is not an overnight gap,
#: it is a quarter. The hold distribution breaks cleanly -- p98 is 15.6 h and p99
#: is 1013 h -- so any threshold in the 24 h-336 h gap selects the same 136 runs.
MAX_HOLD_HOURS = 96.0

PAIRS_SQL = """
WITH ev AS (
    SELECT DISTINCT run_ts, available_at
    FROM evening_retrains
    WHERE risk_bucket = ?
),
ranked AS (
    SELECT e.run_ts, e.available_at, r.symbol, r.close_price AS entry_px,
           row_number() OVER (PARTITION BY e.run_ts ORDER BY r.score DESC) AS rk
    FROM ev e
    JOIN ranks r ON r.run_ts = e.run_ts AND r.risk_bucket = ?
    WHERE r.score IS NOT NULL AND r.close_price IS NOT NULL AND r.close_price > 0
),
top_n AS (SELECT * FROM ranked WHERE rk <= ?),
obs AS (
    SELECT symbol, available_at, close_price
    FROM ranks_pit
    WHERE risk_bucket = ? AND close_price IS NOT NULL AND close_price > 0
)
SELECT t.run_ts, t.available_at, t.symbol, t.rk, t.entry_px,
       o.close_price AS exit_px, o.available_at AS exit_at
FROM top_n t
ASOF LEFT JOIN obs o
  ON t.symbol = o.symbol AND t.available_at < o.available_at
ORDER BY t.available_at, t.rk
"""


def load_pairs(con, risk_bucket: str, top_n: int) -> pd.DataFrame:
    """One row per (evening run, selected symbol) with its entry and exit price.

    The ASOF join takes the SMALLEST `available_at` strictly greater than the
    entry's -- the first moment the position could actually have been marked.
    """
    df = con.execute(PAIRS_SQL, [risk_bucket, risk_bucket, top_n, risk_bucket]).df()
    df["available_at"] = pd.to_datetime(df["available_at"])
    df["exit_at"] = pd.to_datetime(df["exit_at"])
    return df


def per_run_returns(pairs: pd.DataFrame, top_n: int,
                    min_hold_hours: float = MIN_HOLD_HOURS,
                    max_hold_hours: float = MAX_HOLD_HOURS) -> pd.DataFrame:
    """Equal-weight basket return per evening run, keeping only complete baskets.

    A run missing a leg is DROPPED rather than averaged over what survived:
    averaging the available legs silently changes the estimand from "the top-5
    basket" to "whichever of the top 5 had a successor", which is a
    survivorship-flavoured statistic.
    """
    priced = pairs.dropna(subset=["exit_px"]).copy()
    priced["ret"] = priced["exit_px"] / priced["entry_px"] - 1.0
    grp = priced.groupby(["run_ts", "available_at"], as_index=False).agg(
        legs=("ret", "size"), ret=("ret", "mean"),
        exit_at=("exit_at", "max"))
    complete = grp[grp["legs"] == top_n].sort_values("available_at").reset_index(drop=True)
    complete["hold_hours"] = (
        (complete["exit_at"] - complete["available_at"]).dt.total_seconds() / 3600)
    complete["excluded"] = np.where(
        complete["hold_hours"] < min_hold_hours, "same_bar",
        np.where(complete["hold_hours"] > max_hold_hours, "stale_mark", ""))
    return complete


def usable(per_run: pd.DataFrame) -> pd.DataFrame:
    return per_run[per_run["excluded"] == ""].reset_index(drop=True)


def _mde(sd: float, n: int) -> float:
    return MDE_Z * sd / np.sqrt(n) if n else float("nan")


def _boot_iid(x: np.ndarray, draws: int, rng) -> np.ndarray:
    idx = rng.integers(0, len(x), size=(draws, len(x)))
    s = x[idx]
    return MDE_Z * s.std(axis=1, ddof=1) / np.sqrt(len(x))


def _boot_block(x: np.ndarray, draws: int, block: int, rng) -> np.ndarray:
    """Moving-block bootstrap: preserves local serial dependence.

    Overnight gaps on an overlapping basket are not independent draws. An iid
    bootstrap under-states the interval, and it under-states it in the direction
    that makes the study look better powered than it is.
    """
    n = len(x)
    if n <= block:
        return _boot_iid(x, draws, rng)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(draws, n_blocks))
    out = np.empty(draws)
    offs = np.arange(block)
    for i in range(draws):
        s = x[(starts[i][:, None] + offs).ravel()][:n]
        out[i] = MDE_Z * s.std(ddof=1) / np.sqrt(n)
    return out


def compute(cfg: Config, risk_bucket: str = "low", top_n: int = 5,
            draws: int = BOOTSTRAP_DRAWS, seed: int = 20260902) -> dict:
    con = duckdb_io.connect(cfg.duckdb_path, read_only=True)
    try:
        pairs = load_pairs(con, risk_bucket, top_n)
        runs_total = con.execute(
            "SELECT count(DISTINCT run_ts) FROM evening_retrains").fetchone()[0]
    finally:
        con.close()

    all_runs = per_run_returns(pairs, top_n)
    per_run = usable(all_runs)
    dropped = all_runs["excluded"].value_counts().to_dict()
    x = per_run["ret"].to_numpy(dtype=float)
    n = len(x)
    if n < 3:
        raise RuntimeError(f"only {n} usable evening run(s); cannot compute an MDE")

    sd = float(np.std(x, ddof=1))
    lo, hi = np.quantile(x, [WINSOR_Q, 1 - WINSOR_Q])
    sd_w = float(np.std(np.clip(x, lo, hi), ddof=1))

    rng = np.random.default_rng(seed)
    b_iid = _boot_iid(x, draws, rng)
    b_blk = _boot_block(x, draws, BLOCK_LEN, rng)
    ci_iid = [float(v) for v in np.quantile(b_iid, [0.025, 0.975])]
    ci_blk = [float(v) for v in np.quantile(b_blk, [0.025, 0.975])]
    # Quote the wider interval. See _boot_block.
    quoted = ci_blk if (ci_blk[1] - ci_blk[0]) >= (ci_iid[1] - ci_iid[0]) else ci_iid

    mde = _mde(sd, n)
    span = per_run["available_at"].max() - per_run["available_at"].min()
    window_days = float(span.total_seconds() / 86400)
    runs_per_year = (n / window_days * 365.25) if window_days > 0 else float("nan")
    return {
        "risk_bucket": risk_bucket, "top_n": top_n,
        "evening_runs_in_view": int(runs_total),
        "runs_with_a_complete_basket": int(len(all_runs)),
        "runs_usable": n,
        "dropped_same_bar": int(dropped.get("same_bar", 0)),
        "dropped_stale_mark": int(dropped.get("stale_mark", 0)),
        "min_hold_hours": MIN_HOLD_HOURS, "max_hold_hours": MAX_HOLD_HOURS,
        "mean_ret_per_run": float(np.mean(x)),
        "sd_per_run": sd, "sd_winsorized": sd_w,
        "mde_per_run": mde,
        "mde_winsorized": _mde(sd_w, n),
        "mde_ci95": quoted,
        "mde_ci95_iid": ci_iid,
        "mde_ci95_block": ci_blk,
        # NOT "annualized". The original H11 note said "~9.9%/yr over 63 runs",
        # which silently treated the sample as a year because it roughly was one.
        # This window is 182 days, so the same arithmetic would overstate a year
        # by ~2x. Both figures are reported with the window that produced them.
        "mde_cumulative_over_window": mde * n,
        "mde_cumulative_over_window_ci95": [quoted[0] * n, quoted[1] * n],
        "window_days": window_days,
        "runs_per_year_observed": runs_per_year,
        "mde_per_year_if_persistent": mde * runs_per_year,
        "median_hold_hours": float(per_run["hold_hours"].median()),
        "first_run": str(per_run["available_at"].min()),
        "last_run": str(per_run["available_at"].max()),
        "bootstrap_draws": draws, "block_len": BLOCK_LEN, "seed": seed,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=None)
    ap.add_argument("--risk-bucket", default=None)
    ap.add_argument("--top-n", type=int, default=None)
    ap.add_argument("--json", action="store_true", help="print the raw dict")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = Config.load(args.config)
    res = compute(cfg,
                  risk_bucket=args.risk_bucket or cfg.baseline_risk_bucket,
                  top_n=args.top_n or cfg.baseline_top_x)
    if args.json:
        print(json.dumps(res, indent=2))
        return 0

    pct = lambda v: f"{100 * v:.4f}%"
    print(f"H11 power, evening retrains, top-{res['top_n']} "
          f"{res['risk_bucket']}-risk, equal weight")
    print(f"  runs in evening_retrains        {res['evening_runs_in_view']}")
    print(f"  complete baskets                {res['runs_with_a_complete_basket']}")
    print(f"  dropped, same-bar (<{res['min_hold_hours']}h)       "
          f"{res['dropped_same_bar']}   [RULE 3]")
    print(f"  dropped, stale mark (>{res['max_hold_hours']}h)   "
          f"{res['dropped_stale_mark']}")
    print(f"  USABLE                          {res['runs_usable']}")
    print(f"  window                          {res['first_run']} .. {res['last_run']}")
    print(f"  median hold to first mark       {res['median_hold_hours']:.2f} h")
    print(f"  mean return / run               {pct(res['mean_ret_per_run'])}")
    print(f"  sd / run                        {pct(res['sd_per_run'])} "
          f"(winsorized {pct(res['sd_winsorized'])})")
    print(f"  MDE / run  (80% power)          {pct(res['mde_per_run'])}  "
          f"95% CI [{pct(res['mde_ci95'][0])}, {pct(res['mde_ci95'][1])}]")
    print(f"  MDE winsorized                  {pct(res['mde_winsorized'])}")
    print(f"  window span                     {res['window_days']:.0f} days, "
          f"{res['runs_per_year_observed']:.0f} runs/yr at the observed rate")
    print(f"  MDE cumulative over the window  "
          f"{pct(res['mde_cumulative_over_window'])}  95% CI "
          f"[{pct(res['mde_cumulative_over_window_ci95'][0])}, "
          f"{pct(res['mde_cumulative_over_window_ci95'][1])}]")
    print(f"  MDE per year if it persists     "
          f"{pct(res['mde_per_year_if_persistent'])}  "
          f"(per-run MDE x observed runs/yr; NOT a measured annual effect)")
    print(f"  bootstrap                       iid "
          f"[{pct(res['mde_ci95_iid'][0])}, {pct(res['mde_ci95_iid'][1])}] | "
          f"block-{res['block_len']} "
          f"[{pct(res['mde_ci95_block'][0])}, {pct(res['mde_ci95_block'][1])}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
