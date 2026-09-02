# The alignment anchor — Phase 2's first measurement

**Status: SPEC. Nothing here is built.** Do not implement before Andrew signs off
on the tolerance table in §6.

## 0. Why this exists, and why it comes before any provider method

Phase 2 joins two clock domains for the first time.

| side | clock |
|---|---|
| `ranks.run_ts`, `ranks_pit.available_at` | tz-naive **America/Chicago** wall clock, as upstream writes it |
| yfinance daily | a **date**, on the exchange's ET trade-date grid |
| yfinance intraday | tz-**aware** US/Eastern, converted at the `validate()` boundary |
| Robinhood / Alpaca | **UTC** (`robin_stocks` returns `begins_at` with a `Z`) |

The timing study measures effects at 15–30 minute granularity against a **0.157%
MDE**. A one-hour misalignment does not degrade that result — it **manufactures**
one. Anchoring a "buy at T+30min" arm to a price actually observed at T+90min
imports 60 minutes of realised drift into the estimate, and drift over an hour is
one to two orders of magnitude larger than the MDE. The result would be large,
clean, and entirely spurious.

And it would pass every test in the repo. Both sides are internally consistent:
the archive is self-consistent in CT, the provider frame is self-consistent in
whatever it returns, and no assertion currently compares them. This is the same
silent-failure class as the exit-0-on-incomplete defect and the `-Once` scheduler
trigger — the system reports success while producing nothing true.

**So the anchor is measured before any return is computed, and a failed anchor
stops the phase (rule 9). Do not reconcile. Do not widen a tolerance.**

## 1. What "the anchor" actually is

Three questions, each answered by measurement, not assumption:

| | question | damage if wrong |
|---|---|---|
| **(a) DAILY** | Which trade date's close does `ranks.close_price` carry on a morning build? | Every forward return in the repo is offset by one session. |
| **(b) INTRADAY** | Does an intraday `run_ts`, read as America/Chicago, land on the minute bar the build actually saw? | The timing study's independent variable is shifted by a constant — exactly how a spurious optimum appears. |
| **(c) ADJUSTMENT** | Is `ranks.close_price` split-adjusted? (FINDINGS §F7) | A split reads as a −50% return. |

These are **measurements, not hypotheses**: none predicts a return, so none
consumes the FDR denominator in `docs/HYPOTHESES.md` (rule 7). They belong in
`docs/FINDINGS.md` alongside F1–F4. This is the one rule-7 judgement call in the
spec — say so if you read it differently.

## 2. Prerequisites that must land first

### 2.1 The trading calendar comes FIRST, not last

`test_reconcile_trading_calendar` is listed fourth in PLAN §2c. It must run
**first**, because (a)'s whole question is "same trade date or *prior* trade
date", and "prior" is undefined without a holiday-aware session grid. A naive
`run_ts.date() - 1 day` is wrong on every Monday and every post-holiday session —
a failure that presents as ~25% noise rather than as an error, which is the worst
possible shape for a bug to have.

Build the grid from the provider's own returned dates for a liquid, always-traded
reference basket (`SPY`, `AAPL`, `MSFT`), intersected across the three. Do not
hardcode a holiday list, and do not take `pandas_market_calendars` as truth
without cross-checking it against returned bars — a calendar package and the
provider disagreeing is itself the finding.

**Required order: calendar → (a) → (b), with (c) depending on (a).**

### 2.2 The anchor must run on UNADJUSTED prices

`YFinanceProvider.fetch_daily` hardcodes `auto_adjust=True` and stamps
`adjusted=True`. That is right for computing returns and **wrong for the anchor**:
adjusted history is restated backwards for every dividend, so a 2026-03 close
pulled today differs from what the model saw by the sum of subsequent dividends —
a few percent for a dividend payer. The anchor would then fail for a reason with
nothing to do with the clock, and (c) could not distinguish "unadjusted" from
"misaligned".

Required change, which is **not** a column-contract change (the `adjusted` flag
exists precisely for this):

1. `YFinanceProvider.__init__(cache_dir, adjusted: bool = True)`, setting
   `self.returns_adjusted` per instance and passing it to `auto_adjust` and to the
   `adjusted` column.
