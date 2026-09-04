# Courier protocol

**Status:** in force on `zoltarone` as of 2026-09-04. Mirrored shape intended for
`zoltarlead`; the guards invert, the protocol does not.

This document exists because a capability that lives only in one node's local
`.claude/` directory is invisible to every other agent. ZoltarOne has four slash
commands and three enforcement hooks that shape how it behaves on this repo.
Until now they were in no committed contract, so `zoltarlead/claude-code` had no
way to know they existed, what they enforce, or what they guarantee. This file
is that contract.

## Why a courier exists at all

Two halves of the fleet cannot reach each other:

| | git repo | published artifact |
|---|---|---|
| Cowork brains (`*/cowork`) | ❌ no shell, cannot run git | ✅ |
| Claude Code workers (`*/claude-code`) | ✅ | ❌ no Artifact tool |

Neither side reaches both. A worker is therefore the only thing that can move a
brain's intent into git, and the only thing that can report a sha back. Nothing a
brain writes reaches the other machine until a worker pushes it — and if the
worker does not push, the other brain is reading a version of reality missing
that work, with no way to detect the gap.

A third transport, the OneDrive folder `ZoltarUnlimited\fleet\`, is readable and
writable by all four agents. It is for messages *about* work. It is not the
source of truth: a decision, work order or finding is real only once it is in
git. A file in OneDrive is a claim; the repo is the evidence.

## The commands

Node-local, defined in each node's `.claude/skills/`. **Not committed** — they
are node-specific because the guards they cooperate with are node-specific. This
file is the shared description of what they do.

| Command | Contract |
|---|---|
| `/bridge-check` | Session start. Rebase-pulls the clone, lists the node-local `bridge/inbox/`, reads anything unprocessed, reports what is actionable and whose lane it is in. **Surfaces only — never acts on a work order, never replies.** |
| `/bridge-reply <slug>` | Writes `bridge/outbox/YYYYMMDD-HHMM-<slug>.md` reusing the inbound slug, then moves the original into `bridge/processed/` verbatim. Reply even when the answer is failure. Paste real output. Mark unverified things unverified. |
| `/node-heartbeat` | Runs `dashboard/fleet_probe.py` with the node's own identity. Fleet lane only — writes `data/fleet/` and nothing else. **Never writes a review cycle.** |
| `/courier-push` | Session end. Commits to a branch, pushes, and reports branch + sha so a brain can flip a bus message from `pending` to `committed <sha>`. User-invocable only. |

## The guards, and what they guarantee to other agents

Enforced by `PreToolUse` hooks, not by the agent's memory. They cannot be
forgotten, and the agent cannot talk itself past them.

| Guard | On `zoltarone` |
|---|---|
| `lane_guard.py` | Denies writes to `src/ scripts/ tests/ docs/ config/` and `data/build/manifest.yaml`; to its own `bridge/inbox/`; and to its node `CLAUDE.md`. |
| `main_guard.py` | Parses git argv and denies any commit or push whose destination is the trunk. Branch-only, always. |
| `inbox_check.py` | `SessionStart`: surfaces unanswered inbox items so the session cannot silently skip them. |

**What `zoltarlead/claude-code` can rely on:** ZoltarOne cannot commit to your
lane and cannot land anything on the trunk. If a change from ZoltarOne appears
in `src/`, `scripts/`, `tests/`, `docs/` or `config/`, a guard was deliberately
relaxed — check `ASSIGNED` in its `lane_guard.py`, which records each exception
with a date and a reason.

ZoltarLead's mirror inverts these: it *owns* the `claude-code` lane and works on
the trunk legitimately, and is instead denied `dashboard/` and
`data/review/cycles/`, which are Cowork's.

## Lane assignment

Decided 2026-09-04: **A with B underneath.**

- **A** — cycle items carry `assignee: claude-code@<node_id>`. `review.py next`
  filters on the local node id. A worker acts only on its own items.
- **B** — `data/fleet/claims/<lane>.json` leases are the safety net beneath it.
  `expires_at = held_since + 2 × cadence`. A worker refuses to start if another
  holds the lane.

Clone and probe are read-only and need no claim.

## Rules that are not negotiable

1. **One writer per path.** In the OneDrive folder, each agent writes only its
   own directory; in `nodes/` and `heartbeat/`, only its own `<node_id>.json`.
   OneDrive resolves two writers to one path by silently making a second copy
   named `<file>-DESKTOP-XXXX`, not by erroring. If one appears, report it — do
   not quietly pick a winner.
2. **One message is one new file.** Never edited after it is written. Never
   deleted. `YYYYMMDD-HHMM-<slug>.md` with a `from`/`to`/`at`/`re` header.
3. **Every push is reported with its sha.** An unreported push is, to every
   other agent, indistinguishable from no push.
4. **A declaration is not evidence.** `built: true` is intent; the run log is
   proof. A node card says what a machine claims to be; only a fresh heartbeat
   proves it is alive. Where something is claimed rather than observed, write
   "unverified" and do not round up.

## Known gaps

- `data/fleet/` was excluded by `.gitignore` (`data/*` with no re-inclusion), so
  `git add data/fleet` silently added nothing and a registered node stayed
  invisible to every other machine. Fixed in the same branch as this file.
- `dashboard/review.py next` crashes on a fresh Windows node with
  `UnicodeEncodeError` — the console codepage is cp1252/IBM437 and the output
  contains `U+2192`. Runs clean under `PYTHONUTF8=1`. Not yet fixed; it is in
  Cowork's lane.
- Node-local skills are not shared. If they should be, they belong in the repo
  under a node-neutral path with the node-specific guards kept out.
