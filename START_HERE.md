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
produced at time T, when should the buy actually happen?* The archive **code**
is built and validated against live upstream — but it has never been **run**, so
the archive itself is empty. Populate it first, then start Phase 2.

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
| ✅ **All tests pass, network included** | 27 passed, 4 skipped. All four upstream contract tests green, `test_upstream_is_point_in_time` among them. The 4 skips are the Phase 2 `test_reconcile_*`. |
| ✅ **The archive is POPULATED** | `data/zoltar.duckdb`, 1.25M rank rows. Numbers below. |
| ✅ **The 30-min harvester is scheduled and proven** | `ZoltarRanksHarvest`, 07:00–21:30 every 30 min. Triggered a live run: `LastTaskResult 0`. |
| ✅ Idempotency verified | Manual re-run and scheduled run both: `staged=866402 inserted=0`, all table counts unchanged. |
| ✅ `.env` created | From `.env.example`, **still empty** — fill in credentials before Phase 2. |
| ✅ Dashboard workstream started | `dashboard/` exists and is owned by the Cowork session — **do not edit it** |

### What the archive actually holds

| feed | bucket | run ts | rows | first | last |
|---|---|---|---|---|---|
| `daily` | low / high | **233** each | 276,666 each | 2025-10-01 07:46:57 | 2026-09-02 19:34:49 |
| `all` | low / high | **300** each | 350,672 each | 2026-05-18 15:09:29 | 2026-09-01 15:17:16 |

533 distinct run timestamps in union. By class: 214 morning, 255 intraday,
64 nightly, **0 placeholder**. `expected_returns`: 43 as-of dates (32 `daily`,
43 `live`), 14 horizons, 1.23M rows. `shap_summary`: 322 snapshots across
Large/Mid/Small, 3.73M rows.

`daily` starting at 2025-10-01 (not 2026-01-01) confirms the coverage walk ran.

### Two things the next session must not discover the hard way

1. **Only ~10 trading days of real intraday granularity exist.** The `all` feed
   holds **1 run/day up to 2026-08-18** and 9–16/day only from 2026-08-19 on.
   That is 300 run timestamps, not FINDINGS F3's 376 — a density shortfall, not
   a date gap. The rolling buffer already destroyed the intraday history the
   Phase 6 timing study is built on. Everything before 2026-08-19 is one
   snapshot per day and will never improve. **The harvester lapsing for a day
   costs a day of the primary objective's raw material.**
2. **`classify_run` is not catching FINDINGS F4's forward-stamped build.** Run
   `2026-09-02 19:34:49` was committed `2026-09-01 19:36:25` — 24h forward, but
   not at `00:00:00`, so it is labelled `nightly` and CLAUDE.md rule 5 (which
   only guards `placeholder`) does not apply. Zero rows archive-wide are
   `placeholder`. `harvest_manifest.committed_at` exists, so `run_ts >
   committed_at` is the available invariant. **Ask Andrew before changing it** —
   re-labelling a run class re-labels every downstream decision point.

| Open question | Blocks |
|---|---|
| `schema.sql` says `prices_intraday.ts` is `America/New_York`; `prices.py`, CLAUDE.md and AGENTS.md all say `America/Chicago` | Phase 2 — a comment only, but on the column the timing study keys off |
| The three FINDINGS §F7 items (`Cap_Size`, `Close_Price` adjustment, live Streamlit engine) | Phases 2, 3, 5 |

## Do this first, in this order

```powershell
cd C:\Shared\ClaudeWork\zoltar-ranks
.\.venv\Scripts\Activate.ps1

pytest tests -q                  # ALL of them, including the 8 network tests
.\scripts\setup.ps1              # idempotent; its real job now is the backfill
.\scripts\schedule_harvest.ps1   # elevated PowerShell
```

Three things that will come up:

1. **`requirements.txt` is now version-pinned with upper bounds.** If a pin will
   not resolve on Python 3.14, loosen **that one line**, note it in
   `docs/SESSION_LOG.md`, and carry on. Do not strip the pins wholesale — in a
   research repo a silent dependency major bump changes results without failing a
   test. (pandas 3.0.2 / numpy 2.4.4 were verified to read every upstream pickle
   correctly; Python 3.14 specifically has not been verified.)
2. **The backfill bulk-fetches small blobs.** The ER and SHAP harvesters read
   ~1,000 distinct blobs, and fetching them lazily costs ~0.45 s each (~8 min).
   `--mode backfill` therefore does one bulk fetch first: 10-15 s, after which
   reads take ~7 ms — a 63× speedup, measured. The cost is disk: the mirror under
   `data/cache/` grows from ~1 MB to ~328 MB. That directory is gitignored. Pass
   `--no-prefetch` if you would rather trade the 8 minutes for the disk.
3. **`setup.ps1` stops at the first failure on purpose.** If the network contract
   tests fail, stop and report. That means upstream changed shape or started
   restating historical scores, and every downstream conclusion becomes suspect.
   Do not loosen the assertion.

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