2. **`PriceProvider._cache_path` must fold `self.returns_adjusted` into the hash
   key.** Without it, a raw request is served adjusted bars cached from an earlier
   adjusted request under the same provider name — silently, and in exactly the
   direction that hides the answer to (c).

### 2.3 Symbol normalization is a required output, not a footnote

yfinance writes `BRK-B`; upstream may write `BRK.B` or `BRK/B`. ADRs and class
shares are where this bites, and F7's known case (`SFTBY`) is an ADR. A symbol the
provider cannot map is **`unknown`**, reported separately. It is never counted as
a mismatch — doing so lets a ticker-mapping bug masquerade as an adjustment
finding.

### 2.4 Yahoo is rate-limiting right now

A bare request to `query1.finance.yahoo.com` returned **HTTP 429** on 2026-09-02.
(Good news in one respect: a 429 and not the AVG TLS interception error of
2026-09-01 — the certificate block is gone.) The anchor needs backoff-and-retry
around every `yf.download`, and a 429 must raise `ProviderUnavailable` rather than
return an empty frame, for the same reason `fetch_actions` already does.

## 3. (a) The daily anchor

### 3.1 Sample — fixed in advance, so it cannot be tuned

- **Runs:** all `run_kind='morning'` runs on the dates below, from the `daily`
  feed (append-only; not the rolling `all` feed).
- **Dates: 20 morning runs**, chosen by rule, not by result:
  - the **15 most recent** morning runs — over days, dividend drift ≈ 0, so this
    subsample isolates the clock question and nothing else;
  - **2025-11-03**, first session after US DST ends 2025-11-01;
  - **2026-03-09**, first session after US DST begins 2026-03-08;
  - the first sessions after Thanksgiving 2025, Christmas 2025, and Independence
    Day 2026 — the short and holiday-adjacent sessions.
- **Symbols:** every symbol in that run, both risk buckets, deduplicated. No
  filtering by liquidity or by result — the universe is what the run contained
  (rule 8).

The two DST dates are the highest-value cells in the table. A tz bug that
manifests only across a DST boundary is precisely the one that survives an eyeball
of recent data.

### 3.2 Split the morning class at the opening bell

`classify_run` labels `morning` as `hour < 9.0` CT. The US open is **08:30 CT**.
So the morning class **straddles the open**: PREMARKET runs (08:14–08:50, mostly
pre-open) and early AFTEROPEN runs (08:57–08:59, ~28 min *after* the open) are
both `morning`. Report the subsets separately:

- `pre_open`: time-of-day < 08:30
- `post_open`: 08:30 ≤ time-of-day < 09:00

If they answer differently, `close_price` is a live quote for one subset and a
prior close for the other, `morning_ranks` is not a homogeneous class, and that is
a finding in its own right.

### 3.3 Candidates compared

For each (run, symbol), compute `d = ranks.close_price / candidate − 1` against:

| candidate | what a match means |
|---|---|
| **close(T−1)** | prior trade date's close — the assumed answer |
| **close(T)** | same trade date's close — **a look-ahead** |
| close(T−2) | an off-by-two or stale-file mode |
| open(T) | the build used the opening print |

`T` is the trade date of `run_ts` on the §2.1 session grid; `T−1` and `T−2` are
prior **sessions**, not prior calendar days.

The primary statistic is the **match rate at each tolerance, per date, never
pooled**. A pooled 97% can be 19 perfect dates and one catastrophic one — and a
DST bug affects exactly one date. Each rate carries a **Wilson 95% interval**
(rule 6).

### 3.4 Tolerances, and why

Report three; state the verdict on the tightest.

| tolerance | justification |
|---|---|
| **5 bps (0.05%)** — the decision tolerance | If the build read the price the exchange printed, the only remaining differences are float round-tripping through pickle and vendor differences on the consolidated close. Both are well under 5 bps. |
| 50 bps | PLAN §2c's reconciliation threshold, reported for continuity. |
| 200 bps | Diagnostic only — shows how fast the rate saturates. |

**Why 5 bps and not PLAN's 50.** The reconciliation test asks *is this the right
price*; the anchor asks *is this the right observation*. At 50 bps roughly a third
of symbol-days have |daily return| < 0.5%, so close(T−1) and close(T) both "match"
and the test cannot separate them. At 5 bps, for a 1.5%-daily-vol name, a
coincidental match runs about 3%. The two hypotheses then separate by ~95 points
instead of ~60. **A tolerance wide enough to absorb the error you are looking for
is worthless** — that is the whole reason this document exists.

