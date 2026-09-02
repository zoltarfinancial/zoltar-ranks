# START HERE

You are the implementing agent for this repo. Read this file completely before
touching anything. It exists so there is exactly one right answer to "what do I
do first."

## The one-paragraph version

Andrew runs a quant ranking pipeline that publishes stock ranks to
`github.com/apod-1/ZoltarFinancial` several times a day. Git holds only ~1
snapshot/day before 2026-08-19 — **not** because a rolling buffer destroyed the
rest, but because those intraday files were moved to offline SSD and never
committed (F2, closed). This repo captures the history into a local
point-in-time archive, then uses it to answer one question: *given a rank
produced at time T, when should the buy actually happen?* The archive is
**built, populated and harvesting on a 30-minute schedule**. Work starts at
Phase 2 (market data). Read "What the last session actually fixed" and
"F2 is CLOSED" below before you touch anything.

## Read these, in this order

| # | File | What it gives you |
|---|------|-------------------|
| 1 | `docs/FINDINGS.md` | Verified facts about the upstream data. **Do not re-derive these** — they were measured against the live repo, and re-deriving them costs hours and gigabytes. |
| 2 | `docs/PLAN.md` | The phased build plan. Section 0 contains ten rules you must not break. |
| 3 | `AGENTS.md` | Working agreement, layout, conventions. |
| 4 | `docs/HYPOTHESES.md` | The pre-registration register. Every experiment gets a row here *before* it runs. |
| 5 | `docs/DASHBOARD.md` | The one file you hand to the dashboard workstream, and the schema it expects. |

`CLAUDE.md` is loaded automatically at the repo root and carries the ten
non-negotiable rules in condensed form, so they stay in context for the whole
session. It is a summary of `docs/PLAN.md` §0, not a substitute for reading it.

## Where things stand — updated 2026-09-02 (reflects commit `a0f9317`)

Do not re-do these. Previous sessions already ran them.

| Done | Detail |
|---|---|
| ✅ Repo initialised and **pushed through `a0f9317`** | `origin` = `github.com/zoltarfinancial/zoltar-ranks`, branch `main`. `origin/main == a0f9317`. |
| ✅ venv + dependencies | `.venv` on **Python 3.14.7** (the only interpreter on this box), `pip install -e .` done. The pins resolve on 3.14 — nothing loosened. |
| ✅ **All tests pass, network included** | Contract tests green, `test_upstream_is_point_in_time` among them. Remaining skips are the Phase 2 `test_reconcile_*`. |
| ✅ **The archive is POPULATED** | `data/zoltar.duckdb`. Numbers below. |
| ✅ **The 30-min harvester is scheduled and ALIVE** | `ZoltarRanksHarvest`, 07:00–21:30 every 30 min, on a **`-Daily`** trigger. |
| ✅ Idempotency verified | Manual re-run and scheduled run both: `staged=866402 inserted=0`, all table counts unchanged. |
| ✅ `run_sessions` built | 1,026 rows / 513 build stamps, from `*_rankings_*` **filenames only** (Rule 4 untouched). The only external check on `classify_run()`. |
| ✅ Stamp-convention cutover handled | `ranks_pit.stamp_convention`, keyed on **`available_at`**. Three canary tests in `tests/test_stamp_cutover.py`. |
| ✅ Exit-0-on-incomplete killed repo-wide | `ingest/incomplete.py` + `tests/test_incomplete.py`. |
| ✅ Dashboard workstream started | `dashboard/` exists and is owned by the Cowork session — **do not edit it** |
| ✅ `.env` created | From `.env.example`, **still empty** — fill in credentials before the Robinhood/Alpaca providers. yfinance needs none. |

### What the last session actually fixed (commit `a0f9317`)

1. **The scheduled task was DEAD, and it was not the network.** `schedule_harvest.ps1`
   used `New-ScheduledTaskTrigger -Once -At 7:00AM` with a 14h30m
   `RepetitionDuration`. A `-Once` trigger repeats for that duration **on one day**
   and then expires. It ran 2026-09-01 07:00–21:30 and never scheduled again, while
   `State` still read `Ready`. Now `-Daily`, carrying the repetition via
   `$trigger.Repetition`; `NextRunTime` is populated.
   **A task reporting `Ready` with a blank `NextRunTime` is dead** — check
   `NextRunTime`, never `State`.
2. **Stamp-convention cutover, keyed on `available_at`, not `run_ts`.** Keying on
   `run_ts` labelled the last two forward-stamped runs (`2026-09-02 00:00:00` and
   `19:34:49`, both built 09-01) as `honest` — exactly the contamination the column
   exists to prevent. Archive is currently **871 forward / 0 honest**.
3. **Canonical vocabulary: EVENING RETRAIN.** `run_kind='nightly'`,
   `run_kind='placeholder'` and the upstream label `AFTERCLOSE UPDATE` are one
   event. `placeholder` is an artifact of the forward stamp and carries **no**
   tradability verdict. The early-AFTERCLOSE cluster (15:27–15:31) is the day's
   **final intraday re-score**, not a retrain — it belongs to **H12a**, and the
   `evening_retrains` view excludes it. H12b is defined against that view, never
   against `run_kind`.
