# ORDER_RESULT — c-0002.1: the intraday alignment anchor

**Order:** `c-0002.1`, research class, priority now, dispatched 2026-09-08
(event `e-000066-dsp021`), verify: andrew.
**Executed by:** `zoltarlead/claude-code` on `ZOLTARLEAD`, 2026-09-10 21:53–22:15 CDT.
**Branch:** `research/c-0002.1-intraday-anchor` — **waiting_trunk, not merged.**

> This file previously held the report for the courier order
> `t-20260908-courier-zoltarlead`. That report is preserved unchanged on branch
> `fleet/courier-zoltarlead` @ `57ed181`. It has been **replaced**, not merged
> with this one.

## Result

**Both acceptance parts are met, and the anchor's own pre-registered verdict is
`STOP_TIMEZONE`.** Those two statements do not conflict, and neither is hidden
inside the other:

| | |
|---|---|
| **measured offset** | **−25 minutes** |
| **95% confidence interval** | **[−27, −24]** — cluster bootstrap over dates, B = 10,000, seed 20260910 |
| **pre-registered verdict** | **`STOP_TIMEZONE`** — Phase 6 stops pending Andrew (c-0007.1) |

Negative means **the price inside an intraday rank is from about 25 minutes
before that rank's `available_at`.**

`test_alignment_anchor` passes because it asserts that the measurement *exists*,
with an offset and an interval. **It does not assert alignment, and it was
written that way before the result was known.** The alignment verdict is the
STOP above.

## Acceptance

### 1 — `data/results/alignment_anchor_intraday.json` exists with an offset and a CI

```
INTRADAY ANCHOR  STOP_TIMEZONE
  offset -25.0 min, 95% CI [-27.0, -24.0]  (cluster bootstrap over dates, B=10000, seed=20260910)
  a clock-domain hypothesis beats h=0 on ['2026-08-19']. Rule 9: Phase 6 stops; the winning offset is NOT applied.
  wrote C:\Shared\ClaudeWork\zoltar-ranks\data\results\alignment_anchor_intraday.json
```

`data/results/` is gitignored, so the file is force-added on the branch. It is
the only record of a measurement that cannot be retaken from yfinance after about
2026-09-17.

### 2 — `test_alignment_anchor` passes (actual output)

```
tests/test_alignment_anchor.py::test_verdict_pass_when_prior_close_wins_everywhere PASSED [  3%]
tests/test_alignment_anchor.py::test_verdict_stops_on_lookahead PASSED   [  7%]
tests/test_alignment_anchor.py::test_verdict_stops_when_no_candidate_clears_the_bar PASSED [ 10%]
tests/test_alignment_anchor.py::test_one_bad_date_fails_the_whole_verdict PASSED [ 14%]
tests/test_alignment_anchor.py::test_verdict_requires_separation_not_just_a_high_rate PASSED [ 17%]
tests/test_alignment_anchor.py::test_verdict_on_no_data_is_not_a_pass PASSED [ 21%]
tests/test_alignment_anchor.py::test_decision_tolerance_is_tighter_than_the_reconciliation_one PASSED [ 25%]
tests/test_alignment_anchor.py::test_prev_session_skips_the_weekend PASSED [ 28%]
tests/test_alignment_anchor.py::test_prev_session_returns_none_when_the_grid_runs_out PASSED [ 32%]
tests/test_alignment_anchor.py::test_prev_session_is_strictly_before PASSED [ 35%]
tests/test_alignment_anchor.py::test_wilson_brackets_the_point_estimate PASSED [ 39%]
tests/test_alignment_anchor.py::test_wilson_stays_inside_zero_one_at_the_extremes PASSED [ 42%]
tests/test_alignment_anchor.py::test_wilson_is_wider_on_less_data PASSED [ 46%]
tests/test_alignment_anchor.py::test_intraday_method_recovers_a_planted_zero_offset PASSED [ 50%]
tests/test_alignment_anchor.py::test_intraday_method_recovers_a_planted_lag PASSED [ 53%]
tests/test_alignment_anchor.py::test_a_one_hour_clock_error_is_a_timezone_stop_not_a_lag PASSED [ 57%]
tests/test_alignment_anchor.py::test_a_flat_tape_has_no_power_and_cannot_pass PASSED [ 60%]
tests/test_alignment_anchor.py::test_fewer_than_three_powered_dates_is_no_data PASSED [ 64%]
tests/test_alignment_anchor.py::test_stale_bars_beyond_tolerance_are_not_used PASSED [ 67%]
tests/test_alignment_anchor.py::test_mixed_timestamp_resolutions_join PASSED [ 71%]
tests/test_alignment_anchor.py::test_session_window_excludes_post_close_runs PASSED [ 75%]
tests/test_alignment_anchor.py::test_cluster_bootstrap_resamples_dates_not_runs PASSED [ 78%]
tests/test_alignment_anchor.py::test_every_1min_request_spans_at_most_8_days PASSED [ 82%]
tests/test_alignment_anchor.py::test_an_empty_window_is_recorded_as_provider_unavailable PASSED [ 85%]
tests/test_alignment_anchor.py::test_all_windows_empty_raises_rather_than_returning_nothing PASSED [ 89%]
tests/test_alignment_anchor.py::test_dotted_symbols_are_requested_with_a_dash_and_mapped_back PASSED [ 92%]
tests/test_alignment_anchor.py::test_intraday_timestamps_are_converted_to_chicago_wall_clock PASSED [ 96%]
tests/test_alignment_anchor.py::test_intraday_anchor_result_exists_with_offset_and_interval PASSED [100%]
============================= 28 passed in 20.49s =============================
```