### 3.5 Decision rule

Let `p_prior` and `p_same` be the 5 bps match rates.

- **PASS** — `p_prior ≥ 0.95` on **every** date and `p_prior − p_same ≥ 0.20`.
  Anchor is close(T−1). Record in FINDINGS and continue.
- **STOP / LOOK-AHEAD** — `p_same > p_prior`. `close_price` on a morning build
  encodes a price the build could not have known. That invalidates Phases 3 and 6
  wholesale, not just Phase 2. Report to Andrew; build nothing on it.
- **STOP / AMBIGUOUS** — neither candidate clears 0.95. Report the per-date table
  and the nearest-field diagnostic (which of the four candidates minimises median
  |d|, per date). Do not widen the tolerance to manufacture a pass.
- **STOP / SPLIT VERDICT** — `pre_open` and `post_open` disagree. Report; do not
  average them.

## 4. (b) The intraday anchor

### 4.1 (b0) A precondition that costs one SQL query

Before any provider call: **does `close_price` change within a day across intraday
runs?**

```sql
SELECT run_ts::DATE AS d, symbol, count(DISTINCT close_price) AS n_prices
FROM ranks WHERE run_kind = 'intraday' AND run_ts >= '2026-08-19'
GROUP BY 1, 2;
```

If `n_prices` is 1 for most (date, symbol) pairs, intraday `close_price` is **not
a live price** — it is the prior daily close carried through every re-score. Then
(b) is not a well-posed question, H3 and H12a change meaning, and the intraday
anchor must instead be established against `prices_intraday` on the execution side
only. **Run this first; it can make the rest of §4 moot.**

### 4.2 Sample

- All `run_kind='intraday'` runs from **2026-08-19** forward (the dense era) —
  currently ~9 dense days at ~15 runs/day.
- **This is time-critical.** yfinance serves 1-minute bars for roughly the last
  **30 days**. 2026-08-19 is 14 days back today. Every day of delay erodes the only
  window in which this can be measured at 1-minute resolution, and once 2026-08-19
  falls outside that window it cannot be measured at all until the offline SSD
  archive lands (F2). **Do (b) before (c).**
- Symbols: as §3.1 — the run's own universe.

### 4.3 The offset search

`INTRADAY_COLUMNS.ts` is the bar **open** time. For a candidate offset `h`, the
comparison price is *the close of the last bar whose open time ≤ run_ts + h*.

Search `h ∈ {0, ±1min, ±30min, ±1h, ±2h, ±5h, ±6h}`. The ±1h cell is the
ET-vs-CT error this whole exercise is about; ±5h/±6h catch a UTC-vs-CT error with
and without DST; ±2h catches a double conversion; ±1min catches a bar-labelling
(open-vs-close) convention error, which is benign but must be named rather than
absorbed.

**Winner** = the `h` minimising **MARD** (median absolute relative deviation)
across all (run, symbol) pairs, computed and reported **per date**.

### 4.4 Tolerance, and the power guard

- **PASS:** `argmin h = 0` on every date **and** `MARD(h=0) ≤ 25 bps`.
  *Why 25 bps:* a liquid name's own 1-minute bar spans ~10–20 bps high-to-low, so
  whatever quote the build read can legitimately sit that far from the bar close.
  Beyond 25 bps it is a different observation, not a different tick.
- **POWER GUARD:** `MARD(h=±1h) ≥ 3 × MARD(h=0)` is **required for the date to
  count**. On a flat tape the price an hour away is the same price, so h=0 "wins"
  meaninglessly. A date failing the guard is **dropped and reported as no-power**,
  never counted as a pass. Without this guard a quiet session launders a timezone
  bug into a green test.
- **Anything but h=0 wins → STOP and report.** Do not silently apply the winning
  offset.

### 4.5 The existing `test_reconcile_intraday_coverage` has zero power here

Its stated assertion is "≥95% of ranked symbols have a bar within 5 minutes."
There is a bar every minute of the session, so it passes identically whether the
timestamps are aligned or off by an hour. **Coverage is not alignment.** The test
must assert both: coverage ≥95% *and* `argmin h = 0` with the power guard
satisfied.

