# zoltar-ranks — build plan

**Audience:** the implementing agent (Sonnet 5). Read `docs/FINDINGS.md` first —
it contains verified facts about the upstream data that this plan depends on.

**Goal.** Turn the existing Zoltar rank production process into a measurable
research loop: a point-in-time archive, an honest benchmark, a bias diagnostic
suite, a hypothesis register, and a backtester — with **the timing of rank
generation vs. the timing of the buy** as the first-class question, because that
is where an immediate, executable return improvement most likely lives.

---

## 0. Rules the agent must not break

These exist because violating any one of them produces a backtest that looks
great and loses money.

| # | Rule |
|---|---|
| R1 | **Never write to the upstream repo.** `apod-1/ZoltarFinancial` is read-only. All writes go to `zoltarfinancial/zoltar-ranks` or `data/`. |
| R2 | **Never UPDATE a row in `ranks`.** The archive is append-only. A changed score is a finding, not a correction. |
| R3 | **No same-bar execution.** Every simulated fill must occur strictly after the timestamp of the information that triggered it, with an explicit, configurable latency. Default 0 is forbidden. |
| R4 | **Never backtest on `*_rankings_latest.pkl`.** It contains `source='train'` rows scored by the current model. Use `ranks` (built from `*_PROD_*`) only, and only rows with `src_split='validate_oot'`. |
| R5 | **`run_kind='placeholder'` rows are not tradable at their timestamp.** They are stamped with the *next* day at 00:00:00. Map them to their real availability time (previous evening) or exclude them. |
| R6 | **Every result reports an uncertainty interval**, not a point estimate. With ~230 trading days of history, a strategy difference smaller than its bootstrap CI is not a finding. |
| R7 | **Log every hypothesis before testing it** in `docs/HYPOTHESES.md`, including the pre-registered success criterion. Count every test for multiple-comparison correction. |
| R8 | **No look-ahead in universe construction.** Only symbols present in the archive *as of* the decision timestamp are eligible. |
| R9 | If a contract test in `tests/test_harvest.py` fails, **stop and report**. Do not "fix" it by loosening the assertion. |

---

## Phase 0 — Environment (½ day)

```bash
cd C:\Shared\ClaudeWork\zoltar-ranks
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
pytest tests -q -m "not network"     # offline contract tests
pytest tests -q                       # includes upstream verification
```

**Acceptance:** all tests pass, including `test_upstream_is_point_in_time`.

---

## Phase 1 — Point-in-time rank archive ✅ built, validated

Already implemented and validated against the live upstream repo.

```bash
python -m zoltar_ranks.ingest.harvest_ranks --mode backfill --export-parquet
python -m zoltar_ranks.ingest.harvest_ranks --mode daily     # schedule this
```

- `backfill` uses a **coverage walk**: fetch HEAD, read how far back the blob
  reaches, binary-search for the oldest commit whose blob still reaches that
  floor, repeat. 2–4 fetches per file instead of 68.
- `daily` fetches HEAD only, and falls back to a coverage walk automatically if
  it detects a gap against what the archive already holds.
- Everything lands in DuckDB (`data/zoltar.duckdb`), keyed
  `(run_ts, symbol, risk_bucket)`, with a `harvest_manifest` for idempotency.

**Expected after backfill:** ~234 daily run timestamps back to 2025-10-01 and
~376 run timestamps including intraday back to 2026-05-16.

### 1a. Expected returns and SHAP ✅ built, validated

`ingest/harvest_er.py` and `ingest/harvest_shap.py` are implemented and
validated against real blobs across the full commit range. Both are small files,
so they read every commit rather than walking coverage.

**Still to do in this phase:**

1. `ingest/harvest_reference.py` — `fundamentals_df_latest.pkl` and
   `ratings_detail_df_latest.pkl`, stamped with the harvest date so they form a
   slowly-changing dimension rather than being overwritten. Add the tables to
   `schema.sql` and the step to `scripts/daily.py::STEPS`.
2. **Run `scripts/schedule_harvest.ps1`.** This is the time-sensitive step —
   it stops the ongoing intraday data loss described in FINDINGS F2. Do it
   before any analysis work.

**Acceptance:** re-running any harvester inserts 0 new rows; `run_ts` coverage
matches the table in FINDINGS F3; `Get-ScheduledTask -TaskName ZoltarRanksHarvest
| Get-ScheduledTaskInfo` shows a recent successful run; `data/results/
last_run_status.json` reports no failed steps.

---

## Phase 2 — Market data layer (2–3 days)

The archive tells us what was *recommended*. This tells us what would have
*happened*.

### 2a. Provider adapter

`sources/prices.py` with one interface and three implementations, selected by
`config.price_provider`:

