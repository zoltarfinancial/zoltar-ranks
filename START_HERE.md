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
produced at time T, when should the buy actually happen?* Phase 1 (the archive)
is already built and validated. Your job starts at Phase 2.

## Read these, in this order

| # | File | What it gives you |
|---|------|-------------------|
| 1 | `docs/FINDINGS.md` | Verified facts about the upstream data. **Do not re-derive these** — they were measured against the live repo, and re-deriving them costs hours and gigabytes. |
| 2 | `docs/PLAN.md` | The phased build plan. Section 0 contains nine rules you must not break. |
| 3 | `AGENTS.md` | Working agreement, layout, conventions. |
| 4 | `docs/HYPOTHESES.md` | The pre-registration register. Every experiment gets a row here *before* it runs. |

`CLAUDE.md` is loaded automatically at the repo root and carries the nine
non-negotiable rules in condensed form, so they stay in context for the whole
session. It is a summary of `docs/PLAN.md` §0, not a substitute for reading it.

## Do this first, in this order

```powershell
cd C:\Shared\ClaudeWork\zoltar-ranks
copy .env.example .env           # then fill in credentials (Phase 2 needs them)
.\scripts\init_repo.ps1          # git init + connect to the empty GitHub remote
.\scripts\setup.ps1              # venv, deps, contract tests, one-time backfill
.\scripts\schedule_harvest.ps1   # elevated PowerShell; every 30 min, 07:00-21:30
```

`.env` is gitignored. Never commit it, never print it into logs or results.
Phase 1 needs no credentials at all — it reads a public repo — so you can run
the backfill before you have any keys.

`setup.ps1` stops at the first failure on purpose. If the **upstream contract
tests** fail, stop and report — that means upstream changed its data shape or
started restating historical scores, and every downstream conclusion in this
repo becomes suspect until that is understood. Do not loosen the assertion.

### What "the backfill worked" looks like

After `setup.ps1`, this query should return roughly these numbers:

```sql
SELECT feed, risk_bucket, count(DISTINCT run_ts) runs,
       min(run_ts) first_run, max(run_ts) last_run
FROM ranks GROUP BY 1,2;
```

| feed | runs | first_run |
|---|---|---|
| `daily` | ~234 | ~2025-10-01 |
| `all` | ~376 | ~2026-05-16 |

Plus `expected_returns` covering ~30 as-of dates and `shap_summary` covering
~200+ snapshots across three segments. If `daily` starts at 2026-01-01 instead
of 2025-10-01, the coverage walk did not run — you fetched HEAD only and are
missing three months.

Re-running any harvester must insert **zero** new rows. If it does not, the
idempotency is broken and you must fix that before building on the archive.

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
`ingest/harvest_reference.py`, `dashboard/` — create them per `docs/PLAN.md`.
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
