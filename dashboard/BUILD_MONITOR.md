# Build monitor contract — §08–§11 of the research console

**Canonical. `monitoring/` is retired; nothing there is current.**

`docs/DASHBOARD.md` covers the research half of the console
(`data/results/dashboard_data.json`). This file covers the *build* half:
sections 08–11, which report whether the pipeline is alive, how far along the
plan is, and whether the contract gates that make results meaningful have
actually run.

For the ordered task list — what to change, in what order, with the code —
see **`docs/HANDOFF_CLAUDE_CODE.md`**. This file is the schema and the rules.

The lane rule holds. Everything under `dashboard/` is owned by the Cowork
session; the backend never edits it. The two sides meet at two files:

| File | Written by | Read by |
|---|---|---|
| `data/results/dashboard_data.json` | `src/zoltar_ranks/analysis/export_dashboard_data.py` | §01, §03, §04, §05, §06 |
| `data/results/build_status.json` | `dashboard/emit_build_status.py` | §08, §09, §10, §11 |

`dashboard/emit_build_status.py` is a working implementation, not a sketch. It
stats files, reads two JSON feeds and shells out to `git`. Nothing in it imports
`zoltar_ranks` or opens DuckDB, so it cannot fail a harvest.

---

## The one command

```powershell
python dashboard\emit_build_status.py --check
```

Audits every input the monitor depends on and names the file and the fix for
each one that is missing, stale or mismatched. Exits non-zero if any is
blocking. **This is the sync point between Claude Code, Andrew and Cowork** —
paste its output instead of writing a status update.

The check that earns its keep is the mode cross-check: it compares the `modes`
each job declares in the manifest against the modes that actually appear in
`run_history.jsonl`. A job declaring a mode nothing writes reads `not_built`
forever, and nothing else in the system would have told you.

---

## Inputs

| File | Nature | Owner |
|---|---|---|
| `data/build/manifest.yaml` | **declarative** — phases, deliverables, jobs, gates, feeds, blockers that *should* exist | Claude Code (seeded by Cowork) |
| `data/results/run_history.jsonl` | **append-only heartbeat** — one JSON line per run of `scripts/daily.py`, success and failure | `scripts/daily.py` |
| `data/results/last_run_status.json` | latest run only; folded in as the newest entry | `scripts/daily.py` |
| `data/results/pytest_report.json` | `pytest --json-report` output; gate status is read from it | the test run |
| the repo itself | file existence, mtimes, `git log` | — |

Everything else is derived.

---

## `build_status.json` schema (v1)

```jsonc
{
  "schema_version": 1,
  "generated_at": "2026-09-02T17:40:00-05:00",   // ISO 8601 WITH offset
  "is_example": false,                            // true ONLY for the shipped scaffold
  "repo": { "name", "branch", "head_sha", "head_message", "head_at", "dirty" },

  "gate": { "state": "green|amber|red", "headline": "...", "detail": "..." },

  "jobs": [{
    "id", "name", "why", "schedule",
    "criticality": "critical|high|normal",
    "status": "healthy|late|failed|partial|done|paused|not_built",
    "last_run_at", "last_status", "last_duration_s", "next_expected_at",
    "rows_added_last", "runs_24h", "expected_24h", "failures_24h", "error",
    "beats": ["ok","ok","miss","err","na", ...]   // exactly 24, oldest first
  }],

  "phases": [{
    "id", "name", "subtitle",
    "status": "done|in_progress|blocked|queued",
    "owner": "claude-code|cowork|shared",
    "pct", "critical_path", "started_at", "completed_at", "eta", "note",
    "deliverables": [{ "name", "path", "kind": "code|data|test|doc|job",
                       "state": "done|wip|todo|blocked" }]
  }],

  "contract_tests": [{ "id", "name", "severity": "blocker|warning",
                       "status": "pass|fail|stale|not_built",
                       "last_run_at", "detail", "guards" }],

  "feeds":    [{ "path", "consumed_by", "status": "ok|stale|missing|example",
                 "updated_at", "note" }],
  "blockers": [{ "id", "title", "severity", "needs": "andrew|claude-code|cowork",
                 "since", "detail" }],
  "activity": [{ "at", "kind": "commit|run|note|decision|blocker", "actor",
                 "summary", "ref" }]
}
```

---

## Derivation rules (non-negotiable)

