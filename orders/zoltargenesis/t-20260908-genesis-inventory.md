# Order: Inventory the StockPicker lineage on ZoltarGenesis

```json
{ "id": "t-20260908-genesis-inventory", "from": "zoltarlead/cowork", "title": "Inventory the StockPicker lineage on ZoltarGenesis: what it does that zoltar-ranks does not" }
```

## Why

Andrew says this code goes further than the current ranks at the same problem —
ingesting ranks, finding execution biases, recommending actionable changes. A
read-only inventory (modules, entry points, the data it expects, what it outputs)
is the cheapest way to decide what to port into ZoltarUnlimited.

## Acceptance

`docs/genesis-inventory.md` on a branch, containing: a module map, entry points,
inputs and outputs, the overlap with `zoltar-ranks`, and the top three candidates
to port.

## Constraints

- Read-only with respect to the StockPicker tree. Change nothing there.
- **Never open, copy, quote or move credential files** (`.env`, `credentials.py`,
  token stores). If you encounter one, note only its path and that it exists.
- Do not `git init` or push anything from the StockPicker tree.
- Write only into this repository, on a branch. Never touch `main`.

Write the result to `ORDER_RESULT.md` and a one-line summary as the last line of
your output.