```python
class PriceProvider(Protocol):
    def daily_bars(symbols, start, end) -> DataFrame   # date, symbol, o,h,l,c,v, adjusted
    def intraday_bars(symbols, start, end, interval) -> DataFrame
    def corporate_actions(symbols, start, end) -> DataFrame
```

- **`robin_stocks`** — primary. Same source the ranks are built from, so
  `Close_Price` in `ranks` should reconcile against it. That reconciliation is
  itself a test (see 2c).
- **`alpaca`** — secondary, for minute bars. Known to have coverage gaps for some
  Robinhood-listed tickers; the adapter must record a per-symbol coverage table
  rather than silently returning short frames.
- **`yfinance`** — fallback and cross-check only.

Cache every response to `data/cache/prices/` keyed by
`(provider, symbol, interval, date)` so a re-run costs nothing.

### 2b. Corporate actions

Populate `corporate_actions` and build `prices_daily_adjusted` as a view. Do not
skip this: FINDINGS F7 shows at least one symbol whose `Close_Price` halved
between snapshots, which would read as a -45% "return".

### 2c. Reconciliation tests (`tests/test_prices.py`)

| test | assertion |
|---|---|
| price agreement | for morning runs, `ranks.close_price` matches the prior session's provider close within 0.5% for ≥99% of symbols, after split adjustment |
| intraday coverage | for each intraday `run_ts`, ≥95% of ranked symbols have a bar within 5 minutes |
| no phantom returns | no adjusted 1-day return exceeds ±60% without a matching corporate action |
| calendar | no bars on market holidays; every trading day 2025-10-01→today present |

**Acceptance:** all four pass, and a `data/results/coverage_report.html` shows
per-symbol, per-interval coverage so we know exactly where the backtest is blind.

---

## Phase 3 — Benchmark replication (2 days)

Two backtests, and the difference between them is itself a headline result.

### 3a. `BASELINE_ASIS` — faithful replication of the Streamlit engine

Reimplement `Strategy_Play.py::update_strategy` exactly, bugs included:
top 5 by `Low_Risk_Score`, equal weight, buy at the snapshot's `Close_Price`,
sell when `gain_loss > +2%` or `<= -1%` evaluated at snapshot cadence, cash
split before dedup. Config in `config.baseline_*`.

**Acceptance:** running it over the same window the Streamlit app covers
reproduces the app's reported equity curve within 1% terminal value. If it does
not, the discrepancy must be explained before proceeding — it means we have
misunderstood the engine.

### 3b. `BASELINE_HONEST` — same rules, realistic execution

Identical selection logic, but:

- fills at the next available tradable price after `run_ts + execution_latency`
  (default 15 minutes, configurable);
- exits evaluated against **intraday OHLC**, not snapshot closes — a -1% stop
  triggers if the low touches it, with the standard conservative convention
  (if both the stop and the target are inside the same bar, assume the stop);
- explicit costs: commission (0 for Robinhood), spread (half the quoted spread,
  or a liquidity-tiered estimate), and slippage as a function of order size vs.
  ADV;
- cash accounting corrected.

### 3c. Passive baselines

SPY and QQQ buy-and-hold, and an equal-weight random-5-from-universe portfolio
with the same exit rules, bootstrapped 1,000 times. **The random portfolio is
the real benchmark** — it separates "the ranks pick well" from "the -1%/+2%
exit rule is what makes money."

**Deliverable:** `data/results/benchmark_report.html` — equity curves, and a
table of `BASELINE_ASIS` vs `BASELINE_HONEST` vs `RANDOM_5` vs `SPY`.

> Expect `BASELINE_ASIS` to beat `BASELINE_HONEST` substantially. That gap is
> the execution bias, and quantifying it is the first deliverable of value.

---

## Phase 4 — Measurement framework (2 days)

Build this before any hypothesis testing, so every experiment reports the same
numbers the same way.

### 4a. Metrics (`analysis/metrics.py`)

Total and annualized return, volatility, Sharpe, Sortino, max drawdown, Calmar,
hit rate, average win/loss, profit factor, turnover, average holding period,
exposure (% of time invested), and per-trade return distribution.

### 4b. Statistical machinery (`analysis/stats.py`) — the part that matters

With ~230 trading days, naive t-tests will produce false discoveries.

| tool | why |
|---|---|
| **Stationary block bootstrap** (Politis–Romano, mean block ≈ 10 days) | daily returns are autocorrelated; use for all CIs on Sharpe and return differences |
| **Purged, embargoed walk-forward CV** (López de Prado) | overlapping holding periods leak across a naive train/test split; purge the holding-period window, embargo 2 days |
| **Deflated Sharpe ratio** | corrects the Sharpe for the number of configurations tried |
| **Benjamini–Hochberg FDR at q=0.10** | applied across the whole hypothesis register, not per test |
| **Minimum detectable effect calculator** | run *before* each test; if the design cannot detect the effect size we care about, say so and do not run it |

