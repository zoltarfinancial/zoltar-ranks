# Handoff — make the build monitor tell the truth

**To: Claude Code, working in `C:\Shared\ClaudeWork\zoltar-ranks`.**
**From: the Cowork session, which owns `dashboard/`. Written 2026-09-02.**

The research console (`dashboard/index.html`) is built and its build-monitor
half (§08–§11) is wired end to end. The emitter runs, the manifest exists, the
page reads them. **Every one of its numbers is currently `not_built` or `red`,
and that is correct** — the pipeline is not yet reporting anything the monitor
can read.

This document is the list of what makes it real. It is ordered by leverage.
Nothing here is a refactor; the largest change is about forty lines in
`scripts/daily.py`.

---

## The one command

```powershell
python dashboard\emit_build_status.py --check
```

It audits every input the monitor depends on, names the file and the fix for
each broken one, and exits non-zero if any is blocking. **This is the sync
point between the three of us.** When Andrew asks "where are we", when you
finish a task, when Cowork changes the console — run it and paste the output.
It is shorter than a status update and it cannot be wrong.

Right now it reports 11 blocking rows. The tasks below clear them.

```
  [FAIL] data/results/run_history.jsonl         missing
  [FAIL] job harvest_intraday                   declares mode(s) ['intraday'] that never
                                                appear in the run history (history has: ['daily'])
  [FAIL] data/results/pytest_report.json        missing
  [FAIL] scripts/daily.py -> emitter            emitter is NOT called from daily.py
  [FAIL] scripts/daily.py -> run_history.jsonl  does not append run_history.jsonl
  ...
```

---

## Lanes

| Path | Owner | Rule |
|---|---|---|
| `src/`, `scripts/`, `tests/`, `docs/`, `config/` | **you** | Cowork never edits these |
| `dashboard/` | **Cowork** | you never edit these; open an issue in `§11 blockers` instead |
| `data/build/manifest.yaml` | **you**, seeded by Cowork | update it in the same commit that changes `docs/PLAN.md` |
| `data/results/*.json*` | **derived** | never hand-edited by anyone |

`monitoring/` is retired — see Task 6.

---

## Task 1 — `scripts/daily.py`: stamp a real mode, and append a heartbeat

**Highest leverage on the board.** Two facts the monitor cannot currently see:

1. Every run is stamped `mode: "daily"`, because `daily.py` only distinguishes
   `--backfill`. The scheduled task fires every 30 minutes from 07:00 to 21:30,
   so an intraday tick and the evening retrain capture are indistinguishable in
   the record. The manifest declares three jobs and all three read `not_built`.
2. `last_run_status.json` is **overwritten**, so there is exactly one run in
   existence at any moment. A heartbeat needs history.

Fix both in one edit. Derive the mode from the wall clock using the run-type
boundaries already established in `docs/FINDINGS.md` F4, keep `--mode` as an
explicit override, and append to `run_history.jsonl` in a `finally` block.

```python
# scripts/daily.py

from datetime import datetime
from zoneinfo import ZoneInfo

CDT = ZoneInfo("America/Chicago")

# Run-type boundaries, docs/FINDINGS.md F4. These must stay in sync with the
# start_hour/end_hour of the jobs in data/build/manifest.yaml — if they drift,
# `emit_build_status.py --check` will not catch it, because both sides will
# agree on a mode string that means different things.
def derive_mode(at: datetime | None = None) -> str:
    at = (at or datetime.now(CDT)).astimezone(CDT)
    h = at.hour + at.minute / 60
    if at.weekday() > 4:
        return "weekend"
    if h < 9:
        return "premarket"      # 01:00-09:00 morning model build
    if h < 15.5:
        return "intraday"       # 30-min re-scores
    return "evening"            # AFTERCLOSE UPDATE full retrain
```

```python
    # in main(), replacing `mode = "backfill" if args.backfill else "daily"`
    mode = "backfill" if args.backfill else (args.mode or derive_mode())
```

Add the argument, and make the harvesters' `--mode` value independent of the
*record's* mode — the harvesters only understand `backfill` / `daily`:

