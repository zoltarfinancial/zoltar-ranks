# Session log

Append one entry per working session, newest at the top. Keep it short — this is
how the next session learns what happened without re-deriving it. Record
decisions, surprises, and anything you had to work around; not routine progress.

---

## 2026-09-02 (later still) — harvest_daily_ranks re-read 4.8 GB every 30 min

- **🔴 DEFECT CONFIRMED (Andrew's diagnosis, verified).** `harvest_daily_ranks.main()`
  parsed `--mode` and **never read `args.mode`** -- grep confirms one occurrence,
  the `add_argument` call. Both modes ran `_read_all()` over every file from
  `added_prod_files()`: **234 files, ~21 MB each (~4.8 GB), 234 unpickles of
  ~233k-row frames, ~866k rows staged, 0 inserted -- on every 30-minute tick.**
  `last_run_status.json` shows what that cost: `daily_ranks` **290.6 s of a
  309.5 s** run, i.e. **94% of the tick**, 29 times a day, on the box running the
  live model re-scores. And it grew linearly with every new build.

- **Why our idempotency check missed it: row-idempotency is not
  work-idempotency.** "staged=866402 inserted=0, table counts unchanged" verifies
  the ROWS are idempotent and says nothing about the WORK. Same shape as the dead
  scheduler -- the field we checked was not the field that mattered.

- **Fix: `ingest/manifest.py`, a shared helper alongside `incomplete.py`.**
  `manifest.unread(con, candidates, key=...)` filters a candidate list through
  `harvest_manifest` **before** any blob is opened. The skip is exact, not a
  heuristic: the manifest PK is `(file_path, commit_sha)` and a row is written
  only after a successful read. `daily_ranks/` files are immutable once added, and
  `production/*_latest.pkl` is rewritten in place but keyed per-commit, so the
  same helper is correct for both shapes.

- **MEASURED after the fix: 290.6 s -> 2 s, 0 blobs read, exit 0.** Steady-state
  cost is now dominated by the git fetch, not by reading.

- **Both traps handled, and both are tested.**
  1. The zero-file guard stays on files **discovered** (`if not files: return 1`),
     never on files **to read** -- after the skip, "nothing new" is the normal
     outcome on ~26 of 29 ticks, and a guard there would fire constantly and stop
     guarding. `test_zero_new_files_does_not_trip_the_empty_source_guard` and
     `test_empty_upstream_still_fails_loudly` pin both sides.
  2. Failed reads still retry, because the manifest records **successes only**.
     `test_failed_read_is_retried_on_the_next_run` pins it. This is exactly how
     the 8 unreadable `all_high` files of 2026-07-21/22/23 were recovered; a skip
     built on attempts rather than successes would have lost them permanently.

- **`--strategy coverage` deliberately does NOT consult the manifest** and now
  says so loudly. The walk binary-searches the whole ordered list, so filtering it
  would break the search -- and it is a non-default escape hatch that is UNSOUND
  on this data anyway.

- **`exit_code(intended=)` now scopes to what THIS run set out to read**, not to
  everything ever discovered. "could not read 1 of 8" is actionable; "1 of 234" is
  misleading once the skip means we only ever intended 8.

### Audit of the other four harvesters

| harvester | reads `args.mode` | consults manifest before reading | blobs on a 2nd consecutive run |
|---|---|---|---|
| `harvest_daily_ranks` | **NO -> now yes** | **NO -> now yes** | **234 -> 0** (measured) |
| `harvest_er` | yes | **yes**, already correct | 0 |
| `harvest_shap` | yes | **yes**, already correct | 0 |
| `harvest_ranks` | yes | no -- but bounded, see below | 4 (one HEAD blob per rank file) |
| `harvest_sessions` | **NO** | n/a | 0 -- reads FILENAMES only (Rule 4) |

- **`harvest_er` and `harvest_shap` were already right**, and their
  `done = {sha...}` / `todo = [s for s in snaps if s.sha not in done]` pattern is
  what `manifest.unread()` generalises. They were the model, not the problem.
- **`harvest_ranks` is bounded and left alone.** Daily mode reads HEAD only, one
  blob per file in `cfg.rank_files` = **4 reads, 13.9 s measured**. It cannot use
  a plain manifest skip the way the others do, because `production/*_latest.pkl`
  is rewritten in place and HEAD is usually a genuinely new commit. A skip when
  HEAD's sha is already in the manifest would help on ticks where upstream has not
  pushed -- worth doing, but it is a 13.9 s step, not a 290 s one, so it is
  recorded here rather than done now.
- **`harvest_sessions` ignores `--mode` too, but harmlessly**: it parses
  `*_rankings_*` FILENAMES from `git log` and opens no blob at all. 1.4 s.
  Left as-is; the unused argument is noted so a future reader does not mistake it
  for the same defect.

### Expected per-run cost on the 30-minute schedule

Measured arrival rate: **6.7 daily_ranks files/day** in **2-3 pushes/day**, at
most **4 files in a single push**. Per-file cost: **0.29 s** `read_pickle` on a
cached blob, ~1.2 s including `normalize` + `upsert_new_rows`.

| tick | before | after |
|---|---|---|
| nothing new (~26 of 29/day) | 290.6 s | **2 s** (measured) |
| 2-4 new files (~3 of 29/day) | 290.6 s | ~5-15 s (2 s + per-file read, plus a first-time blob fetch) |
| **whole day** | ~8,400 s (~2.3 h) | **~100 s (~1.7 min)** |
| **blob reads/day** | 6,786 | **~7** |

~80x less work per day, and it no longer grows with the number of builds.

- **New tests: `tests/test_manifest.py`, 15 of them**, asserting on the
  instrumented blob-read **count**, never on rows or timing. Includes a structural
  guard (`test_harvester_consults_the_manifest_before_reading_pure`) so the next
  harvester inherits this the way it inherits `Incomplete`.

- **Rule 10 added** to `CLAUDE.md` and `docs/PLAN.md` §0 (now ten rules, and the
  references in `START_HERE.md` were updated to match).

---

## 2026-09-02 (later) — connectivity restored; the stamp cutover DID NOT HAPPEN

- **Connectivity is fully back, and the AVG TLS block is gone too.** TCP to
  `github.com:443` succeeds (140.82.113.4), `git ls-remote` works, and Python
  HTTPS to `query1.finance.yahoo.com` now returns **HTTP 429** rather than the
  2026-09-01 OpenSSL "Basic Constraints of CA cert not marked critical" error.
  429 is Yahoo rate-limiting, not interception -- so yfinance is unblocked but
  needs backoff-and-retry. Pushed `7791216` and `a0f9317`; `origin/main == a0f9317`.

- **Scheduler confirmed alive after the `-Daily` fix.** `LastTaskResult 0`,
  `NextRunTime 2026-09-02 11:30`, `NumberOfMissedRuns 0`.

- **daily_ranks backfill COMPLETE.** 228 files, `staged=54,858,681 inserted=0`,
  exit 0, no INCOMPLETE line. The 8 previously-missing files (2026-07-21/22/23)
  are now in `harvest_manifest` and added **zero** rows -- the `all_*` rolling
  buffers overlap so heavily that their run timestamps were already covered by
  neighbouring files. A completed backfill inserting 0 rows is the *expected*
  outcome here, not a sign the files were skipped; the manifest is the check.

- **🔴 THE 2026-09-02 STAMP CUTOVER DID NOT HAPPEN.**
  `tests/test_stamp_cutover.py::test_no_third_stamping_convention` fails. Left
  failing per rule 9. Upstream is still forward-stamping after the cutover date:
  `all_*_PROD_20260903_000000.pkl` has build stamp 2026-09-03 00:00:00 and was
  **committed 2026-09-02 08:41:57** -- forward by 15.3h. The only post-cutover
  files with `build_stamp <= committed_at` are the *morning* builds, which were
  never forward-stamped under either convention, so they are not evidence.
  **Caveat, and the reason nothing was changed:** it is 11:25 CT and evening
  retrains land 19:00-21:18, so the first post-cutover AFTERCLOSE has not landed
  yet. The cutover may be scheduled for tonight. Check this evening before
  touching `CUTOVER` or the view.

- **`stamp_convention` is a calendar cutoff, not a measurement of stamp
  behaviour.** It labels everything with `available_at >= 2026-09-02` as `honest`,
  which is how run `2026-09-03 00:00:00` is simultaneously
  `stamp_convention='honest'` and `stamp_is_forward=true`. 6 of the 7 `honest`
  runs are today's morning/intraday runs, which never had a forward stamp. The
  column measures **era**, not **convention**. Not fixed -- reported.

- **🔴 `placeholder` is a MORNING artifact, not the evening retrain. Contradicts
  F4.** All **32** midnight-stamped (`*_000000.pkl`) daily_ranks files were
  committed 08:16-14:46, 28 of 32 in 08:00-09:59 -- the morning window, never the
  evening. Decisive on the newest: `20260903_000000.pkl` was committed 2026-09-02
  08:41:57, so it cannot be the 2026-09-02 evening retrain, which does not exist
  until ~19:36 that evening. The evening retrain has its own separate
  forward-stamped form (`20260902_193449.pkl`), so a given day carries **two
  distinct forward-stamped artifacts**. `evening_retrains` unions both and
  therefore mixes two processes -- this lands on **H12b**. Not changed; the
  decisive confirmation is a payload comparison (does a midnight file equal the
  prior evening retrain, or that morning's build?).

- **`available_at` can overstate by hours on fresh rows, for the same reason it
  understates on backfilled ones.** Run `2026-09-03 00:00:00` carries
  `available_at 2026-09-02 10:54:02` because `first_seen_sha` points at the 10:54
  `production/*_latest.pkl` harvest -- but commit `70690e5` carried the same build
  at **08:41:57**. `first_seen_sha` is the first commit *harvested*, not the
  earliest that carried the row, and harvest order is not commit order.

- **Phase 2 spec written before any provider code: `docs/ALIGNMENT_ANCHOR.md`.**
  Defines the daily / intraday / adjustment anchors as the concrete content of the
  four `test_reconcile_*` stubs, with justified tolerances (5 bps daily, 25 bps
  intraday, plus a 3x power guard so a flat tape cannot launder a timezone bug
  into a green test). Three points worth carrying forward even if the spec
  changes: the **trading-calendar test must run FIRST** because "prior trade date"
  is undefined without it; the existing `test_reconcile_intraday_coverage` has
  **zero power** against a timezone error, since there is a bar every minute
  either way; and the anchor must run on **unadjusted** prices, which requires
  folding `returns_adjusted` into `_cache_path`'s key or a raw request is served
  cached adjusted bars.

- **The intraday anchor is time-critical.** yfinance serves 1-minute bars for ~30
  days; the dense era starts 2026-08-19, which is 14 days back. Once it falls out
  of that window the intraday anchor cannot be measured at 1-minute resolution
  until the offline SSD archive lands.

---

## 2026-09-02 — stamp cutover, vocabulary, exit-0 invariant; SCHEDULER WAS DEAD

- **🔴 The scheduled task had silently stopped, and it was not the network.**
  `NextRunTime` was **empty** with `NumberOfMissedRuns: 1`, while `State` still
  read `Ready`. Cause: `schedule_harvest.ps1` used
  `New-ScheduledTaskTrigger -Once -At 7:00AM` with a 14h30m `RepetitionDuration`.
  A `-Once` trigger repeats for that duration **on one day** and then expires --
  so it ran 2026-09-01 07:00-21:30 and never scheduled again. Fixed to `-Daily`
  carrying the repetition via `$trigger.Repetition`; `NextRunTime` is now
  populated. **A task reporting `Ready` with a blank `NextRunTime` is dead** --
  that is the thing to check, not `State`.

- **F2 RESOLVED (Andrew, pending SSD confirmation).** The pre-2026-08-19 sparsity
  is operational, not a buffer cap: the intraday files grew too large for the app
  and were moved to offline SSD, never committed. Recorded as explanation, not
  measurement. Consequence: Phase 6's intraday range is **not** permanently
  capped at 2026-08-19, and H12a's underpowered verdict is provisional.

- **Identified the early AFTERCLOSE mode.** Of the 7 runs at 15:27-15:31, **5
  share a day with a real evening retrain**, arriving ~35 min after a PRECLOSE in
  the ordinary 30-min cadence. So they are the day's **final intraday re-score**,
  not a retrain. `evening_retrains` excludes them; H12b is defined against that
  view, not `run_kind`.

- **Canonical name fixed: EVENING RETRAIN.** `run_kind='nightly'`,
  `run_kind='placeholder'` and label `AFTERCLOSE UPDATE` are one event.
  `placeholder` is an artifact of the forward stamp, never a tradability verdict.

- **Stamp cutover (2026-09-02) handled before the first new file.**
  `stamp_convention` on `ranks_pit`, keyed on **`available_at`, not `run_ts`** --
  a bug I caught mid-build: keying on `run_ts` labelled the last two
  forward-stamped runs (2026-09-02 00:00:00 and 19:34:49, both built 09-01) as
  'honest', which is exactly the contamination the column exists to prevent.
  Archive is currently 871 forward / 0 honest.
  Three canary tests in `tests/test_stamp_cutover.py` FAIL rather than warn.

- **Exit-0-on-incomplete is now a repo-wide invariant** (`ingest/incomplete.py`).
  All four harvesters were affected, not just daily_ranks -- `harvest_er`,
  `harvest_shap` and `harvest_ranks` all did `log.warning(...); continue` and then
  `return 0`. `tests/test_incomplete.py` enforces it structurally and
  behaviourally, and immediately caught a second instance: `harvest_daily_ranks`
  returned 0 when it found **zero** PROD files upstream, which would silently
  freeze the feed if the directory moved.

- **`run_sessions` table built** from `*_rankings_*` FILENAMES only (Rule 4
  untouched -- 1,026 rows, 513 build stamps). It is the only external check on
  `classify_run()`, which is otherwise unfalsifiable.

- **Blob fetching is round-trip bound, not bandwidth bound.** GitHub refuses
  arbitrary-OID batch fetches (`fatal: bad revision` / "did not send all
  necessary objects"), so 228 serial lazy fetches take **hours**; 8 parallel
  workers cleared the outstanding ones in **0.7 min**. And `git cat-file -e` on a
  missing object **triggers a lazy fetch** -- existence checks need
  `GIT_NO_LAZY_FETCH=1` or the check costs exactly what it was meant to avoid.

- `schema.sql` must be read with `encoding="utf-8"` -- a non-ASCII character in a
  comment crashed `connect()` under Windows' cp1252 default.

## 2026-09-01 (step 3) — daily_ranks backfilled; coverage_walk proven unsound

- **`coverage_walk()` is UNSOUND for `daily_ranks/`.** It assumes an older commit
  reaches further back. True for `production/*_latest.pkl`; false here, because
  the `all_*` buffer holds ~200 **runs**, not 200 days -- 200 runs span ~200 days
  in the sparse era and ~16 in the dense one, so reach is non-monotonic in build
  time (build 07-22 reaches 03-12; build 08-31 only 06-10). The walk fetched
  **2 of 114 builds and inserted 0 rows**; Andrew's 5-random-skipped-snapshot
  check then found **205 run timestamps it had missed**, reaching 2 months
  further back. Escalated to the full read, which is now the default for this
  source with the measurement recorded in `_read_all`'s docstring.

- **Result: 633,743 rows inserted; distinct run timestamps 533 -> 871.**
  `daily_ranks` feed: 338 runs, 772,921 rows, floor **2026-03-03** (vs `all`'s
  2026-05-18). Intraday runs 255 -> **482**; nightly 64 -> **141**; placeholder
  0 -> **30**.

- **⚠️ The backfill is INCOMPLETE by 8 of 228 files.** GitHub became unreachable
  mid-run, and those 8 blobs were not yet local. `_read_all` logs and continues
  (one bad blob must not abandon 227) -- but the run still **exited 0**, which was
  a defect: a silently short backfill is a silently short archive. Now returns
  non-zero with a loud INCOMPLETE line. **Re-run `harvest_daily_ranks --mode
  backfill` when GitHub is reachable.**

- **`available_at` distribution: `run_ts` 817 runs / `committed_at` 54 /
  `build_stamp` 0.** The build_stamp branch is wired and reachable (29 commits
  have build_stamp < committed_at) but never wins on this data: wherever
  build_stamp precedes run_ts, committed_at precedes it in turn -- many
  daily_ranks files are committed *days before* their own forward-dated filename
  stamp. min() is picking the correct bound in every case; the branch is inert,
  not wrong.

- Blob fetching is **round-trip bound, not bandwidth bound**. GitHub refuses
  arbitrary-OID batch fetches, so 228 serial lazy fetches would take hours; 8
  parallel workers did the outstanding ones in **0.7 min**. Note that
  `git cat-file -e` on a missing object **triggers a fetch** -- use
  `GIT_NO_LAZY_FETCH=1` for existence checks or the check costs what it was
  meant to avoid.

- Added `--no-sync` (explicit, warns loudly) so a backfill can run from cached
  blobs during an outage without silently skipping the mirror refresh.

## 2026-09-01 (later still) — steps 1-2 recon; yfinance blocked by local AV

- **`classify_run()` validated against ground truth for the first time.** 513
  labelled build stamps from `*_rankings_*` FILENAMES (no blob read - Rule 4
  untouched). **5 disagreements / 513 = 1.0%.** Both failure modes are boundary
  errors, not systematic: 2 AFTEROPEN at 08:57-08:59 -> `morning` (cutoff 9.0 is
  ~3 min late) and 3 AFTERCLOSE at 15:27-15:31 -> `intraday` (cutoff 15.5 is
  ~4 min late). Not changed - reported per instruction.
- **FINDINGS F4's "after 17:00 = nightly" cutoff is wrong.** Observed AFTERCLOSE
  runs from **15:27:35**; PRECLOSE ends 15:24:56. The real boundary is ~15:26,
  not 17:00. `classify_run`'s 15:30 is far closer to truth than the doc.
- **Session cadence changed hard on 2026-08-19**, in the SOURCE filenames, not
  just the panel: 149 days before it at median **1 session/day**; 9 days from it
  at median **15/day**. This is evidence the dense era is a real process change,
  which bears directly on step 4's compaction question.
- **`Date` semantics audit (step 2): morning, intraday and placeholder are
  clean** - newest `Date` == build stamp exactly (delta 0.00h, 3 files each).
  **Only `nightly` diverges: +23.96h on 2 of 3.** So the feared
  "intraday stamped today, forecasting tomorrow" failure does NOT exist. The
  divergence is confined to the nightly retrain.
- **Corrected `stamp_is_forward` is ~64x larger than what ships today.**
  `ranks_pit` compares `run_ts` to `committed_at` from `first_seen_sha`, which
  the coverage walk pins to one of only 2 blob commits - so it flags 2,330 rows.
  Comparing against the real per-file build stamp implicates the whole nightly
  class: **64 runs / ~149,982 rows**, plus placeholder rows once daily_ranks
  lands. Proposed, not built: a `forecast_target_date` column ALONGSIDE `run_ts`.

- **yfinance is BLOCKED by local TLS interception, not by code.** AVG Antivirus
  "Web/Mail Shield" MITMs HTTPS on this box (issuer
  `CN=AVG Web/Mail Shield Root ... generated by AVG Antivirus for SSL/TLS
  scanning`). Its root is in the Windows store but OpenSSL rejects it outright:
  **"Basic Constraints of CA cert not marked critical"** - a malformed CA that
  Windows schannel tolerates and OpenSSL does not. Adding it to a certifi bundle
  does NOT help for that reason. **Not worked around** - disabling certificate
  verification to fetch price data is not a trade worth making. Needs Andrew to
  exclude the finance domains from AVG HTTPS scanning (or turn the shield off).
- **Bug found and fixed in my own new code:** `fetch_actions` swallowed provider
  failures and returned an empty frame, making "no corporate actions" and "the
  provider could not answer" identical downstream - the exact path by which a
  split becomes a phantom -50% return. Now raises `ProviderUnavailable`.

## 2026-09-01 (later) — Claude Code session: F4 corrected, H12, daily_ranks recon

- **FINDINGS F4 was wrong and it was load-bearing.** The nightly build is not a
  "placeholder" or moving-window refresh — Andrew runs a **full model training
  routine after the close**, and it can produce materially different results from
  that morning's model. The next-day `000000` suffix is his deliberate naming
  convention, not upstream flakiness. Corrected in place, with the error recorded
  rather than quietly edited. Two consequences now written into F4:
  `stamp_is_forward` is a **fully reliable nightly-retrain detector**, and
  **morning-vs-nightly is a model comparison, not a vintage comparison** — H3
  does not cover it, which is why H12 exists.

- **H12 pre-registered** (not run): the nightly retrained model's top-5 differs
  materially from that morning's, and the disagreement is informative rather than
  noise. n~64 paired days, MDE TBD.

- **H11 resequenced: the EH spread survey is now a GATE.** Before any Phase 6
  nightly machinery, survey observed extended-hours spreads on the eligible
  nightly top-5. If the median spread exceeds the 0.157% MDE, H11 is dead on cost
  alone and ~4 days are saved. Needs credentials; blocks nothing else.

- `Cap_Size` marked RESOLVED in F7 (model segment label; not valid as a Phase 5
  size control — use `fundamentals_df_latest.pkl` market cap instead).

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