`analysis/stats.py` must expose `compare_strategies(a, b) -> {diff, ci_low, ci_high, p, n_eff}` and every report must use it.

### 4c. Signal-quality metrics, independent of any trading rule

Information Coefficient (Spearman rank correlation of score vs. forward return)
at horizons 1, 2, 3, 5, 10, 14 days — matching the 14 horizons in
`expected_returns` — plus IC t-stat, IC decay curve, and quantile spread
(top-decile minus bottom-decile forward return).

**Acceptance:** `analysis/stats.py` has unit tests with synthetic data where the
true answer is known (e.g. a known-Sharpe AR(1) series recovers its CI coverage).

---

## Phase 5 — Bias diagnostics (3–4 days)

Now describe *where* the ranks are wrong, before trying to fix anything.

Produce `data/results/bias_report.html` answering:

1. **Horizon bias.** IC by horizon. The models emit `Score_HoldPeriod` (7.5 days
   in current data) — does realized IC actually peak there, or earlier? If the
   scores predict 3-day moves but the exit rule holds for 7.5, the mismatch is
   free money.
2. **Segment bias.** IC and realized return by `Cap_Size` segment. Three separate
   model families means three separate calibrations; are their scores even on the
   same scale? Check whether top-5-overall is systematically dominated by one
   segment (the 2026-09-01 top 5 was 4× `Small`), and whether that segment is
   the one that actually performs.
3. **Sector / industry bias.** Concentration of picks vs. contribution to return.
4. **Liquidity and price bias.** IC bucketed by ADV and by share price. Low-price
   and low-ADV names inflate paper returns and die in execution.
5. **Score-level calibration.** Bucket scores into deciles; plot predicted
   (`er` from `expected_returns`) vs. realized. A monotone but mis-scaled curve
   is fixable by recalibration; a non-monotone one is a model problem.
6. **Rank stability / turnover.** How much does the top 5 change between the
   morning run and each intraday re-score? Between consecutive days? High churn
   with no IC gain is pure cost.
7. **Regime dependence.** Split by market state (SPY 20-day trend, VIX tercile).
   A strategy that only works in one regime is a position on that regime.
8. **SHAP drift.** Using `shap_summary`, track top-feature importance over time
   per segment. Correlate periods of feature drift with periods of IC decay.
   This is the mechanism link between a bias and its cause.
9. **Crowding / self-impact.** Do published top-5 names show abnormal open
   volume? Relevant if the app has users.

Each finding gets a one-line entry in `docs/HYPOTHESES.md` with a proposed test.

---

## Phase 6 — Execution timing study (PRIMARY OBJECTIVE, 4–5 days)

The question: **given a rank produced at time T, when should the buy happen?**

### 6a. Build the decision grid

For every `(run_ts, symbol)` in the archive, compute forward returns from a grid
of candidate entry times:

| axis | values |
|---|---|
| entry time | market open (08:30 CDT), +5, +15, +30, +60 min, 11:00, 13:00, close (15:00), next-day open |
| rank vintage | previous nightly placeholder, morning build, each intraday re-score, latest available at entry |
| exit rule | fixed horizon 1/2/3/5/7/10/14d, `Score_HoldPeriod`, and the -1%/+2% bracket |
| order type | market, limit at last close, limit at −0.25% |

The archive supports this directly: intraday `run_ts` values give real,
point-in-time scores at ~30-minute resolution through the session, so
"score at 10:30, buy at 11:00" is a genuine observation, not a reconstruction.

### 6b. The three questions to answer, in order

1. **Decay:** how fast does a rank's edge decay after `run_ts`? Plot IC and
   mean forward return vs. minutes elapsed. If the edge is gone by 10:00, the
   morning-build-then-buy-at-open workflow is already near-optimal and the win
   is elsewhere. If it *builds* through the morning, buying later is free alpha.
2. **Vintage:** is the intraday re-score better than the morning score at the
   same entry time? This isolates information value from timing value — they are
   different levers and conflating them is the classic mistake here.
3. **Interaction:** does optimal entry time differ by segment, liquidity, or
   score magnitude? A single global rule is unlikely to be optimal.

### 6c. Guard against the obvious trap

The grid above is ~9 × 4 × 9 × 4 = 1,296 configurations over ~230 days. **This
will produce a spuriously excellent winner.** Mandatory controls:

- pre-register the grid in `docs/HYPOTHESES.md` before running it;
- report the deflated Sharpe ratio for the winner, accounting for all 1,296;
- walk-forward validation: fit the optimal timing on 2025-10→2026-04, test on
  2026-05→present, and report both;
- require the result to be **smooth in its neighbours** — if 10:30 wins but
  10:00 and 11:00 both lose, it is noise, not a signal;
