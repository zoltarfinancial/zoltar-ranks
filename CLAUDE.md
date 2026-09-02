# zoltar-ranks — project instructions

**First action in a new session: read `START_HERE.md`** — its "Where things
stand" table says what the last session actually finished, so you neither redo
completed work nor assume work that was never run. Then `docs/FINDINGS.md`,
`docs/PLAN.md`, `AGENTS.md`, `docs/HYPOTHESES.md`, and `docs/SESSION_LOG.md`.

This is a quantitative research repo, not a normal application. The failure mode
here is not a crash — it is a backtest that looks excellent and loses real money.
The rules below exist to prevent that and are not negotiable.

## What this project is

Andrew runs a stock-ranking pipeline that publishes to `apod-1/ZoltarFinancial`
three times a day. This repo archives that output point-in-time, benchmarks it
honestly, and answers: **given a rank produced at time T, when should the buy
happen?** Phase 1 (archive) is built and validated. Work starts at Phase 2.

## Non-negotiable rules

1. **Upstream `apod-1/ZoltarFinancial` is read-only.** Never push to it, never
   write into a clone of it.
2. **The `ranks` table is append-only.** Never `UPDATE`. A changed historical
   score is a finding to report, not a row to correct.
3. **No same-bar execution.** Every simulated fill occurs strictly after the
   timestamp of the information that triggered it, plus a configured latency.
   A latency of 0 is a bug, not a default.
4. **Never backtest on `*_rankings_latest.pkl`.** It contains `source='train'`
   rows scored by the current model. Diagnostics only. The archive built from
   `*_PROD_*` is the only honest source.
5. **`run_kind='placeholder'` rows are not tradable at their timestamp.**
   Upstream stamps the previous evening's build with the next day at 00:00:00.
6. **Every result reports an uncertainty interval.** Use
   `analysis/stats.compare_strategies`, never a bare t-test on daily returns.
   With ~230 trading days, a difference smaller than its bootstrap CI is not a
   finding.
7. **Pre-register every hypothesis in `docs/HYPOTHESES.md` before testing it**,
   with its success criterion. Rejected hypotheses stay in the register — the
   row count is the denominator for the FDR correction.
8. **No look-ahead in universe construction.** Only symbols present in the
   archive as of the decision timestamp are eligible.
9. **If a contract test fails, stop and report.** Do not loosen the assertion.
   `test_upstream_is_point_in_time` failing means the archive's core assumption
   is broken and everything downstream is suspect.

## Two workstreams — stay in your lane

A second session (Cowork) owns `dashboard/`. You own everything else. You meet
at exactly one file: `data/results/dashboard_data.json`, which you produce via
`analysis/export_dashboard_data.py` (append it to `scripts/daily.py::STEPS`).
Read `docs/DASHBOARD.md` for the schema. Never edit anything under `dashboard/`;
never change the JSON schema without updating `docs/DASHBOARD.md` first.

## Environment

- Windows, PowerShell. Repo root is `C:\Shared\ClaudeWork\zoltar-ranks`.
- Python venv at `.venv`. Activate before anything: `.\.venv\Scripts\Activate.ps1`
- `git` must be on PATH — the harvester shells out to it.
- DuckDB (`data/zoltar.duckdb`) is the system of record. Parquet under
  `data/archive/` is an export; never edit it directly.
- Timestamps in the archive are **tz-naive America/Chicago wall clock**, matching
  what upstream writes. Convert at the edges, never in the middle.
- Credentials live in `.env` (see `.env.example`). `.env` is gitignored and must
  never be committed or printed into logs, results, or commit messages.

## Commands

```powershell
.\scripts\setup.ps1                  # first time: venv, deps, tests, backfill
.\scripts\schedule_harvest.ps1       # first time, elevated: the 30-min task
pytest tests -q -m "not network"     # fast loop while developing
pytest tests -q                      # full, includes upstream contract tests
python scripts\daily.py              # the scheduled loop
```

## Before you finish any task

- `pytest tests -q` passes.
- `docs/SESSION_LOG.md` has a new entry: decisions, surprises, workarounds — not
  routine progress. And `START_HERE.md`'s "Where things stand" table reflects
  reality, so the next session starts from the truth.
- Re-running a harvester inserts **zero** new rows (idempotency holds).
- Any new pipeline step is appended to `scripts/daily.py::STEPS`.
- Any new upstream dependency has a contract test that fails loudly on drift.
- Any claim about performance carries a confidence interval and a note on how
  many configurations were searched to find it.

## Ask, do not assume

Three things are unverified and flagged in `docs/FINDINGS.md` §F7: what
`Cap_Size` actually means, whether `Close_Price` is split-adjusted, and which of
the ~40 `Strategy_Play_v*.py` files upstream is the live Streamlit engine.
Ask Andrew rather than guessing — each wrong guess silently corrupts a whole
phase of results.
