# Upstream reconnaissance — verified findings

Everything below was measured against `github.com/apod-1/ZoltarFinancial` on
**2026-09-01**, not assumed. Re-run `tests/test_harvest.py -m network` to
re-verify; if any of these break, stop and re-plan before trusting a backtest.

## F1. The rank archive is genuinely point-in-time (load-bearing)

`production/low_risk_PROD_latest.pkl` at HEAD vs. the same file at commit
`3fbff58` (2026-08-18):

| check | result |
|---|---|
| overlapping (Date, Symbol) rows | 185,880 |
| `Score` identical | **100.0%** |
| `Close_Price` identical | **100.0%** |
| max abs score difference | 0.0 |

Scores are **never restated**. This is what makes a backtest on the archive
honest — the row for 2026-03-14 is what the model actually said on 2026-03-14,
not a re-score with today's model.

⚠️ One run timestamp present in the old blob was missing from HEAD. Never treat
a single snapshot as complete; the archive is the **union** of snapshots.

## F2. Two feeds with very different retention

| file | rows | run timestamps | reach | behaviour |
|---|---|---|---|---|
| `low/high_risk_PROD_latest.pkl` | 201k | 170 | 2026-01-01 → 2026-09-02 | append-only, ~1 snapshot/day |
| `all_low/high_risk_PROD_latest.pkl` | 233k | 200 | 2026-07-01 → 2026-09-02 | **rolling buffer, hard cap 200** |

The `all_*` buffer had reached back to **2026-05-22** on 2026-08-18 and only to
2026-07-01 by 2026-09-01 — **137 intraday run timestamps destroyed in two weeks.**

**This is a running clock.** Every day without a harvest permanently loses
intraday snapshots, which are exactly the observations the fill-timing study
needs. Run the backfill first, then schedule the daily harvest.

The two feeds agree exactly where they overlap (46 shared run timestamps,
53,731 rows, 100% identical `Score` and `Close_Price`), so a plain union on
`(run_ts, symbol, risk_bucket)` is safe.

## F3. Git history reaches further back than HEAD does

The blob at the oldest commit carries data HEAD has already dropped. The
coverage walk in `harvest_ranks.py` recovers:

| feed | HEAD alone | after coverage walk | gain |
|---|---|---|---|
| daily | 170 run ts, from 2026-01-01 | **234 run ts, from 2025-10-01** | +3 months |
| all (intraday) | 200 run ts, from 2026-07-01 | **376 run ts, from 2026-05-16** | +6 weeks |

Cost: **2 blob fetches per file** (~50 MB total), not 68 (~6 GB). Upstream git
history itself begins 2026-07-20 ("Fresh start" commit) — anything the blobs at
that commit do not contain is gone for good.

## F4. Run timestamps encode the run type

| pattern | meaning |
|---|---|
| `HH:MM:SS` between 01:00–09:00 CDT | morning model build (the primary product) |
| between 09:00–15:30 CDT | live intraday re-score, ~17/day, every ~30 min |
| after 17:00 CDT | nightly placeholder build (moving-window refresh) |
| exactly `00:00:00`, dated the **next** day | nightly placeholder stamped forward |

The `00:00:00` stamps are a trap: they look like a midnight run you could have
traded on. They are the previous evening's build. `classify_run()` labels them
`placeholder` — **never** use them as a tradable decision point without shifting
them back to their true availability time.

## F5. The baseline engine has three built-in execution biases

From `app/Strategy_Play.py::update_strategy` (the live zoltar.streamlit.app path):

1. **Same-bar execution.** Buys fill at `stock['Close_Price']` — the exact price
   the rank was computed from. In reality that price is already gone by the time
   the 5:30–8:30am build finishes. This alone can manufacture most of the edge.
2. **Exits are only checked at snapshot cadence, against snapshot close.** The
   -1% / +2% rules are evaluated when a rank file lands, not against intraday
   OHLC. A stock that touched -1% at 10:15 and closed +3% is recorded as a
   winner. Real fills would have stopped out.
3. **Cash drift.** `cash_per_stock = available_cash / num_stocks_to_buy` is
   computed before symbols already held are skipped, so the book is chronically
   under-invested and position sizes are inconsistent. This biases the return
   series downward and adds noise.

Existing levers already in the code, worth treating as hypotheses rather than
constants: `omit_first` (skip the top N), `score_cutoff`, `bottom_z_percent`
panic-sell, `follow_days_to_hold` (use `Score_HoldPeriod` as a max hold).

## F6. Other upstream artifacts

| file | shape | content |
|---|---|---|
| `er_for_last_date{,_live}.pkl` | 16,310 × 4 | forward expected return, **14 horizons** per symbol, one as-of date per file. Overwritten daily. Only **33 commits** touch the daily variant, so git yields ~30 as-of dates — the ER archive is thin and gets deeper only from here forward. |
| `combined_SHAP_summary_{Large,Mid,Small}_latest.pkl` | 26×160, 26×180, 33×171 | WOE-binned feature attributions. **Indexed by SYMBOL** (the segment's top-scoring names), one object column `Feature Category`, no internal timestamp — `snapshot_ts` comes from the commit. 185–241 commits available per segment. The unbiasing lever. |
| `fundamentals_df_latest.pkl` | 1,124 × 23 | PE, PB, market cap, float, sector/industry, 52w range |
| `ratings_detail_df_latest.pkl` | 4,230 × 4 | analyst rating text, `RatingPublishedAt` — usable as an event feature |
| `*_rankings_latest.pkl` | 108k × 10 | current build's train/validate/validate_oot panel. **Contains in-sample rows — never backtest on this.** Diagnostics only. |

`low` and `high` risk scores correlate 0.76 and are never identical — they are
genuinely two models, not one relabelled.

## F7. Data-quality flags to resolve before trusting returns

- `Cap_Size` looks like a **model segment label, not literal market cap**
  (LITE at $870 and BE at $210 are both tagged `Small`). Confirm the definition
  before using it as a control variable.
- `Close_Price` for `SFTBY` reads 28.68 on 2026-06-01 and 15.88 on 2026-09-01.
  Either a split or an unadjusted-price inconsistency. Every return computed
  from `Close_Price` is suspect until corporate actions are joined in — hence
  the `corporate_actions` table in the schema.
- Symbol universe drifts (1,124 to 1,214 symbols across files). Survivorship
  matters: a symbol that disappears may have been delisted, not merely dropped.