Full suite: **239 passed, 4 skipped, 2 failed.** Neither failure is in this
work. `test_review_protocol_invariants_hold` fails on cowork's cycle files
`c-0009..c-0013` (items with `kind: note`, and `depends_on` naming an order id).
`test_no_third_stamping_convention` is the stamp canary, left failing since
2026-09-08 per rule 9.

## What the data say

Pooled curve — median |close_price / bar close − 1| across powered runs, bps:

| offset (min) | −90 | −60 | −45 | −35 | −30 | −27 | **−25** | **−24** | −22 | −20 | −15 | −10 | −5 | 0 | +5 | +30 | +60 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bps | 29.3 | 21.6 | 15.6 | 10.7 | 7.3 | 5.2 | **3.8** | **3.6** | 5.8 | 8.1 | 11.6 | 14.3 | 16.1 | 18.4 | 20.6 | 25.8 | 31.4 |

A single sharp minimum at −24/−25, **5× lower than h = 0 and 6× lower than −60**.
Median deviation at each run's own best offset: **2.5 bps**. Median power ratio:
**8.9** (the guard needs 3).

Per-date medians of the per-run best offset (powered runs):

| date | runs | median | range |
|---|---|---|---|
| 2026-08-19 | 10 | **−30** | −32 … −28 |
| 2026-08-20 | 12 | −24 | −34 … −22 |
| 2026-08-21 | 12 | −25 | −27 … −23 |
| 2026-08-24 | 7 | −25 | −26 … −25 |
| 2026-08-25 | 12 | −23 | −26 … −22 |
| 2026-08-26 | 11 | −25 | −28 … −22 |
| 2026-08-27 | 12 | −24 | −27 … −23 |
| 2026-08-28 | 13 | −24 | −27 … −23 |
| 2026-09-01 | 9 | −24 | −25 … −24 |
| 2026-09-02 | 10 | −28 | −29 … −27 |
| 2026-09-03 | 11 | −26 | −35 … −24 |
| 2026-09-04 | 13 | −24 | −28 … −23 |
| 2026-09-08 | 11 | −27 | −30 … −25 |
| 2026-09-09 | 8 | −24.5 | −28 … −23 |
| 2026-09-10 | 11 | −29 | −30 … −28 |

### Why the STOP fired, and why I did not override it

The timezone gate compares the coarse clock-error cells
{0, ±60, ±120, ±300, ±360} and requires h = 0 to win on every powered date. It
won on 14 of 15. **On 2026-08-19 the lag was −30**, exactly halfway between the
0 and −60 cells, and −60 beat 0 by 1.9 bps (21.98 vs 23.88).

The data do **not** look like a clock error:

- **0 of 162 powered runs has an offset within 10 minutes of −60.** All of them
  lie in [−35, −20].
- A clock error is a constant multiple of 60 minutes for every run. This is
  continuous, and it varies by day from −23 to −30.
- The −60 cell is 6× worse than the fine optimum.

It looks like **build latency**: the pipeline fetches prices, then takes ~25
minutes to score before the rank is stamped and published. One mechanism that
would produce it — **unverified**: a quote feed delayed ~15-20 minutes, plus
build time. Andrew knows the pipeline and can confirm or refute it in one line.

**None of that changes the verdict.** The gate's rule was written into code
before any bar was read, and re-gating after seeing the result is exactly the
loosening rule 9 forbids. What I can offer instead, clearly labelled as
**post-hoc and not applied**: had the gate asked *"does the fine-grid optimum lie
within ±15 minutes of any clock-error cell other than 0?"*, every run would
answer no (the nearest, −60, is at least 25 minutes from every run), and the
verdict would have been `LAG_MEASURED`. Whether to adopt that rule — and
re-measure against the persisted bars — is Andrew's call.

## Coverage — stated exactly

**Symbols.** The runs' own universe: **1,167 symbols**, every one served by at
least one window. Misses by window:

| window (8-day chunk) | rows | symbols served | missing |
|---|---|---|---|
| 2026-08-19 → 2026-08-27 | 2,480,547 | 1,167 | — |
| 2026-08-27 → 2026-09-04 | 2,474,665 | 1,165 | `TWO`, `WBS` |
| 2026-09-04 → 2026-09-11 | 1,653,931 | 1,163 | `CRNX`, `HLX`, `TWO`, `WBS` |

**Dates with 1-minute bars:** 16 sessions — 2026-08-19, 20, 21, 24, 25, 26, 27,
28, 31, 09-01, 02, 03, 04, 08, 09, 10. (2026-09-07 is Labor Day; there is no
session, correctly.)

