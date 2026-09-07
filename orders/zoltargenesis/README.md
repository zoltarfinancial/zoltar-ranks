# Orders for zoltargenesis

Drop a `.md` order file here, commit it, push. The ZoltarGenesis order executor
pulls this directory every cycle and treats anything here as **authorised** -
someone with push rights committed it.

`fleet\bridge\inbox\` is the other channel; orders there are treated as
**requests** (the bound Cowork brain placed them from the dashboard). Both are
held to the same allowlist in `fleet\executor\allowlist.ps1`; the difference is
recorded in the receipt.

Receipts come back to `fleet\bridge\outbox\`. A refusal is a receipt too.
Kill switch: create `fleet\HALT`.
