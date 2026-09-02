# START HERE

You are the implementing agent for this repo. Read this file completely before
touching anything. It exists so there is exactly one right answer to "what do I
do first."

## The one-paragraph version

Andrew runs a quant ranking pipeline that publishes stock ranks to
`github.com/apod-1/ZoltarFinancial` three times a day. The intraday portion of
that history sits in a rolling buffer that **permanently deletes old snapshots
every ~10 trading days**. This repo captures that history into a local
point-in-time archive, then uses it to answer one question: *given a rank
produced at time T, when should the buy actually happen?* The archive is
**built, populated and harvesting on a 30-minute schedule**. Work starts at
Phase 2 (market data). Read the two warnings below before you touch anything.

## Read these, in this order

| # | File | What it gives you |
|---|------|-------------------|
| 1 | `docs/FINDINGS.md` | Verified facts about the upstream data. **Do not re-derive these** — they were measured against the live repo, and re-deriving them costs hours and gigabytes. |
| 2 | `docs/PLAN.md` | The phased build plan. Section 0 contains nine rules you must not break. |
| 3 | `AGENTS.md` | Working agreement, layout, conventions. |
| 4 | `docs/HYPOTHESES.md` | The pre-registration register. Every experiment gets a row here *before* it runs. |
| 5 | `docs/DASHBOARD.md` | The one file you hand to the dashboard workstream, and the schema it expects. |

`CLAUDE.md` is loaded automatically at the repo root and carries the nine
non-negotiable rules in condensed form, so they stay in context for the whole
session. It is a summary of `docs/PLAN.md` §0, not a substitute for reading it.

## Where things stand — updated 2026-09-01 (clock time; see SESSION_LOG note)

Do not re-do these. A previous session already ran them.

| Done | Detail |
|---|---|
| ✅ Repo initialised, pushed | `origin` = `github.com/zoltarfinancial/zoltar-ranks`, branch `main`. Pushing works. |
| ✅ venv + dependencies | `.venv` on **Python 3.14.7** (the only interpreter on this box), `pip install -e .` done. The pins resolve on 3.14 — nothing loosened. |
| ✅ **All tests pass, network included** | 30 passed, 4 skipped. All four upstream contract tests green, `test_upstream_is_point_in_time` among them. The 4 skips are the Phase 2 `test_reconcile_*`. |
| ✅ **The archive is POPULATED** | `data/zoltar.duckdb`, 1.25M rank rows. Numbers below. |
| ✅ **The 30-min harvester is scheduled and proven** | `ZoltarRanksHarvest`, 07:00–21:30 every 30 min. Triggered a live run: `LastTaskResult 0`. |
| ✅ Idempotency verified | Manual re-run and scheduled run both: `staged=866402 inserted=0`, all table counts unchanged. |
| ✅ `.env` created | From `.env.example`, **still empty** — fill in credentials before Phase 2. |
| ✅ Dashboard workstream started | `dashboard/` exists and is owned by the Cowork session — **do not edit it** |

### What the archive actually holds

| feed | run ts | rows | first | last |
|---|---|---|---|---|
| `daily` | 233 | 553,332 | 2025-10-01 07:46:57 | 2026-09-02 19:34:49 |
| `all` | 300 | 701,344 | 2026-05-18 15:09:29 | 2026-09-01 15:17:16 |
| `daily_ranks` | **338** | 772,921 | **2026-03-03 20:18:25** | 2026-09-02 00:00:00 |

**871 distinct run timestamps** in union (was 533 before the daily_ranks
backfill), floor 2025-10-01, ceiling 2026-09-02. By class: 482 intraday,
218 morning, 141 nightly, 30 placeholder. `expected_returns` 43 as-of dates;
`shap_summary` 322 snapshots.

Runs/day: 244 days before 2026-08-19 at median **1**; 13 days from it at median
**15** (max 19). Whether that is collapse or a real cadence change is
**unresolved** -- see FINDINGS F2, and do not assert either.

