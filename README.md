# zoltar-ranks

A repeatable research loop around the Zoltar Financial ranking process: a
point-in-time archive of every rank ever published, an honest benchmark, bias
diagnostics, and a hypothesis-testing framework — built to answer one question
first: **given a rank produced at time T, when should the buy actually happen?**

## Why

The upstream production process publishes ranks three times a day (morning
build, ~17 intraday re-scores, nightly placeholder) into
`apod-1/ZoltarFinancial`. The intraday history is stored in a rolling buffer
capped at 200 run timestamps and is **permanently destroyed** roughly every ten
trading days. This repo captures it before that happens, and turns it into a
measurable research asset.

See `docs/FINDINGS.md` for what was verified about the upstream data,
`docs/PLAN.md` for the build plan, `AGENTS.md` for the working agreement.

## Quick start

**Agents: read `START_HERE.md` first.**

```powershell
cd C:\Shared\ClaudeWork\zoltar-ranks
.\scripts\setup.ps1              # venv, deps, contract tests, one-time backfill
.\scripts\schedule_harvest.ps1   # elevated; every 30 min, 07:00-21:30
```

The scheduled harvest is the single most time-sensitive step in the project —
upstream destroys intraday history roughly every ten trading days.

## Status

| Phase | State |
|---|---|
| 1 — rank archive | built, validated against live upstream |
| 1a — expected returns + SHAP ingestion | built, validated |
| 1a — reference data (fundamentals, ratings) + scheduler | next |
| 2 — market data layer | not started |
| 3 — benchmark replication | not started |
| 4 — statistics framework | not started |
| 5 — bias diagnostics | not started |
| 6 — execution timing study | not started |
| 7 — hypothesis loop | register seeded |
| 8 — dashboard | not started |
