# Working agreement for agents on this repo

Read `START_HERE.md` first, then these, in order:

1. `docs/FINDINGS.md` — verified facts about the upstream data. Do not re-derive.
2. `docs/PLAN.md` — the phased build plan and the rules in section 0.
3. `docs/HYPOTHESES.md` — the live register of what is being tested.

## Ground rules

- **Upstream (`apod-1/ZoltarFinancial`) is read-only.** Never push, never open a
  PR, never write a file into a clone of it.
- **The archive is append-only.** No `UPDATE` on `ranks`. If upstream restates a
  score, that is a finding — write it up, do not overwrite.
- **No same-bar execution in any simulation.** Fills happen strictly after the
  information timestamp plus a configured latency. A latency of 0 is a bug.
- **Pre-register before you test.** Add the hypothesis and its success criterion
  to `docs/HYPOTHESES.md` *before* running the experiment, and count it toward
  the multiple-comparison correction. Do not delete rejected hypotheses.
- **Every number ships with an uncertainty interval.** Use
  `analysis/stats.compare_strategies`, never a bare t-test on daily returns.
- **Secrets live in `.env` only.** Never commit it, never echo it into logs,
  results, or commit messages. `.env.example` documents what is needed.
- **If a contract test fails, stop.** `tests/test_harvest.py::test_upstream_is_point_in_time`
  failing means the archive's core assumption is broken. Report it; do not
  relax the assertion.

## Layout

```
src/zoltar_ranks/
  config.py            Config dataclass + config/config.yaml overrides
  sources/git_archive.py   blobless mirror of upstream; point-in-time blob reads
  sources/prices.py        Phase 2  — interface + cache BUILT, providers NOT
  ingest/harvest_ranks.py  Phase 1  — built and validated
  ingest/harvest_er.py     Phase 1a — built and validated
  ingest/harvest_shap.py   Phase 1a — built and validated
  ingest/harvest_reference.py  (Phase 1a, not built)
  db/schema.sql            DuckDB schema — the contract
  db/duckdb_io.py          connect / upsert_new_rows / export_parquet
  analysis/               metrics, stats, backtest engine
scripts/daily.py             the scheduled job; append later phases to STEPS
scripts/init_repo.ps1        one-time git init + remote
scripts/setup.ps1            one-time Windows bootstrap
scripts/schedule_harvest.ps1 registers the 30-minute scheduled task
data/                    gitignored: duckdb file, parquet archive, caches, results
```

## Conventions

- All timestamps in the archive are **tz-naive America/Chicago wall clock**,
  matching what upstream writes. Convert at the edges, never in the middle.
- DuckDB is the system of record; Parquet under `data/archive/` is an export for
  portability. Never edit Parquet directly.
- Long-running steps must be resumable and idempotent — check the manifest, skip
  what is done, and make re-running a no-op.
- Results are written to `data/results/` as self-contained HTML plus the
  underlying `.parquet`, so a number in a report can always be traced to rows.

## Commands

```powershell
.\scripts\setup.ps1                          # first time: venv, deps, tests, backfill
.\scripts\schedule_harvest.ps1               # first time: register the 30-min task
pytest tests -q -m "not network"             # fast
pytest tests -q                              # includes upstream contract tests
python scripts\daily.py                      # the full loop (what the task runs)
python scripts\daily.py --backfill           # re-run every harvester in backfill mode
```

Individual harvesters, if you need one alone:

```powershell
python -m zoltar_ranks.ingest.harvest_ranks --mode backfill --export-parquet
python -m zoltar_ranks.ingest.harvest_er   --mode backfill
python -m zoltar_ranks.ingest.harvest_shap --mode backfill
```
