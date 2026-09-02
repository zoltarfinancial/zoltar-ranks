# Session log

Append one entry per working session, newest at the top. Keep it short — this is
how the next session learns what happened without re-deriving it. Record
decisions, surprises, and anything you had to work around; not routine progress.

---

## 2026-09-01 — Claude Code session: archive populated, scheduler live

(Machine clock and upstream commit timestamps both read 2026-09-01; the two
entries below are dated 2026-09-02. Ordering here is by actual clock time, so
this entry is newer than it looks.)

- **The backfill was blocked by a schema-parsing bug — fixed.** Every harvester
  died in `duckdb_io.connect()` with `Parser Error: syntax error at end of
  input`. `split_statements` dropped only lines *beginning* with `--`, so the
  trailing comment on `corporate_actions.ratio` —
  `-- split ratio (new/old); NULL for dividends` — was split on the semicolon
  **inside its own comment**, truncating the CREATE TABLE mid-column-list.
  `corporate_actions` and the `morning_ranks` view were therefore never created.
  The splitter is now comment- and string-aware.
  - **Why no test caught it:** nothing in the suite ever executed `schema.sql`.
    Added `tests/test_db.py` (10 tests) covering the regression, quoting/escape
    cases, that `connect()` creates all 8 tables + the view, and that
    `upsert_new_rows` is append-only. Assert against the **real** schema.sql —
    a fixture copy would not contain the offending comment.

- **Archive backfilled and verified.** `daily` 233 run timestamps back to
  2025-10-01 (expected ~234 — the coverage walk ran), `all` 300 back to
  2026-05-18. ER 43 as-of dates, SHAP 322 snapshots. Full numbers in
  `START_HERE.md`.

- **⚠️ Only ~10 trading days of genuine intraday granularity exist.** The `all`
  feed holds **1 run/day up to 2026-08-18**, and 9–16/day only from 2026-08-19
  onward. Total is 300 run timestamps, not the 376 in FINDINGS F3, and the
  shortfall is density, not a date gap (no calendar gap >4d). The rolling buffer
  has already eaten the intraday history that the Phase 6 timing study needs —
  what is on disk now is very nearly all there will ever be for dates before
  2026-08-19. **This makes the 30-minute harvester the whole ballgame.** Do not
  let it lapse. Not re-derived against upstream; reported as measured.

- **⚠️ FINDINGS F4's forward-stamped build is NOT being caught by `classify_run`.**
  Run `2026-09-02 19:34:49` (2,330 rows) was committed `2026-09-01 19:36:25` —
  stamped 24h forward, exactly the F4 trap, but at 19:34:49 rather than
  00:00:00. `classify_run` keys `placeholder` on *exactly* `00:00:00`, so this
  landed as `run_kind='nightly'` and **CLAUDE.md rule 5 does not protect it.**
  Zero rows in the whole archive are classified `placeholder`. The manifest
  already carries `committed_at`, so `run_ts > committed_at` is a stronger and
  available invariant. **Not changed — flagged for Andrew**, since redefining a
  run class silently re-labels every downstream decision point.

- **Scheduler registered and proven.** `ZoltarRanksHarvest`, every 30 min
  07:00–21:30, `RunLevel: Limited`, `UserId: owner`. Triggered a real run:
  `LastTaskResult 0`, all steps ok. Idempotency verified twice (manual re-run
  and scheduled run): `staged=866402 inserted=0`, all five table counts
  unchanged.

- **Pins resolved on Python 3.14 — nothing loosened.** They forced
  `pytest` 9.1.1→8.4.2 and `yfinance` 1.7.0→0.2.66 (plus `frozendict`); both work
  on 3.14 and the full suite passes. `requirements.txt` left exactly as pinned.

- **Inconsistency not acted on:** `db/schema.sql` documents
  `prices_intraday.ts` as `America/New_York`, while `sources/prices.py`
  (`MARKET_TZ`), CLAUDE.md and AGENTS.md all say `America/Chicago`. Comment only,
  but it sits on the column the fill-timing study keys off, where a one-hour
  error would be invisible. Left for Andrew to confirm before Phase 2 writes
  bars into that table.

- `init_repo.ps1` still uses `Read-Host` (noted last session); steps were run
  manually again.

## 2026-09-02 — Cowork session: dashboard workstream + backfill performance

- Built `dashboard/` — self-contained HTML research console, dark terminal-quant.
  Reads `data/results/dashboard_data.json`; falls back to embedded seed data with
  a loud amber banner. **Owned by the Cowork session — the backend session must
  not edit it.**
- Wrote `docs/DASHBOARD.md`: the JSON schema and the ownership split. The backend
  deliverable is `analysis/export_dashboard_data.py`, appended to
  `scripts/daily.py::STEPS`.
- Pinned `requirements.txt` with upper bounds. Verified on python 3.11 /
  pandas 3.0.2 / numpy 2.4.4 that every upstream pickle in `production/` reads
  cleanly with correct dtypes and zero nulls — **pandas 3 is not a problem for
  this repo**. Python 3.14 specifically is still unverified.
- **Backfill performance fix.** The ER and SHAP harvesters read ~1,000 distinct
  small blobs, and a blobless clone fetches each lazily. Measured against
  upstream: 0.45 s per lazy read (~8 min total) vs. one bulk
  `git fetch --refetch --filter=blob:limit=600k` at 10-15 s, after which reads
  take ~7 ms — **63× per read**. `--mode backfill` now does the bulk fetch by
  default; `--no-prefetch` opts out. Cost: the gitignored mirror under
  `data/cache/` grows ~1 MB → ~328 MB.
  - Blob-OID dedup was evaluated and rejected: 377 commits on
    `er_for_last_date_live.pkl` produce 377 distinct blobs, so there is nothing
    to dedup. Do not re-investigate.

## 2026-09-02 — Claude Code session: environment bootstrap

- `git init`, first commit `be60f19`, `origin` set to
  `github.com/zoltarfinancial/zoltar-ranks`, pushed. Repo-local `user.name` /
  `user.email` were set because they were unset globally.
- `.venv` created on **Python 3.14.7** (the only interpreter on the box),
  `requirements.txt` + `pip install -e .` installed.
- Offline tests: 13 passed, 8 network tests deselected.
- **Not run:** `setup.ps1` (so no backfill — the archive is empty),
  `schedule_harvest.ps1`, and the 8 network contract tests.
- `init_repo.ps1` uses `Read-Host`, which does not work in a non-interactive
  shell. The steps were run manually instead. Worth fixing if it comes up again.

## 2026-09-01 — Cowork session: reconnaissance and scaffold

- Reverse-engineered the upstream data contracts against the live repo. All
  findings and their evidence are in `docs/FINDINGS.md` — do not re-derive them.
- Built and validated Phase 1 (`harvest_ranks.py`) and Phase 1a
  (`harvest_er.py`, `harvest_shap.py`) against real blobs.
- Key design decision: the backfill uses a **coverage walk** (fetch HEAD, binary
  search for the oldest commit whose blob still reaches back to what HEAD covers)
  rather than sampling commits per day. 2-4 fetches per file instead of 68, and
  it recovers 3 extra months of daily history and 6 extra weeks of intraday that
  HEAD alone does not contain.