**ProviderUnavailable:** **none.** 36 requests (3 windows × 12 symbol batches),
0 came back empty for all of their symbols. Had any done so, it would be recorded
with its window in `coverage.provider_unavailable` in the JSON; that list is
present and empty.

**Runs.** 182 dense-era intraday runs → 17 excluded as outside the pre-registered
08:40–14:50 CT window (mostly PRECLOSE runs after the 15:00 close, which would
measure the close rather than the clock) → 165 measured → **162 powered** over
**15 dates**. The 3 no-power runs are each the first run of a day whose intraday
series started late (08-24 11:11, 09-01 10:30, 09-09 10:45); their price matches
the tape nowhere within ±90 minutes. 192,147 run-symbol pairs.
`run_ts == available_at` for every sampled run, so anchoring on `available_at`
(rule 5) and on `run_ts` give the same answer here — but the code keys on
`available_at`.

**Two gaps that are not provider gaps:**

- **2026-08-31 has bars but no archived intraday runs.** That's a gap in the
  archive.
- The dense era only reaches back to 2026-08-19. Yahoo served 1-minute bars back
  to 2026-08-12 today, but the sparse-era days hold about one intraday run each
  and were outside the spec's sample.

## Where this departs from the daily anchor (the prior art), and why

Prior art: `data/results/alignment_anchor.json` (F8) and
`docs/ALIGNMENT_ANCHOR.md` §3–§4.

1. **A continuous offset, not a match rate.** The daily anchor asks *which of
   four candidate prices* the rank carries and reports a match rate at 5 bps. The
   intraday question is *which minute*, so this measures the offset that
   minimises the median deviation on a 1-minute grid from −90 to +90, plus the
   spec's clock-error cells. The order asks for **a measured offset and a
   confidence interval**; the spec's seven coarse cells can only *select* one.
2. **The interval is a cluster bootstrap over dates**, not Wilson. The statistic
   is a median offset, not a proportion; and runs within one session share a tape,
   so resampling runs would narrow the interval in the flattering direction.
3. **The whole universe, not the first 250 symbols.** The daily anchor capped at
   250 alphabetically. Here the fetch cost only 36 requests, so the spec's "the
   run's own universe" is followed literally — and all 6.61M bars are persisted,
   because after about 2026-09-17 they cannot be re-fetched.
4. **Dotted tickers are mapped** (`BRK.B` → `BRK-B`). The daily anchor lost both
   `BF.B` and `BRK.B` to this.
5. **A power guard and a session window**, both from spec §4.4, which the daily
   anchor had no need for.
6. **The same discipline kept:** RAW prices (`auto_adjust=False`), anchoring on
   `available_at`, per-date reporting (a pooled number would have hidden
   2026-08-19), and rules fixed in code before the data were read.

## Three defects found and fixed on the way, all caught before they touched a number

1. **The power guard discarded the best-aligned runs.** When a run's best match
   was exact (median deviation 0), the ratio divided by zero, came back NaN, and
   the run was marked "no power". The planted-offset tests caught it; nothing else
   would have.
2. **The first real run crashed** on a timestamp-resolution mismatch: DuckDB
   returns microseconds, the parquet bars milliseconds. The synthetic tests were
   all nanoseconds. Fixed at the join and pinned with a test.
3. **Yahoo's 1-minute limit fails silently.** An exact 8-day request is served;
   a 9-day request returns an *empty frame*, not an error. The chunking is
   therefore load-bearing, and an empty frame raises or records
   `ProviderUnavailable` — never "no data".

## Needs Andrew

1. **c-0007.1.** The anchor says the clock domain is fine and the rank's price is
   ~25 minutes stale, but its pre-registered gate returned `STOP_TIMEZONE`. Either
   accept the STOP, or approve the corrected gate above and a re-measure against
   the persisted bars. **The measurement no longer depends on the yfinance window
   — the bars are on disk.**
2. **Confirm the mechanism.** Does the intraday pipeline read quotes ~25 minutes
   before it stamps a run — a delayed feed, or build time? That decides whether
   Phase 6 should model it as a fixed latency or a per-run one.
3. **Keep `data/cache/prices/yfinance/intraday_1m/`.** It is 6.61M bars, about
   100 MB, and gitignored. Deleting it after 2026-09-17 makes this measurement
   unrepeatable.
4. **The review board is still red** on cowork's cycles `c-0009..c-0013`
   (unchanged from the courier report).

## Reporting notes

- **The courier does not carry `fleet/bridge/outbox/`.** Its deployed
  `DISPATCH_PATHS` are `orders`, `fleet/msgs`, `fleet/receipts`,
  `data/fleet/heartbeat` and `data/review`. So the outbox copy of this report is
  committed on this branch rather than left for the courier.
- **The branch was published from a separate worktree.** Switching this clone to
  a branch cut from `main` would have deleted `scripts/fleet_courier.py` from
  disk: the courier's code is not on `main` yet, and the scheduled task runs it
  from this working tree.