```python
    ap.add_argument("--mode", default=None,
                    help="override the derived run mode recorded in run_history.jsonl "
                         "(premarket|intraday|evening). Does not change harvester behaviour.")
    ...
    harvest_mode = "backfill" if args.backfill else "daily"   # what the STEPS receive
    step_argv = ["--mode", harvest_mode]
```

Then the status write, in a `finally` so a crash still leaves a mark:

```python
    finally:
        status = {
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "mode": mode,
            "failed": failed,
            "rows": total_rows,          # see Task 1b; null is acceptable
            "steps": results,
        }
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        (cfg.results_dir / "last_run_status.json").write_text(
            json.dumps(status, indent=2), encoding="utf-8")
        with (cfg.results_dir / "run_history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(status, separators=(",", ":")) + "\n")
```

Three things about that block that are load-bearing:

- **`.astimezone()`.** The current code writes `datetime.now().isoformat()`,
  which is naive. It happens to work because the emitter runs on the same
  machine, but the file is committed, read by a monitor that may not be, and
  spans a DST boundary in November. `--check` warns on naive timestamps.
- **`finally`, not the happy path.** A job that only logs its successes is
  indistinguishable from one that is not running — which is exactly the state
  the monitor exists to detect.
- **Append, never rewrite.** `run_history.jsonl` is append-only for the same
  reason the archive is. A torn line is survivable (the emitter skips
  unparseable lines); a rewritten history is not.

### Task 1b — row counts (optional, do it if cheap)

`rows_added_last` is null on every tile because nothing reports a row count.
If the harvesters can return one — even just the `inserted` count they already
log — put it in each step's dict as `rows` and the emitter will sum them:

```python
    results[name] = {"ok": True, "seconds": ..., "rows": inserted_or_none}
```

An unreported count stays null and the console shows a dash. **Do not emit 0
for "unknown"** — a zero row count is a real and alarming signal on the
intraday job, and it must not be produced by a missing return value.

---

## Task 2 — run the emitter automatically

It is stdlib-only, exits 0, and only reads the repo. Two call sites:

**a. Last step of `scripts/daily.py::STEPS`,** after everything else, so a
harvest immediately refreshes the page:

```python
def _emit_monitor(argv=None) -> int:
    import subprocess, sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    return subprocess.run([sys.executable, str(repo / "dashboard" / "emit_build_status.py")],
                          cwd=repo).returncode

STEPS = [
    ...,
    ("monitor", _emit_monitor),      # last: it reports on everything above it
]
```

It must run **after** the status write of Task 1, or it will report the
previous run. If that ordering is awkward inside `STEPS`, call it directly
after the `finally` block instead — either is fine, the ordering is not.

**b. `.git/hooks/post-commit`,** so the repo header and §11 activity log stay
current between harvests:

```sh
#!/bin/sh
python dashboard/emit_build_status.py >/dev/null 2>&1 || true
```

`|| true` deliberately — a monitor that can fail a commit is worse than a stale
monitor.

---

## Task 3 — emit a pytest report

Every contract gate in §10 currently reads `not_built`, which holds the
top-line gate at amber. That is the honest default, and it clears the moment
the suite writes a machine-readable report.

```powershell
pip install pytest-json-report
pytest tests -q --json-report --json-report-file=data\results\pytest_report.json
```

Add it to whatever you use to run the suite, and to the post-commit hook if it
is fast enough. Gate status is matched by **substring against pytest nodeids**,
so `test: test_manifest` in the manifest matches every test in
`tests/test_manifest.py`. If you rename a test file, `--check` will report the
gate as having no matching test rather than silently passing.

The report going stale is itself signalled: a gate that passed more than 36
hours ago reads `stale`, not `pass`.

---

## Task 4 — write the three missing blocker gates

These are the four non-negotiable rules, and three of them have no test:

| id | Rule | Test | State |
|---|---|---|---|
| `pit_immutability` | archive is point-in-time | `tests/test_db.py` | exists |
| `work_idempotency` | a repeat tick reads 0 blobs | `tests/test_manifest.py` | exists |
| `no_same_bar` | fills strictly after the information timestamp | `test_no_same_bar_execution` | **to build** |
| `no_run_ts_execution` | decisions key off `available_at`, never `run_ts` | `test_no_run_ts_execution` | **to build** |
| `no_latest_pkl` | `*_rankings_latest.pkl` never reaches a backtest input | `test_no_latest_pkl` | **to build** |