## 5. (c) The split / adjustment diagnostic

### 5.1 Method

1. Populate `corporate_actions` from `YFinanceProvider.fetch_actions` over the full
   ranked universe, 2025-09-01 → today. It already raises `ProviderUnavailable` on
   partial failure — keep that.
2. Repeat §3.3's close(T−1) comparison over the **full archive history**
   (2025-10-01 forward), this time against the **adjusted** series, giving
   `d(symbol, date)`.
3. For each symbol with a split of ratio `k` on ex-date `D`, compare `median d` for
   dates `< D` against `median d` for dates `≥ D`.

### 5.2 Signature and verdict

If `close_price` is **unadjusted**, comparing it to an adjusted series gives
`d ≈ k − 1` before `D` and `d ≈ 0` after `D` — a step function of exactly the split
ratio at exactly the ex-date. That is unmistakable.

- **UNADJUSTED** if, for ≥80% of symbols with a split in the window, `median d(< D)`
  is within 2% *relative* of `k − 1` **and** `median d(≥ D)` is within 50 bps of 0.
- **ADJUSTED** if `d ≈ 0` on both sides for those same symbols.
- **NEITHER → STOP.** A third pattern means something other than adjustment is
  wrong, and reporting an adjustment verdict would be a guess.

Expected answer is **unadjusted**: F1 proves `close_price` is never restated (100%
identical across snapshots), and never-restated raw prices are exactly what produce
F7's SFTBY discrepancy. But expected is not measured, and this is the measurement.

### 5.3 SFTBY is not yet evidence of anything

F7 records `SFTBY` at 28.68 on 2026-06-01 and 15.88 on 2026-09-01 — a ratio of
**1.806**, not a clean 2:1. Three months is ample time for a −45% drawdown with no
corporate action at all. **Check `SFTBY` against `corporate_actions` before citing
it as split evidence.** If there is no split on its books, F7's second bullet needs
rewording and the adjustment question rests on the §5.2 population, not on this one
name.

### 5.4 Two concentrations that are not splits

Report mismatches **by symbol** and **by date**, both:

- a **date** where nearly everything mismatches is a bad archive day or a provider
  outage;
- a **symbol** that always mismatches is a ticker-mapping failure (§2.3).

Neither is a corporate action, and both would otherwise be misread as one.

## 6. Tolerance summary — sign off on this table

| test | statistic | tolerance | pass bar | where the number comes from |
|---|---|---|---|---|
| (a) daily | match rate vs close(T−1) | **5 bps** | ≥0.95 every date, and ≥0.20 above close(T) | float/vendor noise ≪ 5 bps; coincidence ≈3% at 1.5% daily vol |
| (a) daily | — | 50 bps | reported only | PLAN §2c continuity |
| (b) intraday | MARD at h=0 | **25 bps** | argmin h = 0 every date | a 1-min bar's own range is 10–20 bps |
| (b) intraday | MARD(±1h) / MARD(0) | **≥3×** | required for the date to count | flat tape ⇒ no power |
| (c) splits | median d before / after ex-date | 2% rel. of `k−1`; 50 bps of 0 | ≥80% of split symbols | the step is `k−1`, i.e. ≥100% for a 2:1 — 2% is generous and still decisive |

## 7. Deliverables

- `analysis/alignment_anchor.py` — the three measurements, provider-agnostic.
- `data/results/alignment_anchor.json` — per-date tables, so a later session can
  re-check without re-fetching.
- `docs/FINDINGS.md` **§F8** — the anchor, stated as a measured fact.
- The four `test_reconcile_*` tests, un-skipped by implementation:

| stub | becomes |
|---|---|
| `test_reconcile_trading_calendar` | **runs first**; builds the session grid (a) depends on |
| `test_reconcile_price_agreement` | (a), asserting the *anchor* — its current docstring already assumes the prior-session answer |
| `test_reconcile_intraday_coverage` | (b): coverage **and** `argmin h = 0` with the power guard |
| `test_reconcile_no_phantom_returns` | (c), against a populated `corporate_actions` |

Only after all four pass does `robin_stocks` get implemented — against an alignment
that has been proven rather than assumed.