4. **Exit-0-on-incomplete is now a repo-wide invariant.** All four harvesters did
   `log.warning(...); continue` then `return 0`. `tests/test_incomplete.py` enforces
   it structurally and behaviourally.

### F2 is CLOSED — the intraday history is not gone

⚠️ **A previous version of this file said the rolling buffer had destroyed the
pre-2026-08-19 intraday history and that it "will never improve." That explanation
is RETRACTED.** See `docs/FINDINGS.md` §F2.

The real cause is operational: the intraday files grew too large for Andrew's app,
so they were **moved to offline SSD** and were never committed. **The files exist.**
Consequences:

- **Phase 6's intraday range is PROVISIONAL, not capped at 2026-08-19.** It is
  limited *until the offline archive is ingested*, and is expected to improve.
- **H12a's underpowered verdict is provisional too** — scope its power warning that
  way rather than treating ~9 dense days as the ceiling.
- Those files were written by the pipeline at the time and then moved, not
  regenerated, so the point-in-time guarantee holds. **But the move may have reset
  mtimes**, so `available_at` must never be derived from mtime — the run timestamp
  is in the filename. See `docs/LOCAL_ARCHIVE_GATE.md` §2.

The harvester lapsing still costs live intraday history going forward, so the
scheduler is still the most perishable thing in the project.

### Rule 5: never key execution off `run_ts`

Use the **`ranks_pit`** view, which carries `available_at` — the information
timestamp, and the only one rule 3's latency may be measured from. Upstream stamps
some builds *later* than it published them (run `2026-09-02 19:34:49` was committed
`2026-09-01 19:36:25`), so `run_ts` is not a lower bound on knowability.
`run_kind` is descriptive only.

`ranks_pit.available_at` = `least(build_stamp, committed_at, run_ts)`, with
`availability_source` recording which bound won. It is a **view**, not columns on
`ranks`, because populating 1.25M rows would be an `UPDATE` (rule 2) — and because
`committed_at` alone would be 21–292 days stale for backfilled rows, `first_seen_sha`
being the first commit *harvested*, not the first that carried the row. Check
`availability_source` and `harvest_lag_days` before trusting it on historical rows.

### Answered by Andrew

- **`Cap_Size` is a model segment label, not literal market cap.** Use it to join
  SHAP segments. It is **not** valid as a size control variable in Phase 5.
- **Intraday tz is America/Chicago**; the schema comment was stale and is fixed.
- **The nightly build is a full retrain**, not a placeholder — see F4.
- **F2 is operational** (offline SSD), not a buffer cap.

| Still open | Blocks |
|---|---|
| **`Close_Price` split-adjusted?** Unknown. Proceeding on the conservative assumption that it is **unadjusted**: build `corporate_actions` first, join it before computing any return, and report what the reconciliation shows. This is diagnostic (c) of the Phase 2 alignment anchor. | Phase 2 — and every return in the repo |
| **Which `Strategy_Play_v*.py` is the live Streamlit engine?** (~40 candidates) | Phase 3 — `BASELINE_ASIS` cannot be a faithful replica without it |
| Is a ~9.9%/yr MDE (H11) above the effect worth acting on? | Whether H11 is `powered` or `underpowered` |

### What the archive actually holds — 2026-09-02 11:25, after the completed backfill

| feed | run ts | rows | first | last |
|---|---|---|---|---|
| `daily` | 235 | 557,984 | 2025-10-01 07:46:57 | 2026-09-03 00:00:00 |
| `all` | 306 | 715,304 | 2026-05-18 15:09:29 | 2026-09-02 10:52:07 |
| `daily_ranks` | 339 | 794,341 | 2026-03-03 20:18:25 | 2026-09-02 00:00:00 |

**880 distinct run timestamps** in union, **2,067,629 rows**. By class: 486
intraday, 220 morning, 142 nightly, 32 placeholder. `expected_returns` 44 as-of
dates; `shap_summary` 327 snapshots; `run_sessions` 1,038 rows / 519 build stamps.

Runs/day: sparse before 2026-08-19 at median **1**; dense from it at median ~15.
Per F2 that sparsity is operational (offline SSD), not a cap.

✅ **The daily_ranks backfill is COMPLETE.** Re-run 2026-09-02 11:19-11:23 once
GitHub was reachable: 228 files, `staged=54,858,681 inserted=0`, exit 0, no
INCOMPLETE line. The 8 previously-missing files (2026-07-21/22/23) are now in
`harvest_manifest` and contributed **zero** new rows -- the `all_*` buffers overlap
so heavily that their run timestamps were already covered. Idempotency holds.

### ✅ Fixed 2026-09-02: the 30-minute tick was doing 290s of pointless work

`harvest_daily_ranks.main()` parsed `--mode` and **never read it**, so both modes
re-read every `daily_ranks/` build on every tick: **234 blobs, ~4.8 GB, ~866k rows
staged, 0 inserted** -- **290.6 s of a 309.5 s run (94%)**, 29 times a day, on the
box running the live model re-scores.