⚠️ **The daily_ranks backfill is INCOMPLETE by 8 of 228 files** -- GitHub became
unreachable mid-run. Re-run `python -m zoltar_ranks.ingest.harvest_daily_ranks
--mode backfill` when connectivity is back; it is idempotent and will fill only
the gap. The step now exits non-zero while incomplete.

### Two things the next session must not discover the hard way

1. **Only ~10 trading days of real intraday granularity exist.** The `all` feed
   holds **1 run/day up to 2026-08-18** and 9–16/day only from 2026-08-19 on.
   That is 300 run timestamps, not FINDINGS F3's 376 — a density shortfall, not
   a date gap. The rolling buffer already destroyed the intraday history the
   Phase 6 timing study is built on. Everything before 2026-08-19 is one
   snapshot per day and will never improve. **The harvester lapsing for a day
   costs a day of the primary objective's raw material.**
2. **Rule 5 changed: never key execution off `run_ts`.** Use the **`ranks_pit`**
   view, which carries `available_at` — the information timestamp, and the only
   one rule 3's latency may be measured from. Upstream stamps some builds
   *later* than it published them (run `2026-09-02 19:34:49` was committed
   `2026-09-01 19:36:25`), so `run_ts` is not a lower bound on knowability.
   `run_kind` is now purely descriptive: `placeholder` carries **no** tradability
   verdict, and the forward-stamped nightly build is a first-class strategy
   vintage (H11), not a row to exclude.
   `ranks_pit.available_at` = `committed_at` where `stamp_is_forward` (2,330
   rows, exact), else `run_ts` (1,252,346 rows). It is a **view**, not columns on
   `ranks`, because populating 1.25M rows would be an `UPDATE` (rule 2) — and
   because `committed_at` alone would be 21–292 days stale for backfilled rows,
   `first_seen_sha` being the first commit *harvested*, not the first that
   carried the row. Check `availability_source` and `harvest_lag_days` before
   trusting it on historical rows.

### Answered by Andrew 2026-09-01

- **`Cap_Size` is a model segment label, not literal market cap.** Use it to join
  SHAP segments. It is **not** valid as a size control variable in Phase 5.
- **Intraday tz is America/Chicago**; the schema comment was stale and is fixed.
- **Rule 5 rewritten** as above — see `docs/SESSION_LOG.md`.

| Still open | Blocks |
|---|---|
| **`Close_Price` split-adjusted?** Unknown. Proceeding on the conservative assumption that it is **unadjusted**: build `corporate_actions` first, join it before computing any return, and report what the reconciliation shows. | Phase 2 — and every return in the repo |
| **Which `Strategy_Play_v*.py` is the live Streamlit engine?** (~40 candidates) | Phase 3 — `BASELINE_ASIS` cannot be a faithful replica without it |
| Is a ~9.9%/yr MDE (H11) above the effect worth acting on? | Whether H11 is `powered` or `underpowered` |

## Do this first

Setup and backfill are **done** — do not re-run them expecting work. Confirm the
machine is still healthy, then start Phase 2:

```powershell
cd C:\Shared\ClaudeWork\zoltar-ranks
.\.venv\Scripts\Activate.ps1

pytest tests -q                                              # expect 30 passed, 4 skipped
Get-ScheduledTask -TaskName ZoltarRanksHarvest | Get-ScheduledTaskInfo   # LastTaskResult must be 0
python scripts\daily.py                                      # must insert ZERO rows
```

If the harvester ever inserts rows on a repeat run, stop and fix idempotency
before building on the archive. If `LastTaskResult` is non-zero or
`NumberOfMissedRuns` is climbing, the intraday clock is running again — fix that
first, it is the most perishable thing in the project.

Two things that will come up:

1. **`requirements.txt` is version-pinned with upper bounds, and the pins hold on
   Python 3.14** (verified: they resolve, forcing `pytest` 8.4.2 and `yfinance`
   0.2.66, and the full suite passes). If a future pin will not resolve, loosen
   **that one line**, note it in `docs/SESSION_LOG.md`, and carry on. Do not
   strip the pins wholesale.
