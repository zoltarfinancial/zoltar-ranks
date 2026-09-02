# Handoff — round 2

**To: Claude Code, in `C:\Shared\ClaudeWork\zoltar-ranks`. From: Cowork (owns `dashboard/`). 2026-09-02, after `cd027cc`.**

Round 1 landed. `--check` went 11 blocking → 2, the heartbeat is real, the
research feed exists, and the two remaining blocking rows are the two you were
right not to fake. This round is smaller: three corrections in your lane, two
answers to questions you raised, and one thing only Andrew can do.

Round 1 for reference: T1 mode stamping ✅ · T1b row counts ✅ · T2 emitter
wiring ✅ · T3 pytest report ✅ · T5 manifest ✅ · B1 exporter ✅. T4 (two gates)
and T6 (`monitoring/`) carry forward below.

---

## Answers to your two questions

**1. `dashboard/dashboard_data.js` — resolved by moving the generated feeds, not
by crossing the lane.** Both `.js` feeds now live in `data/results/` beside their
JSON, and `index.html` loads them as `<script src="../data/results/*.js">`. A
script tag works over `file://` where a `fetch` does not, so nothing is lost.
Nothing but Cowork ever writes into `dashboard/` again.

Your one extra line, in `export_dashboard_data.py`, beside the JSON write:

```python
(cfg.results_dir / "dashboard_data.js").write_text(
    "window.__ZOLTAR_DATA__ = " + payload + ";\n", encoding="utf-8")
```

The emitter already does the equivalent for `build_status.js`. Delete the stale
`dashboard/build_status.js` when you see it — `--check` now warns about it.

**2. Committing `dashboard/` — yes, track it.** The lane rule is about who
*edits*, not who commits. A fresh clone that cannot run `--check` is a real
defect. Commit `dashboard/emit_build_status.py`, `BUILD_MONITOR.md`,
`index.html`, `README.md`, `seed_data.json`, `serve.ps1`. Keep the generated
feeds (`data/results/*.js`, `build_status.json`, `dashboard_data.json`,
`pytest_report.json`, `run_history.jsonl`) gitignored — they are derived, and a
tracked derived file produces a merge conflict on every run.

---

## What Cowork changed this round

| File | Change |
|---|---|
| `dashboard/index.html` | Per-section absence handling; `<meta charset>`; freshness label reads `freshness_basis`; script tags repointed to `../data/results/` |
| `dashboard/emit_build_status.py` | `build_status.js` now written to `data/results/`; new `kind: proof` deliverable; `--check` warns on the stale JS and the missing `dashboard_data.js` |

**The absence fix was urgent and is worth knowing about.** `render()` called
`equity(data)` → `data.benchmarks.equity_curves` with no guard. Your exporter
correctly omits `benchmarks`, so the first live load would have thrown a
`TypeError` and blanked **every** section — including `archive_health`, which is
real. A console that goes dark when a phase is unbuilt reads as a broken page
rather than an honest gap, which is the worst of both. Each renderer is now
wrapped: absent inputs produce a *NOT YET MEASURED* panel carrying your
`sections_absent` reason string verbatim, a throw produces a *FEED ERROR* panel
naming the message, and neither can take down its neighbours.

Verified headless against your live `dashboard_data.json`: 7 absence panels, 0
page errors, the archive strip showing 235 daily / 308 intraday runs from
2025-10-01 and `0.6h since newest available_at · run_ts stamped 09-03`.

---

## Task 7 — a schema-name trap you left behind

`archive_health.hours_since_last_run_ts` is now computed from `available_at` —
correctly — but still named `run_ts`. The console only got this right because it
reads `freshness_basis`; anything else consuming that field will read the name
and believe it.

You fixed the freshness *computation* and left the *name* pointing at the trap.
Rename it to `hours_since_fresh`, keep `freshness_basis` beside it, and bump the
feed's `schema_version`. Tell Cowork the version and both keys will be read.

---

## Task 8 — Phase 1a reads 100% and it is not done

Every 1a deliverable is a path that exists, so `pct` derives to 100 while
`harvest_intraday` has 7 scheduled windows, 6 of them missed. That is the
`schedule_harvest.ps1` lesson recurring one level up: file existence is not
evidence of behaviour.

`kind: proof` now exists for exactly this. Add to phase `1a` in
`data/build/manifest.yaml`:

```yaml
      - name: One full trading session of green beats
        kind: proof
        proof: {job: harvest_intraday, full_session: true}
```

Derivation: `done` only when the job's non-`na` beats are all `ok` **and** there
are at least `expected_24h` of them; `wip` once there is at least one `ok` among
misses; `todo` otherwise. It reads `todo` today and 1a drops to 90%. It cannot be
made green by writing a file — only by the harvest actually running.

Add the same to any phase whose completion is behavioural rather than textual.
Phase 7 also reads 100% on two documents while H9 and H10 are unrun; a proof
deliverable pointed at the first resolved hypothesis would be honest there too.

---

## Task 4 (carried) — the two remaining gates

`no_same_bar` and `no_run_ts_execution` stay `not_built`, and you were right to
leave them: a test that passes because the code under test does not exist is a
green light meaning nothing. They land with `analysis/backtest.py`. Until then
the gate stays amber and §10 says why — that is the system working.

`no_latest_pkl` passing already, via the AST check on the one module allowed to
name those files, is the right shape for the other two.

## Task 6 (carried) — `monitoring/`

Blocked on your side; the stubs are untracked so `git rm` does not apply.
Andrew runs it — see below.

---

## `stamp_cutover` is failing

Decision: **it stays `severity: warning` until the test passes**, then gets
promoted to `blocker`. Rationale — a gate should go red because the system is
wrong, and we do not yet know whether the system or the assertion is wrong. The
test was written today, on the day the convention changed, which is exactly when
a test is most likely to encode the old world.

So the next question is yours: **is the assertion right?** Report which of the
two it is before changing either. If the assertion is right, the failure is a
real pooling bug and rule 9 applies — stop, fix the grouping, do not loosen it.

---

## Acceptance for round 2

- [ ] `data/results/dashboard_data.js` written on every export
- [ ] `dashboard/` tracked in git; generated feeds still ignored
- [ ] stale `dashboard/build_status.js` deleted
- [ ] `hours_since_fresh` renamed, feed `schema_version` bumped
- [ ] `1a` carries the proof deliverable and reads below 100%
- [ ] `stamp_cutover` diagnosed: assertion wrong, or system wrong
- [ ] `--check` reports 0 blocking, and the `harvest_evening` /
      `harvest_premarket` beats fill in on their own after 15:30 today and
      before 09:00 tomorrow

The real acceptance test is unchanged: §08 showing 13 of 13 green beats for
`harvest_intraday` across one full trading session.

---

## For Andrew

```powershell
Remove-Item -Recurse -Force C:\Shared\ClaudeWork\zoltar-ranks\monitoring
```

Then, to see the console:

```powershell
.\dashboard\serve.ps1     # http://localhost:8787/dashboard/
```
