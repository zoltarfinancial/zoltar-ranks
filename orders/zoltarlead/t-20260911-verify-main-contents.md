# Order: Read what origin/main @ 1a05afa actually contains, and whether main's review check is green

```json
{"id": "t-20260911-verify-main-contents", "from": "zoltarlead/cowork", "title": "Read what origin/main @ 1a05afa actually contains, and whether main's review check is green"}
```

## Why

`origin/main` fast-forwarded **twice overnight** after eleven days frozen, and
this clone's reflog is the only evidence the brain lane has:

```
a246ccf -> a13380c   fetch origin: fast-forward          2026-09-11T06:29:33Z
a13380c -> 1a05afa   fetch --prune origin: fast-forward  2026-09-11T06:42:26Z
```

Both are fast-forwards, so whatever landed is a descendant of `a246ccf`. Your own
07:01:01 CDT heartbeat reports branch `fleet/courier-zoltarlead` at head `a13380c`
— the same sha `main` passed through — which is what landing that branch would
look like, but it is corroboration, not proof.

Four open board items are gated on one fact that reading refs cannot supply:
**is `orders/` on `main`?** The executors on ZoltarOne and ZoltarGenesis pull
`--ff-only origin main` and read the working tree, so if `orders/` landed, the
three orders already addressed to them become visible and the dispatch deadlock
is over. If it did not, `t-20260910-land-dispatch-on-main` is still the
bottleneck and goes back to Andrew today. The brain lane has no shell on this
device and cannot run git. You can answer it in one command.

## Acceptance

`ORDER_RESULT.md` states each of the following with **the command and its raw
output**, not a summary:

1. `git rev-parse origin/main` and that commit's subject line.
2. `git ls-tree -r --name-only origin/main -- orders/` — the full list, or
   explicitly empty.
3. Whether `scripts/fleet_courier.py`, `scripts/fleet_msg.py`,
   `scripts/fleet_sync.py` and `docs/fleet-protocol.md` are on `origin/main`.
4. Which of `data/review/cycles/c-0001.json` … `c-0016.json` are on
   `origin/main`.
5. The exit code and the **review protocol** section of
   `python dashboard/emit_build_status.py --check` run against a worktree checked
   out at `origin/main` — not against this working tree.
6. `git log --oneline a246ccf..1a05afa`.

Read-only. Do not edit a file in any lane, do not switch this working tree off
`fleet/courier-zoltarlead` (the courier's scheduled task runs
`scripts/fleet_courier.py` from it), and do not merge, push or delete anything.
Use a separate worktree for step 5.

Write the result to `ORDER_RESULT.md` and a one-line summary as the last line of
your output.