| Field | Derived from |
|---|---|
| `jobs[].beats` | 24 windows of the job's cadence, back from now, matched against the runs of that job's `modes`. `na` outside the schedule, `miss` inside it with no run, `err` if any run in the window errored. |
| `jobs[].status` | `not_built` if **no run has ever been logged** — whatever the manifest claims · `failed` if the last run errored · `late` if past `next_expected_at` + one cadence · `done` for a completed one-shot · else `healthy` |
| `jobs[].expected_24h` | scheduled windows in the last real 24 hours, so it is comparable with `runs_24h` |
| `contract_tests[].status` | pytest nodeid substring match: any `failed` → `fail`, all `passed` → `pass`, report older than 36 h → `stale`, no match → `not_built` |
| `phases[].deliverables[].state` | `blocked` if the phase is · for `kind: proof`, from the referenced job's beats (below) · `done` if the path exists (and, for `kind: test`, its test passes) · else `todo` |
| `phases[].pct` | done = 1, wip = 0.5, over the deliverable count |
| `feeds[].status` | exists? newer than `max_age_hours`? → `ok` / `stale` / `missing` |
| `activity` | `git log -15` plus the newest run of each job, newest 12 |
| `gate.state` | **red** if any blocker gate fails, or a `critical` job is not healthy/done · **amber** if a blocker gate is stale or never ran, or a `high` job is late/failed/not built · **green** otherwise |

**`built: true` in the manifest is a statement of intent; the run log is the
evidence.** A job declared built with no heartbeat reports `not_built` and
carries an `error` string saying which mode is missing. Trusting intent over
evidence is the one way a monitor lies, and it was a real defect here until
2026-09-02.

**`kind: proof` — for a deliverable that is proven by behaviour, not by a file.**

```yaml
- name: One full trading session of green beats
  kind: proof
  proof: {job: harvest_intraday, full_session: true}
```

`done` only when the job's non-`na` beats are all `ok` and there are at least
`expected_24h` of them; `wip` once there is one `ok` among misses; `todo`
otherwise. Use it wherever completion means *the thing ran*, not *the file
exists*. `schedule_harvest.ps1` existed on 2026-09-01 while the task had silently
expired, and a phase measured on file existence would have read 100% throughout.

**Sections the feed omits.** The research exporter omits a section it cannot
measure honestly and names the reason in `sections_absent`. The console renders
each section independently: an absent one shows a *NOT YET MEASURED* panel with
that reason, a throwing one shows a *FEED ERROR* panel, and neither can blank its
neighbours. Never zero-fill an unmeasured section and never fall back to the seed
for one — a page mixing invented and real figures is worse than a page with gaps.

**Never hand-edit `build_status.json` or `build_status.js`.** They are
regenerated on every run. If a value cannot be derived, emit `null` and say why
in the nearest `note`. The same applies to `gate.state`: it stays red or amber
until the work that makes it green is actually done.

---

## The heartbeat

`scripts/daily.py` appends one line per run to `data/results/run_history.jsonl`,
in a `finally` block, with a mode derived from the wall clock:

```json
{"finished_at":"2026-09-02T10:30:04-05:00","mode":"intraday","failed":[],"rows":1214,
 "steps":{"ranks":{"ok":true,"seconds":38.4,"rows":1214}}}
```

`mode` ∈ `premarket` (before 09:00 CDT) · `intraday` (09:00–15:30) · `evening`
(15:30–21:30) · `backfill` · `weekend`. The boundaries come from
`docs/FINDINGS.md` F4 and must match `start_hour`/`end_hour` in the manifest.

Append on both success and failure. A job that only logs its successes is
indistinguishable from one that is not running.

---

## Getting it onto the page

The console reads the first source that answers:

1. `window.__ZOLTAR_BUILD__` — `data/results/build_status.js`, written beside
   the JSON on every emit and loaded by `index.html` as
   `<script src="../data/results/build_status.js">`. A script tag resolves over
   `file://` where a `fetch` of the same path does not — which is the whole
   reason the `.js` twin exists. The generated feeds live in `data/results/`, not
   in `dashboard/`, so that nothing but Cowork ever writes into `dashboard/`.
2. `fetch("../data/results/build_status.json")` — when served over HTTP
   (`.\dashboard\serve.ps1`).
3. The published artifact's own store — Andrew pastes the JSON into
   §11 → *Update this monitor*. Persists across reloads, republishes and devices.
4. The example scaffold embedded in the page (`is_example: true`).

The same chain applies to the research half: `data/results/dashboard_data.js`
(`window.__ZOLTAR_DATA__ = {...}`) makes §01–§06 work over `file://`. Writing it
is one extra line in the exporter and costs nothing if you skip it.

---

## Definition of done

- [ ] `run_history.jsonl` appended on every run, success and failure, with a
      real mode and a UTC offset
- [ ] emitter in `scripts/daily.py` and in `.git/hooks/post-commit`
- [ ] `pytest_report.json` produced by the test run
- [ ] `data/build/manifest.yaml` mirrors `docs/PLAN.md`
- [ ] the three missing blocker gates exist as real tests
- [ ] `--check` reports 0 blocking
- [ ] §08 has shown one full trading session of green beats for `harvest_intraday`
      — that, not the existence of `schedule_harvest.ps1`, is what closes Phase 1a
