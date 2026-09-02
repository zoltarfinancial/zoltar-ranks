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

⚠️ **The MECHANISM is UNRESOLVED (2026-09-01). Do not assert either reading.**
The observed density — ~1 run/day before 2026-08-19, 9–16/day after — is equally
consistent with:

- **(a) rolling-buffer collapse.** `all_*` retains each day's live runs only
  within a recent window and collapses older days to one survivor. If so the
  sparse era under-represents decision points that really existed, *and* the
  survivor is systematically the day's best-informed run — an **upward bias** on
  any backtest spanning it.
- **(b) a genuine cadence change at 2026-08-19.** Andrew simply began running
  many more intraday sessions then. If so the sparse era is faithful and there is
  no bias.

Evidence currently favours neither. Notably the same 1/day → 15/day break appears
in the **source `*_rankings_*` filenames** (149 days at median 1/day before
2026-08-19; 9 days at median 15/day after), which is what (b) predicts — but it
is not decisive, because the filename census only covers what upstream kept.

**A local archive Andrew holds will settle this definitively**, and no amount of
blob-reading can settle it better than the source. Phase 6's valid date range is
**deliberately undecided** until then.

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

**Session boundaries, MEASURED 2026-09-01** from 513 labelled build stamps in
`daily_ranks/*_rankings_*` FILENAMES (filenames only — no blob was read, so Rule 4
is untouched). These supersede the earlier guessed cutoffs:

| upstream label | n | time-of-day min / median / max |
|---|---|---|
| PREMARKET | 10 | 08:14:43 / 08:34:00 / 08:50:52 |
| AFTEROPEN | 54 | 08:57:57 / 09:30:23 / 09:59:38 |
| MORNING | 100 | 10:00:38 / 10:59:15 / 11:58:18 |
| AFTERNOON | 146 | 12:01:25 / 13:14:24 / 14:34:00 |
| PRECLOSE | 50 | 14:37:39 / 15:00:12 / 15:24:56 |
| AFTERCLOSE | 153 | **15:27:35** / 20:11:39 / 21:18:33 |

⚠️ **The old "after 17:00 CDT = nightly" rule was wrong.** AFTERCLOSE is observed
from **15:27:35**, and PRECLOSE ends at 15:24:56 — the true boundary is **~15:26**.
`classify_run()`'s 15:30 cutoff was *closer to the truth than the documented rule*:
the code was right and the doc was wrong.

**`classify_run()` accuracy: 5 disagreements in 513 stamps (1.0%).** Known,
accepted, and all within ±4 minutes of a boundary. **Do not "fix" these** — the
cost of re-labelling every downstream decision point exceeds the benefit:

| observed | classify_run says | n | error |
|---|---|---|---|
| AFTEROPEN 08:57–08:59 | `morning` | 2 | cutoff 9.0 is ~3 min late |
| AFTERCLOSE 15:27–15:31 | `intraday` | 3 | cutoff 15.5 is ~4 min late |

### AFTERCLOSE is two events, not one (measured 2026-09-01)

The 153 AFTERCLOSE stamps are **strongly bimodal** — 2-component vs 1-component
Gaussian BIC favours two by **ΔBIC = 179.4**, with a 1.96h dead zone between
17:04 and 19:00 (zero observations 16:00–17:00):

| mode | n | range | mean | reading |
|---|---|---|---|---|
| early | 7 | 15:27–17:04 | 15.72h | quick post-close re-score |
| late | **146** | 19:00–21:18 | 20.16h | the **full model retrain** |

Only the late mode is the model-vintage event **H12b** tests. The early mode is
4.6% of AFTERCLOSE and sporadic (7 events, 2026-02-25 → 2026-08-13), so the
contamination is small but real, and it would be misattributed as a retrain.
**Proposed, not implemented:** split `nightly` into `postclose_rescore`
(15:26 ≤ t < 18:00) and `nightly_retrain` (t ≥ 18:00), split point 18:01 from the
largest observed gap.

### Nightly uses TWO stamping conventions

Both push roughly +24h, and which one applies varies run to run:

- **Date-only forward:** filename is honest, `Date` is +24h.
  `low_risk_PROD_20260811_201524.pkl` → newest `Date` `2026-08-12 20:13:03`.
- **Both forward:** filename *and* `Date` are +24h.
  `all_low_risk_PROD_20260902_193449.pkl` was committed `2026-09-01 19:36:25`.

This is exactly why `available_at` takes **`min(filename_build_stamp,
committed_at)`** — either source alone is wrong for one of the two conventions.

**Corrected 2026-09-01 (Andrew).** An earlier version of this section called the
nightly build a "placeholder" / "moving-window refresh". That was wrong and the
error was load-bearing, so it is recorded rather than quietly edited.

The nightly build is a **full model training routine** that Andrew runs after the
close. It can produce **materially different results from that morning's model** —
it is a different model, not a restamped copy of the morning one. The
next-day `000000` suffix is a **deliberate naming convention Andrew applies**, not
upstream flakiness or a clock bug.

Two consequences:

1. **`stamp_is_forward` (`run_ts > committed_at`) is a fully reliable
   nightly-retrain detector.** It is not a data-quality wart to be tolerated; it
   is the cleanest signal available for "this row came from the retrained model".
2. **Morning-vs-nightly is a MODEL comparison, not a vintage comparison.**
   H3 ("the latest intraday re-score beats the morning score") does *not* cover
   it: H3 compares the same model scored at different times, whereas
   morning-vs-nightly compares two separately trained models. That gap is why
   H12 exists.

The rows are tradable — see rule 5 and H11. Nothing is excluded on the basis of
`run_kind`.

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

- ✅ **RESOLVED 2026-09-01 (Andrew): `Cap_Size` is a model segment label, not
  literal market cap** (LITE at $870 and BE at $210 are both tagged `Small`).
  Use it to join the SHAP segments. It is **not** valid as a size control
  variable in Phase 5 — use `fundamentals_df_latest.pkl` market cap for that.
- `Close_Price` for `SFTBY` reads 28.68 on 2026-06-01 and 15.88 on 2026-09-01.
  Either a split or an unadjusted-price inconsistency. Every return computed
  from `Close_Price` is suspect until corporate actions are joined in — hence
  the `corporate_actions` table in the schema.
- Symbol universe drifts (1,124 to 1,214 symbols across files). Survivorship
  matters: a symbol that disappears may have been delisted, not merely dropped.