Fixed via **`ingest/manifest.py`**, a shared helper alongside `incomplete.py`:
`manifest.unread()` filters candidates through `harvest_manifest` **before** any
blob is opened. **Measured after: 290.6 s -> 2 s, 0 blobs read.**

**The lesson worth carrying: row-idempotency is not work-idempotency.**
"staged=866402 inserted=0, counts unchanged" proves the ROWS are idempotent and
says nothing about the WORK. That is now **rule 10** in `CLAUDE.md` / `PLAN.md`
§0, and `tests/test_manifest.py` asserts on the instrumented blob-read **count**.

Audit of the others: `harvest_er` and `harvest_shap` were already correct;
`harvest_ranks` reads 4 HEAD blobs (13.9 s) and is bounded, left alone;
`harvest_sessions` ignores `--mode` too but opens no blob at all. Detail and the
per-tick cost table are in `docs/SESSION_LOG.md`.

### ✅ The build monitor is wired: `--check` is 2 blocking, was 10

`docs/HANDOFF_CLAUDE_CODE.md` is the task list; `dashboard/emit_build_status.py
--check` is the sync point between you, Andrew and the Cowork session. Run it
instead of writing a status update.

```powershell
python dashboard\emit_build_status.py --check     # 2 blocking, 1 warning, 15 ok
```

Done: `daily.py` stamps a real window mode and appends `run_history.jsonl` in a
`finally` block; the emitter runs from `daily.py` and from `.git/hooks/post-commit`;
`pytest_report.json` feeds the gates; the manifest's paths match the plan;
`analysis/export_dashboard_data.py` writes the research feed every run.

**The 2 remaining blockers are a CLOCK, not a code change.** `harvest_premarket`
and `harvest_evening` have no beat yet because the history began at 12:27, inside
the intraday window. The scheduled task fills them tonight and tomorrow morning.
**Do not run `daily.py --mode premarket` to clear them** -- that fabricates the
exact evidence the monitor exists to check.

Two things deliberately left undone, both recorded in `docs/SESSION_LOG.md`:
`no_same_bar` and `no_run_ts_execution` (no execution engine to guard, so a test
today would pass because the code does not exist), and `monitoring/`, whose
deletion the sandbox blocked -- **Andrew: `Remove-Item -Recurse -Force monitoring`**.

### ✅ The suite is green, and `placeholder` is settled

**`pytest tests -q` is 125 passed, 5 skipped, 0 failed.** The stamp canary that
was failing is fixed -- by correcting the assertion's POPULATION, not by
loosening it. Read `docs/FINDINGS.md` F4 before touching any of this.

**`run_kind='placeholder'` is a forward-stamped "latest" POINTER.** Measured
2026-09-02: the midnight row is an exact duplicate of the publishing blob's
newest intraday re-score (2326/2326 on score and on close_price), and upstream
**rewrites it every ~30 minutes** -- three commits of the same file agreed 0/1163
with each other, each matching its own blob's newest re-score. It is not the
evening retrain, and not a model output.

Three things that follow, all load-bearing:

1. **F1's "scores are never restated" does NOT hold for this run timestamp.**
   `ranks` is append-only, so we kept whichever version we harvested first and
   dropped every later rewrite. The midnight `run_ts` is a **label**, not an
   observation time. F1 stands for the `daily` feed, where it was measured.
2. **Every placeholder row double-counts** a scoring already in the archive under
   its true stamp. Exclude them from anything that groups by run.
3. **`evening_retrains` now excludes them** -- 138 genuine `nightly` runs. **H12b
   is defined against that view** and is no longer a blend of two processes.

**The stamp cutover is still UNTESTED.** No post-cutover evening retrain has
landed; the first arrives ~19:36 and `test_no_third_stamping_convention` is armed
for it. An earlier version of this file said the cutover "did not happen" -- that
was overstated, and is corrected in F4.


## Do this first

Setup and backfill are **done** — do not re-run them expecting work. Confirm the
machine is still healthy, then start Phase 2:

```powershell
cd C:\Shared\ClaudeWork\zoltar-ranks
.\.venv\Scripts\Activate.ps1

pytest tests -q                                              # expect 125 passed, 5 skipped
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

1. **Phase 2 — market data.** ⚠️ **Read `docs/ALIGNMENT_ANCHOR.md` first.** It is
   the spec for the measurement that must precede any provider method: the ranks
   are tz-naive America/Chicago, the providers return UTC or ET, and a one-hour
   misalignment would not degrade the timing study's 0.157% MDE — it would
   MANUFACTURE a large, clean, spurious result that passes every test in the repo.
   The anchor is the concrete definition of the four `test_reconcile_*` stubs, and
   `yfinance` alone covers all three parts, so it needs no credentials.
   Then the provider adapters (`robin_stocks` primary, Alpaca for minute bars,
   yfinance as cross-check) and corporate actions. Nothing downstream is
   trustworthy without this.
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
