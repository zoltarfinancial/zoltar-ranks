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

- **Found a forward-stamped build that `classify_run` does not flag.** Run
  `2026-09-02 19:34:49` (2,330 rows) was committed `2026-09-01 19:36:25` — 24h
  forward, exactly FINDINGS F4's behaviour, but at 19:34:49 rather than
  `00:00:00`, so `classify_run` labels it `nightly` and zero rows archive-wide
  are `placeholder`. I raised this as a rule-5 gap; see the rule 5 entry below
  for what it actually turned out to mean, which was not what I assumed.

- **Scheduler registered and proven.** `ZoltarRanksHarvest`, every 30 min
  07:00–21:30, `RunLevel: Limited`, `UserId: owner`. Triggered a real run:
  `LastTaskResult 0`, all steps ok. Idempotency verified twice (manual re-run
  and scheduled run): `staged=866402 inserted=0`, all five table counts
  unchanged.

- **Pins resolved on Python 3.14 — nothing loosened.** They forced
  `pytest` 9.1.1→8.4.2 and `yfinance` 1.7.0→0.2.66 (plus `frozendict`); both work
  on 3.14 and the full suite passes. `requirements.txt` left exactly as pinned.

- **Timezone contradiction found and resolved.** `db/schema.sql` documented
  `prices_intraday.ts` as `America/New_York` while `sources/prices.py`
  (`MARKET_TZ`), CLAUDE.md and AGENTS.md all said `America/Chicago`. Comment
  only, but on the column the fill-timing study keys off, where a one-hour error
  would be invisible. Andrew confirmed the code governs; comment corrected.

- **Rule 5 rewritten on Andrew's instruction — no relabeling.** My framing was
  wrong: I read forward-stamping as a tradability trap. It is the opposite. A
  rank published 19:36 CT is actionable in extended hours ~13h before the next
  regular open, so the nightly vintage is a strategy candidate. `placeholder`
  was never widened; it stays descriptive. Rule 5 is now *"no execution decision
  may key off `run_ts`; `available_at` is the information timestamp for rule 3."*
  Updated in CLAUDE.md, PLAN.md R5, AGENTS.md.

- **`available_at` shipped as the `ranks_pit` VIEW, not columns on `ranks`.**
  Two reasons, both worth knowing:
  1. Populating a new column on 1.25M existing rows is an `UPDATE`, which rule 2
     forbids. The view derives it from `first_seen_sha JOIN
     harvest_manifest.committed_at` — cannot drift, costs nothing.
  2. **`committed_at` alone would have been badly wrong as `available_at`.**
     `first_seen_sha` is the first commit *we processed* containing the row, not
     the first commit that *carried* it — the coverage walk reads 2-4 blobs, not
     all 405. Only two distinct shas exist across all 1.25M rows. Using
     committed_at directly would claim a 2025-10-01 rank became available
     2026-07-20: median lag 21 days, worst 292. Execution logic keyed on it
     would collapse the entire archive onto two dates.
  So `ranks_pit` sets `available_at = committed_at` **only** where
  `stamp_is_forward` (2,330 rows — exact, the publication time), and `run_ts`
  otherwise (1,252,346 rows), exposing `availability_source` and
  `harvest_lag_days` so the assumption is visible rather than buried. The
  `run_ts` branch assumes upstream publishes promptly after a build, which
  FINDINGS F4 supports but this archive cannot verify at 2 blobs. Rows harvested
  prospectively from now on will have a tight committed_at, so this improves on
  its own.

- **H11 pre-registered with its MDE, before any Phase 6 machinery.**
  MDE = **0.157%/run** (n=63 usable of 64 nightly runs; sd of the paired
  overnight basket move 0.445%, winsorized 0.422% -> 0.149%). ~9.9%/yr
  cumulative. Left `proposed`, not `powered`: whether 9.9%/yr clears "worth
  acting on" is Andrew's threshold. The EH cost model will only raise the MDE,
  and spreads on thin names plausibly exceed it outright — derivation and three
  caveats in HYPOTHESES.md.

- **Phase 2 scope grew (PLAN 2a-bis):** `prices_intraday.session`
  (pre|regular|post), real extended-hours bars from whichever provider serves
  them, and `symbol_venue.extended_hours_eligible` per ticker/provider. H11 is
  unrunnable without all three.

- `schema.sql` intraday tz comment corrected to America/Chicago (Andrew
  confirmed the code governs). Comment only, no behaviour change.

- **`Cap_Size` is a model segment label** (Andrew confirmed), *not* literal
  market cap. Use it to join SHAP segments; it is **not** valid as a size
  control variable in Phase 5. `Close_Price` split-adjustment is still unknown —
  treating it as unadjusted (conservative), building `corporate_actions` first,
  and reporting what reconciliation shows before trusting any return.

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
