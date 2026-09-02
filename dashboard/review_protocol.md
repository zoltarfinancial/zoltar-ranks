# The review protocol — §12 of the research console

**The shared working surface for the three parties on this build.** Cowork
writes what should happen next and why; Claude Code reports what it did; Andrew
decides the things only he can decide. All three read the same page and append
to the same log.

Canonical. Owned by Cowork. `dashboard/review.py` is the reference
implementation and the CLI all three parties use.

---

## Why it is shaped this way

The build monitor (§08–§11) answers *is the pipeline alive and are its results
trustworthy*. It is entirely derived — nothing in it can be typed. That is right
for machine state and useless for intent: a work order, a judgement call and a
"this is blocked on you" are things a party asserts, not things a script can
observe.

So §12 is the one place in the console that accepts writing. It keeps the
discipline that makes the rest of the system trustworthy by borrowing the
archive's rule rather than the monitor's:

> **Cycles are immutable. Everything after is an append.**

A cycle file, once written, is never edited. Every reaction to it — an
acceptance, a completion, a rejection, a question, a correction — is a new line
in an append-only log. Item *status* is then derived from that log exactly the
way job status is derived from the run history. Nobody types a status, nobody
can quietly rewrite what was asked for last Tuesday, and the trail of how a
decision was reached survives.

This matters more than it sounds. The failure this project keeps rediscovering
is *evidence replaced by claim* — a scheduled task reporting Ready while
expired, a manifest declaring `built: true` for a job that never ran, a
forward-stamped pointer read as a model output. A review board where anyone can
edit history is the same failure wearing a friendlier face.

---

## Files

| Path | Nature | Written by |
|---|---|---|
| `data/review/charter.json` | **declarative** — the end goal, success criteria, critical path, non-negotiables | Cowork, by hand, rarely |
| `data/review/cycles/<cycle_id>.json` | **immutable** — one heartbeat's assessment and its items | Cowork, one per heartbeat |
| `data/review/inbox.jsonl` | **append-only** — every reaction by any party | all three, via `review.py post` |
| `data/review/heartbeat.jsonl` | **append-only** — one line per heartbeat fire, quiet or not | Cowork |
| `data/results/review_state.json` | **derived** — what §12 renders | `review.py emit` |
| `data/results/review_state.js` | **derived** — the `file://` twin | `review.py emit` |

Nothing but `charter.json` is hand-editable, and nothing at all is hand-editable
in `data/results/`.

---

## A cycle

```jsonc
{
  "cycle_id": "c-0007",                       // monotonic, zero-padded
  "opened_at": "2026-09-03T09:00:00-05:00",   // ISO 8601 WITH offset
  "opened_by": "cowork",
  "quiet": false,                             // true = nothing changed, no items
  "state_snapshot": {                         // what Cowork actually saw
    "head_sha": "c294faa", "gate": "amber", "blocking": 2,
    "phase": "1a", "phase_pct": 95
  },
  "assessment": "Two or three sentences: where we are against the end goal, and what the last cycle changed.",
  "items": [{
    "id": "c-0007.1",                         // <cycle>.<n>, stable forever
    "kind": "work_order" | "question" | "decision" | "risk" | "finding",
    "for": "claude-code" | "andrew" | "cowork",
    "priority": "now" | "next" | "later",
    "title": "One line, imperative for a work order.",
    "why": "Why this, why now, in terms of the end goal. Not a restatement of the title.",
    "acceptance": "How we will know it is done. Must be checkable by someone who was not here.",
    "depends_on": ["c-0006.3"],
    "estimate": "2h",
    "refs": ["docs/PLAN.md", "src/zoltar_ranks/analysis/backtest.py"]
  }]
}
```

`acceptance` is not optional on a `work_order`. An order without a checkable
finish line is how a build accumulates work that is neither done nor abandoned.

## An inbox event

```jsonc
{"at":"2026-09-03T11:04:12-05:00","by":"claude-code","cycle":"c-0007",
 "item":"c-0007.1","type":"done","needs":null,
 "text":"Gate written, 4 assertions, all failing against the stub as expected.",
 "ref":"a1b2c3d"}
```

| `type` | Meaning | Who normally sends it |
|---|---|---|
| `accept` | picked up, will do | claude-code, andrew |
| `start` | in progress | claude-code |
| `done` | finished; `ref` should name the commit or artifact | claude-code, andrew |
| `blocked` | cannot proceed; `needs` names who unblocks | any |
| `reject` | will not do, `text` says why | andrew, claude-code |
| `defer` | not now; `text` says until when or until what | andrew |
| `reply` | answers a `question` item | the party the item is `for` |
| `note` | context, no status change | any |
| `reopen` | a `done` item was not actually done | any |

## Derived item status

Computed by `review.py emit`, never stored:

| Condition | `status` |
|---|---|
| no events | `open` |
| latest status-bearing event is `accept` | `acked` |
| … `start` | `in_progress` |
| … `done` | `done` |
| … `blocked` | `blocked` |
| … `reject` | `rejected` |
| … `defer` | `deferred` |
| … `reopen` | `open` |
| `kind: question` with a `reply` from the party it is `for` | `answered` |

`waiting_on` is the party who owes the next move: the item's `for` while it is
open/acked/in_progress, the `needs` party while it is blocked, and nobody once
it is done, rejected, deferred or answered.

**A `done` event does not close a work order on its own if its `acceptance` names
a checkable artifact that is absent.** `emit` re-checks paths named in
`acceptance` and flags `done_unverified` when the artifact is missing — the same
principle as `kind: proof` in the build manifest. Claiming completion is not
completion.

---

## How each party uses it

**Cowork (the brain).** Fires on the heartbeat. Reads `build_status.json`,
`review_state.json`, the inbox since the last cycle, and `git log`. Decides what
is next against `charter.json` and the critical path. Writes one new cycle,
appends a heartbeat line, runs `emit`. Issues freely on the critical path;
queues anything that changes scope, spends money, or picks between defensible
approaches as a `question` or `decision` for Andrew.

**Claude Code.** At the start of every session:

```powershell
python dashboard\review.py next
```

Prints the open items addressed to it, newest cycle first, with `why` and
`acceptance`. Report back with one command per event — never by hand-editing
the log:

```powershell
python dashboard\review.py post --item c-0007.1 --type start
python dashboard\review.py post --item c-0007.1 --type done --ref (git rev-parse --short HEAD) --text "..."
python dashboard\review.py post --item c-0007.2 --type blocked --needs andrew --text "..."
```

It may also **open a `finding`** — something it learned that the brain needs in
the next cycle, like the placeholder-pointer discovery:

```powershell
python dashboard\review.py raise --kind finding --for cowork --title "..." --text "..."
```

Raised items land in `data/review/inbox.jsonl` as `type: raise` and are folded
into the next cycle by Cowork. Claude Code never writes a cycle file.

**Andrew.** Reads §12 in the console and answers inline. The page posts to the
local server; if it is not running, the page hands him the exact JSON line and a
Copy button, and pasting it into the Cowork chat has the same effect.

---

## Invariants

1. A cycle file is never modified after it is written. Corrections are `note` or
   `reopen` events, or a new item in the next cycle.
2. Item ids are stable forever. `c-0007.1` means one thing for the life of the
   project.
3. Status is derived. There is no status field in any cycle or inbox record.
4. Every `work_order` has an `acceptance` that someone who was not present could
   check.
5. `review.py check` enforces 1–4 and exits non-zero. It runs inside
   `emit_build_status.py --check`, so one command still audits everything.
6. The inbox is append-only. A torn or unparseable line is skipped, never
   repaired in place.
