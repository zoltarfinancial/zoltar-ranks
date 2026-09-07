# orders/zoltarone

The **authorised** order channel for node `zoltarone`. An order here was
committed and pushed by someone with push rights, which is what earns it
`provenance: authorised` on the receipt.

Drop a `*.md` file here, commit, push. The executor takes one per cycle, git
channel before inbox, filename order within a channel.

```markdown
# Order: short title

```json
{ "id": "001-thing", "from": "andrew", "title": "short title" }
```

Plain-English instruction. Ask for the result in ORDER_RESULT.md at the repo root.
```

The JSON block is optional; its absence is not an error. Every order is screened
against `fleet\executor\allowlist.ps1` **before** the model is launched, and the
result is checked again by a post-run gate before anything is pushed. The agent
never pushes; the executor does.

Consumed orders move to `fleet\bridge\consumed\` with the sha or outcome
appended. Receipts land in `fleet\bridge\outbox\` and on the
`fleet/receipts-zoltarone` branch.
