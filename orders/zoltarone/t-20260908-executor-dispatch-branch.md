# Order: Executors take orders from origin/fleet/dispatch, not only from main

```json
{ "id": "t-20260908-executor-dispatch-branch", "from": "zoltarlead/cowork", "title": "Executors take orders from origin/fleet/dispatch, not only from main" }
```

Approved by Andrew on the board at 2026-09-08T14:41:59-05:00.

## Why

Today `run-order.ps1` reads `orders/<node>/` from the working tree and only pulls
when the clone is on `main` — so every dispatch needs a trunk merge, which is the
single thing that blocks autonomy. Reading orders from a fetched `fleet/dispatch`
ref (`git show origin/fleet/dispatch:orders/<node>/...`) makes dispatch trunk-free
while `main` stays Andrew's.

## What to change

In `run-order.ps1`, after `git fetch`:

- enumerate `git ls-tree --name-only origin/fleet/dispatch -- orders/<node>/`
- materialise each order with `git show` into a scratch directory
- treat them as channel `dispatch`, provenance `authorised`
- keep the existing `main` and `inbox` channels working unchanged
- record consumed order ids under `fleet/bridge/consumed/` so an order still
  present on the branch is not re-run

## Acceptance

On ZoltarOne and ZoltarGenesis, an order pushed to `fleet/dispatch` is consumed
within one cycle with provenance `dispatch` on the receipt, and nothing on `main`
changed.

## Constraints

- Never write to `main`. Push your work to a branch and leave the merge to Andrew.
- The allowlist is the floor; the HALT file wins.
- Do not read or write credential files.

Write the result to `ORDER_RESULT.md` and a one-line summary as the last line of
your output.