- report the effect size in basis points per trade alongside realistic costs.
  A 4 bp improvement that costs 6 bp in spread is not an improvement.

**Deliverable:** `data/results/timing_report.html` — the decay curve, a heatmap
of return by (entry time × exit rule), the walk-forward table, and a single
recommended execution change with its confidence interval and its cost-adjusted
expected value.

---

## Phase 7 — Hypothesis register and testing loop (ongoing)

`docs/HYPOTHESES.md` is the spine of the project. One row per hypothesis:

```
ID | Date | Statement | Lever | Pre-registered test | MDE | Status | Result | CI | FDR-adjusted p
```

Starter set, derived from the findings above:

| ID | Hypothesis | Lever |
|---|---|---|
| H1 | Realized IC peaks at a shorter horizon than `Score_HoldPeriod`, so shortening the hold improves risk-adjusted return | exit rule |
| H2 | Delaying entry past the open captures a lower average entry price without losing edge | entry timing |
| H3 | The latest intraday re-score beats the morning score at the same entry time | rank vintage |
| H4 | Scores are not comparable across the three cap segments; per-segment z-scoring beats pooled top-5 | rank normalization |
| H5 | Excluding the lowest ADV / lowest price quintile raises net-of-cost return | universe filter |
| H6 | The −1%/+2% bracket is asymmetric in the wrong direction given observed skew; a wider stop improves expectancy | exit rule |
| H7 | `omit_first > 0` (skipping the very top rank) improves returns — the top name may be crowded or already moved | selection |
| H8 | Periods of SHAP feature drift predict periods of IC decay, and can be used as a live risk-off signal | model monitoring |
| H9 | Most of `BASELINE_ASIS`'s edge is same-bar execution, not signal | execution bias |
| H10 | The `-1%/+2%` rule beats buy-and-hold on *random* stocks, i.e. the exit rule not the rank is the source of return | attribution |

H9 and H10 are the uncomfortable ones. Test them **first** — if either is true,
the priority order of everything else changes.

Protocol per hypothesis: state it → compute the minimum detectable effect → if
underpowered, say so and stop → run on training window → validate out-of-sample
→ record result with CI and FDR-adjusted p → mark Confirmed / Rejected /
Underpowered. **Never** silently drop a rejected hypothesis.

---

## Phase 8 — Dashboard and continuous operation (2–3 days)

`dashboard/` — a static HTML dashboard regenerated by the daily job:

- archive health: latest `run_ts` per feed, gap alarm, rows added today;
- signal health: rolling 20-day IC by segment and horizon, with control limits;
- benchmark tracking: `BASELINE_HONEST` vs. the current live rule, live equity;
- hypothesis register status;
- the standing execution recommendation and its live-tracked performance since
  adoption.

Automate: a single `scripts/daily.py` that harvests → refreshes prices →
recomputes metrics → regenerates the dashboard → commits results to
`zoltar-ranks`. Failures must be loud (non-zero exit and a written status file).

---

## Sequencing and effort

| Phase | Depends on | Effort | Why now |
|---|---|---|---|
| 1a — remaining ingestion + scheduler | 1 | 1 day | **urgent — data is being destroyed daily** |
| 2 — market data | 1 | 2–3 d | nothing measurable without it |
| 3 — benchmark | 2 | 2 d | H9 is answerable here and may reframe everything |
| 4 — stats framework | — | 2 d | can run parallel with 2 |
| 5 — bias diagnostics | 3, 4 | 3–4 d | generates the hypothesis backlog |
| 6 — timing study | 3, 4 | 4–5 d | the primary objective |
| 7 — testing loop | 5, 6 | ongoing | |
| 8 — dashboard | 6 | 2–3 d | |

Critical path to the first actionable answer: **1a → 2 → 3 → 6**, roughly two
weeks.

---

## Known risks

| Risk | Impact | Mitigation |
|---|---|---|
| Intraday history keeps eroding | fill-timing study loses power every day | Phase 1a scheduler, today |
| ~230 trading days is thin | most hypotheses will be underpowered | MDE check before every test; report power honestly; prefer signal-level (IC) tests, which have ~1,200 symbols × 230 days of cross-section, over portfolio-level tests, which have 230 observations |
| 1,296-cell timing grid overfits | a confident, wrong execution change | deflated Sharpe, walk-forward, neighbour-smoothness requirement |
| `Close_Price` unadjusted for splits | phantom returns | corporate actions table + phantom-return test |
| Alpaca gaps for Robinhood tickers | silent survivorship in the intraday study | per-symbol coverage table; report which symbols were excluded and re-run headline results on the covered-only universe |
| Upstream schema drift | pipeline breaks silently | network contract tests run daily; loud failure |
| Universe survivorship | inflated backtest returns | track symbol entry/exit in the archive; treat disappearance as a delisting until proven otherwise |