Write them **before** Phase 3, not after. They are the assertions that decide
whether Phase 3's numbers mean anything, and a gate written after the result it
was supposed to guard tends to get written to pass. Blocker B2 in the manifest
says so; it clears when they exist.

`no_latest_pkl` is the cheapest of the three and can be written today against
the existing config: assert no path in `Config.rank_files` or any backtest
input resolver ends in `_rankings_latest.pkl`.

---

## Task 5 — own `data/build/manifest.yaml`

Cowork seeded it from `docs/PLAN.md` and `docs/FINDINGS.md`: 3 jobs, 9 phases,
7 contract tests, 4 feeds, 3 blockers. **From now it is yours.** Update it in
the same commit that changes `docs/PLAN.md`, so the console's phase board and
the plan cannot disagree.

It declares intent only. It has no status fields, and the two overrides it does
carry (`status_override`, `state_override`) exist for a deliberately paused job,
not for making the page look further along. If you want a tile to go green, the
route is to make the thing true.

Two things in it are guesses and should be corrected as you go:

- `phases[2..6].deliverables[].path` — I invented plausible module paths under
  `src/zoltar_ranks/analysis/`. Rename them to whatever you actually build; the
  deliverable state is derived from `path.exists()`, so a wrong path reads
  `todo` forever.
- `phases[].eta` — only `1a` has one. Add them where you have a real estimate
  and leave them null where you do not. Null renders as "—", which is honest.

---

## Task 6 — retire `monitoring/`

`monitoring/` holds a stale copy of `emit_build_status.py` (three revisions
behind) and `DASHBOARD_CONTRACT.md`, superseded by `dashboard/BUILD_MONITOR.md`.
Two copies of a contract is how the contract stops being one.

```powershell
git rm -r monitoring
```

Its files have been replaced with pointer stubs in the meantime, so nothing is
lost by deleting the folder.

---

## Acceptance — what "done" looks like

Run `--check`. Done is:

```
  0 blocking, 0 warning, N ok
```

and then, in order:

- [ ] **T1** `run_history.jsonl` grows by one line every 30 minutes, with
      `mode` ∈ {premarket, intraday, evening} and a UTC offset on every timestamp
- [ ] **T2** the emitter runs from `daily.py` and from `post-commit`;
      `dashboard/build_status.js` mtime tracks the last harvest
- [ ] **T3** `pytest_report.json` exists and the two existing gates read `pass`
      rather than `not_built`
- [ ] **T4** the three missing gates exist; §10 shows five blockers, none
      `not_built`
- [ ] **T5** the manifest's phase deliverable paths all resolve to real files
      or are honestly `todo`
- [ ] **T6** `monitoring/` is gone

**The real acceptance test for Phase 1a is not any of those.** It is: open
`dashboard/index.html` after a full trading day and see §08 show one unbroken
line of `ok` beats for `harvest_intraday`, 13 of 13. The existence of
`schedule_harvest.ps1` is not evidence the harvest ran — that is precisely the
lesson of the `-Once` trigger that expired silently on 2026-09-01 while
reporting `State=Ready`.

---

## What not to do

- **Never hand-edit `data/results/build_status.json` or `build_status.js`.**
  They are regenerated on every run; an edit is lost within 30 minutes and
  misleads everyone in between. If a field cannot be derived, emit `null` and
  explain it in the nearest `note`.
- **Never set `status_override` to clear a red.** Red is the honest default
  until the intraday harvest is running and the execution gates exist.
- **Never loosen a failing contract test.** Stop, and let the monitor show it
  red while it is failing. That is what §10 is for.
- **Do not edit `dashboard/`.** If the console needs a change, add a blocker to
  the manifest with `needs: cowork` and it will surface in §11.

---

## Reporting back

Paste into the Cowork session, or leave in `docs/SESSION_LOG.md`:

1. the `--check` output,
2. the one line the emitter prints (`... gate=AMBER ...`),
3. anything in the manifest you corrected.

That is enough for Cowork to know what the console will show without reading
the repo, and enough for Andrew to know what changed without reading either.