2. **A re-backfill bulk-fetches small blobs.** `--mode backfill` does one bulk
   fetch (10-15 s) instead of ~1,000 lazy reads at ~0.45 s each — a 63x speedup,
   measured. The gitignored mirror under `data/cache/` grows to ~328 MB.
   `--no-prefetch` opts out.

## Then: Phase 2, and the order of the work

The critical path to the first result Andrew can act on is
**Phase 2 → Phase 3 → Phase 6**, roughly two weeks:

1. **Phase 2 — market data.** Price provider adapters (`robin_stocks` primary,
   Alpaca for minute bars, yfinance as cross-check), corporate actions, and the
   four reconciliation tests. Nothing downstream is trustworthy without this.
2. **Phase 3 — benchmark.** Build `BASELINE_ASIS` (faithful replica of the
   Streamlit engine, bugs included) and `BASELINE_HONEST` (same selection,
   realistic fills). **Test H9 and H10 here.** They are the falsification tests
   for the whole premise; if either confirms, come back and re-prioritize
   before building anything else.
3. **Phase 6 — the timing study.** The primary objective.

Phase 4 (statistics) can be built in parallel with Phase 2 and is a prerequisite
for Phases 5 and 6.

## The five ways this project goes wrong

1. **You skip the scheduler** and intraday history keeps evaporating. Do it on
   day one, before any analysis.
2. **You allow same-bar execution** and produce a beautiful backtest of a
   strategy that cannot be traded. Every fill happens strictly after the
   information timestamp plus a configured latency; a latency of 0 is a bug.
3. **You backtest on `*_rankings_latest.pkl`.** It contains `source='train'`
   rows scored by the current model. It is for diagnostics only. The archive
   built from `*_PROD_*` is the only honest source.
4. **You search the 1,296-cell timing grid and believe the winner.** With ~230
   trading days it *will* produce a spuriously excellent result. Deflated
   Sharpe, walk-forward validation, and neighbour-smoothness are mandatory, not
   optional polish.
5. **You quietly drop a hypothesis that failed.** The register is the
   denominator for the FDR correction. Rejected rows stay.

## What is already built

| Component | State |
|---|---|
| `sources/git_archive.py` | blobless upstream mirror, point-in-time blob reads — validated |
| `ingest/harvest_ranks.py` | coverage-walk backfill + idempotent daily harvest — validated against live upstream |
| `ingest/harvest_er.py` | expected-return curves, 14 horizons — validated |
| `ingest/harvest_shap.py` | SHAP attributions, symbol-indexed, long form — validated |
| `sources/prices.py` | Phase 2 **interface + disk cache + coverage tracking — built**; the three providers raise `NotImplementedError` |
| `db/schema.sql` | the data contract, including tables Phase 2 will fill |
| `scripts/daily.py` | the scheduled job; append later phases to `STEPS` |
| `tests/test_harvest.py` | offline unit tests + upstream contract tests |

Not built: `analysis/metrics.py`, `analysis/stats.py`, `analysis/backtest.py`,
`analysis/export_dashboard_data.py`, `ingest/harvest_reference.py` — create them
per `docs/PLAN.md`. (`dashboard/` already exists and belongs to the other
workstream — leave it alone.)
`sources/prices.py` has the interface, cache and coverage machinery already;
your Phase 2 job is to implement the three `fetch_*` methods on each provider
**without changing the column contract**, then un-skip the four
`test_reconcile_*` tests in `tests/test_prices.py` by making them pass. Never
un-skip them by deleting the skip.

## When you are unsure

Three things are flagged as unverified in `docs/FINDINGS.md` §F7 and need
Andrew's confirmation rather than your assumption: what `Cap_Size` actually
means, whether `Close_Price` is split-adjusted, and which of the ~40
`Strategy_Play_v*.py` files is the live Streamlit engine. Ask; do not guess.
