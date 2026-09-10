# Order: Install the ZoltarLead courier — a Claude-free scheduled task that lands bridge/, orders/ and fleet/msgs on fleet/dispatch

```json
{"id": "t-20260908-courier-zoltarlead", "from": "zoltarlead/cowork", "title": "Install a Claude-free courier on ZoltarLead: commit bridge/ + orders/ + fleet/msgs to fleet/dispatch every 10 min"}
```

Approved by Andrew on the board at 2026-09-08T15:21:58-05:00 (event `e-000073-z7kax8`). Class `fleet`.
Node: `zoltarlead`. Lane: `zoltarlead/claude-code`. Repo: `C:\Shared\ClaudeWork\zoltar-ranks`.

## Why

The db→git hop on this node has no carrier. That is the root cause of the five-day split-brain: the brain wrote
cycles and orders for five days and none of them ever left this machine. It is still true right now — as of this
order, `.git/FETCH_HEAD` is 15.8 hours old, `origin/main` in this clone still reads `a3ad2ff` while the board
records a merge to `a246ccf`, and three order files written by the dispatcher are sitting uncommitted in
`orders/`:

- `orders/zoltarlead/c-0002.1.md` — the intraday alignment anchor, whose 1-minute-bar window closes ~2026-09-16
- `orders/zoltarone/t-20260908-executor-dispatch-branch.md`
- `orders/zoltargenesis/t-20260908-genesis-inventory.md`

None of them can reach the node that would run them until something commits and pushes. A scheduled task that
only runs git — never a model — closes the hop deterministically and is the smallest change that unblocks every
other dispatch on the board.

## What to build

A PowerShell scheduled task `\Zoltar\ZoltarLead-Courier`, registered S4U, trigger every 10 minutes plus at boot,
`-StartWhenAvailable`, with an ABSOLUTE path to the interpreter (the fleet has already been bitten by a relative
one). Each run, in this order:

1. `git fetch --prune`
2. `git switch fleet/dispatch` — or `git switch -c fleet/dispatch origin/fleet/dispatch` if it exists on the
   remote, or create it from the current head if it does not
3. `git add orders/ fleet/msgs/ fleet/receipts/ data/fleet/heartbeat/ data/review/`
4. commit only if the index is dirty
5. `git push origin fleet/dispatch`
6. run `scripts/fleet_sync.py` with the venv interpreter
7. write `fleet/receipts/zoltarlead/courier-<UTC-timestamp>.json` recording what it did — **on failure too**, with
   the failing step and the exact git stderr

Constraints that do not move:

- The courier **never touches `main`**. It does not merge, does not rebase onto it, does not push to it. Trunk is
  Andrew's.
- The courier **never runs a model**. No `claude`, no `claude -p`. It is git and a python sync script.
- Do not install anything on this machine beyond the scheduled task itself. No new packages, no new remotes, no
  credential changes.
- Do not read, move, or commit any credential file.
- If push authentication fails, stop and report the exact command Andrew must run. Do not switch transports.
- `docs/courier-protocol.md` and `data/fleet/zoltarone-tooling/` are the closest prior art; read them before
  writing, and say where you departed from them and why.

## Acceptance

Someone who was not here can check all of these:

1. `Get-ScheduledTask -TaskPath '\Zoltar\' -TaskName 'ZoltarLead-Courier'` exists, is S4U, 10-minute repetition,
   at-boot trigger, `-StartWhenAvailable`.
2. A file dropped into the clone (`orders/` or `fleet/msgs/`) appears on `origin/fleet/dispatch` within 10 minutes
   **without anyone running git by hand** — prove it under the scheduler with `Start-ScheduledTask` and again on
   the natural trigger, and paste both shas.
3. The three order files listed above are on `origin/fleet/dispatch` at a real sha.
4. A deliberately broken run (e.g. an unreachable remote) still writes a receipt naming the failing step.
5. `main` is unchanged: `git ls-remote origin main` still reports what it reported before the courier ran.

## Reporting

Write the result to `ORDER_RESULT.md` in the repo root: what was built, the task XML or registration command,
the shas from acceptance 2 and 3, anything that failed, and anything still needing Andrew. Commit the courier
tooling on a branch (`fleet/courier-zoltarlead`), push the branch, and report the sha as **waiting_trunk — not
merged**; only Andrew merges. Also copy the same report to
`fleet\bridge\outbox\<YYYYMMDD-HHMM>-zoltarlead-courier.md` so the dispatcher can mirror it to the board.
End your output with a one-line summary as the last line.
