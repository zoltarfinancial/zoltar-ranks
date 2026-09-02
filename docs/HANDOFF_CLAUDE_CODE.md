# Handoff — round 3: the review protocol

**To: Claude Code, in `C:\Shared\ClaudeWork\zoltar-ranks`. From: Cowork. 2026-09-02, after `c294faa`.**

Rounds 1 and 2 are closed. This round changes how we work rather than what the
code does, so read the whole thing once before touching anything.

**From now on your work comes from a queue, not from this file.** Cowork fires
on a heartbeat, reads the state, and writes a cycle of work orders into
`data/review/cycles/`. You pull them, do them, and report back through one
command. Andrew answers in §12 of the console. This document is the last static
handoff; after it, the equivalent is `python dashboard\review.py next`.

---

## Start every session with this

```powershell
python dashboard\review.py next
```

It prints the end goal, the critical path, the live build gate, the current
cycle's assessment, and the open items addressed to you — each with a `why` and
a checkable `acceptance`. There are four waiting for you in `c-0001`.

Report as you go. One command per event; never hand-edit the log.

```powershell
python dashboard\review.py post --item c-0001.2 --type start
python dashboard\review.py post --item c-0001.2 --type done --ref (git rev-parse --short HEAD) --text "what landed"
python dashboard\review.py post --item c-0001.3 --type blocked --needs andrew --text "why, and what would unblock it"
```

Found something the brain needs to know — another placeholder-pointer — but it
is not any current item?

```powershell
python dashboard\review.py raise --kind finding --title "..." --text "..."
```

It appears in §12 under *Raised, not yet in a cycle*, and the next heartbeat
folds it into a cycle with a why and an acceptance. **You never write a cycle
file.** Cycles are Cowork's; the inbox is everyone's.

---

## Why it is built this way

The board borrows the archive's discipline rather than the monitor's. §08–§11
are entirely derived because machine state can be observed. Intent cannot — a
work order, a judgement call, a "this is blocked on you" are things a party
asserts. So §12 is the one place in the console that accepts writing, and it
keeps its honesty with one rule:

> **Cycles are immutable. Everything after is an append.**

Item status is derived from the append-only inbox exactly the way job status is
derived from the run history. Nobody types a status, nobody can quietly rewrite
what was asked for last Tuesday, and the reasoning behind a decision survives.

This is not ceremony. The failure this project keeps rediscovering is *evidence
replaced by claim*: a scheduled task reporting Ready while expired, a manifest
declaring `built: true` for a job that never ran, a forward-stamped pointer read
as a model output. A review board anyone can edit is that same failure with a
friendlier face.

Two guards follow from it, and both will refuse you:

- `--type done` without `--ref` or `--text` is rejected. A completion with no
  evidence is a claim.
- A `done` whose `acceptance` names a file that does not exist is recorded but
  reported as **`done_unverified`**, and the item does not count as closed. Same
  principle as `kind: proof` in the build manifest.

`dashboard/review_protocol.md` is the full contract — schema, event types,
status derivation, invariants. `python dashboard\review.py check` enforces it,
and `emit_build_status.py --check` now calls it, so one command still audits
everything.

---

## What Cowork shipped this round

| File | What it is |
|---|---|
| `dashboard/review_protocol.md` | the contract |
| `dashboard/review.py` | `next` · `post` · `raise` · `beat` · `emit` · `check` · `open-cycle` |
| `dashboard/serve.py` + `serve.ps1` | the local server, now with one write route |
| `dashboard/index.html` | §12 Review & work orders |
| `data/review/charter.json` | the end goal, success criteria, critical path, lanes |
| `data/review/cycles/c-0001.json` | the first cycle — four items for you, two for Andrew |

`serve.ps1` no longer wraps `python -m http.server`. It runs `dashboard/serve.py`,
which serves the repo read-only **plus** `POST /api/review`, bound to 127.0.0.1.
That is what lets Andrew answer from the page. Over `file://` the page composes
the event line and hands it over with a Copy button instead, so a reply is never
lost — it just arrives through the CLI or the chat.

---

## Your work order for the protocol itself

It is `c-0001.6` in the queue, repeated here because it gates the rest:

- `review.py emit` runs after `emit_build_status.py` in `scripts/daily.py` and in
  `.git/hooks/post-commit`
- `review.py check` runs as part of the test suite
- **`data/review/` is tracked in git** — charter, cycles and inbox are the record
  of how this build was steered and belong in history. `data/results/review_state.json*`
  stays ignored; it is derived.
- a `done` posted through the CLI shows up in §12 on refresh

One caution on tracking the inbox: it is append-only, so two sessions appending
concurrently produce a merge conflict that is always resolved by **keeping both
sides in timestamp order**. Never resolve one by dropping a line.

---

## The heartbeat, and what it means for you

Cowork now fires every two hours, 07:00–19:00 CDT on weekdays. Each fire reads
`build_status.json`, `review_state.json`, the inbox since the last cycle and
`git log`, then writes a cycle. Fires with nothing to say write a quiet
heartbeat and stop.

Consequences worth knowing:

- **Your `done` events are what drive the next cycle.** A finished work order
  that is not posted is invisible to the brain, and the next cycle will be
  written as though you were still on it. Posting is cheaper than explaining.
- **A `raise` is the fastest way to change the plan.** The placeholder-pointer
  finding reordered the whole board; had it existed as a raised finding it would
  have been folded into a cycle within two hours instead of waiting for a report.
- **Cowork issues freely on the critical path and queues judgement calls for
  Andrew.** If an order looks wrong, `--type reject --text "why"` is a legitimate
  response and it will be read. You are not obliged to build something you can
  see is a mistake.

---

## Acceptance for round 3

- [ ] `python dashboard\review.py next` runs and shows four items
- [ ] `c-0001.6` posted `done` with the wiring in place
- [ ] `data/review/` tracked; `review_state.json*` ignored
- [ ] `emit_build_status.py --check` shows `[OK  ] review protocol  0 blocking`
- [ ] the remaining `c-0001` items picked up in priority order

Everything after this comes from the queue.
