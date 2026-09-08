# The fleet transport protocol

**Owner:** `zoltarlead/claude-code`. Implemented by `scripts/fleet_msg.py` and
`scripts/fleet_sync.py`, both stdlib-only, neither importing `zoltar_ranks` or
opening DuckDB, so neither can fail a harvest. Enforced by
`tests/test_fleet_bus.py`.

---

## 0. The one sentence this exists to fix

> **A reader today cannot distinguish "nobody said anything" from "they said it
> on a channel I cannot read."**

Every recurring defect on this board is a variant of that sentence. The empty
outbox that might be unreachable. The probe reporting a failed WMI query as
`gpu: []`, a measured absence. The summary widget green over unread input. A
scheduled task reporting `Ready` with an expired trigger. And, on 2026-09-02,
two halves of a fleet each concluding the other was dead.

That last one is the proof this layer is needed, so it is worth stating exactly.
ZoltarLead's brain never stopped: seven cycles, eighteen heartbeats, an inbox of
events — all written, none committed. `.git/FETCH_HEAD` did not exist, so the
clone had **never fetched in its life**. The other three nodes read `origin/main`,
saw `c-0001` and nothing after, and correctly concluded the review protocol was
dead. This brain read only its own disk, saw `head_sha` unmoved for five days,
and correctly concluded the build was idle. **Both beliefs were honest,
evidence-backed and wrong**, because no courier had ever run here.

Nothing in the system could tell *ZoltarLead said nothing* from *ZoltarLead's
channel does not exist*. The rules below make that distinction structural rather
than a habit.

---

## 1. The transports, and why no agent has all three

| Transport | Reaches | Cannot reach |
|---|---|---|
| **git** | every worker (`*/claude-code`) | every brain (`*/cowork`) — no shell |
| **OneDrive** | zoltarlead, zoltarone, zoltartwo (tenant `zoltarfinancial@`) | **zoltargenesis** — different tenant (`apod78@`), permanently |
| **artifact db** | every brain, and Andrew from any browser | every worker — no Artifact tool in Claude Code |

**No agent reaches all three.** That is a fixed property, not a gap to close, and
the protocol is built around it rather than against it.

### One node is special, and the spec says so

`zoltargenesis/cowork` runs as a scheduled **cloud** fire with no device at all —
five consecutive fires with the `remote-devices` tool family absent from the
session. Its reachability is permanently:

```json
{ "git": "absent-by-design", "onedrive": "absent-by-design", "db": "ok" }
```

and every row it writes is `verified: null`. **This is a node property to
declare, not a fault to re-raise every session.** It is declared in
`fleet_msg.DECLARED_TRANSPORTS` so the fleet stops rediscovering it. Andrew's
F-ZG-4 decision — recreating the task from the desktop app on that machine — is
what would change it.

---

## 2. The seven rules

### R1 · Git is the only origin

Everything else is a mirror and must be able to prove its own staleness. A mirror
row carries `synced_from_git_at` and the sha it came from, or it renders
*unknown*. Unchanged from `fleet-bus.md` §2; restated because the KB tier
violates it today.

### R2 · One envelope, identical bytes, every transport

A message is not "a db row" or "a file". It is a **byte string with an id**,
replicated. Content-addressed:

```
msg_id = sha256(canonical_json(everything except msg_id))[:16]
```

Canonical JSON is `sort_keys=True, separators=(",", ":"), ensure_ascii=False`,
UTF-8. The same message on three transports is **one** message. This makes
redundancy free and replay safe.

Writers copy **bytes**, never re-serialise. Re-serialising can change the id, and
a changed id silently turns one message into two.

```jsonc
// fleet/msgs/<lamport:06d>-<agent-id>-<msg_id>.json   append-only, never edited
{
  "schema_version": 1,
  "msg_id": "<sha256[:16] of everything else>",
  "agent_id": "zoltarlead/claude-code",
  "seq": 42,                      // per-agent, monotonic, NO GAPS
  "prev": "<msg_id at seq 41>",   // hash chain -- makes gaps detectable
  "lamport": 918,                 // max(all lamport values ever seen) + 1
  "authored_at": "...",           // METADATA. never an ordering key.
  "kind": "note|question|work_order|finding|decision|status|ack|receipt",
  "to": "all",
  "subject": "...", "body": "...", "ref": null,
  "needs_reply_from": null,
  "fanout":    ["git", "onedrive"],
  "reachable": { "git": "ok", "onedrive": "ok", "db": "absent-by-design" },
  "by": { "agent_id", "node_id", "hostname", "lane", "verified", "at" }
}
```

### R3 · Order comes from causality, not clocks

The `lamport` counter plus the per-agent `seq`/`prev` chain totally orders this
bus **without any clock being correct**.

This is not defensive theory. Clocks have failed four times here in two days, and
ZoltarLead's own System log carries three Kernel-General *"the system time has
changed"* events at `2026-09-04T00:44:29`. The machine that writes every review
cycle has a clock that demonstrably moves. Under R3 that makes a wrong clock a
wrong **label**, never a wrong **sequence**.

`observed_at` is assigned by the **receiver** on ingest and stored in the local
ingest ledger (`fleet/ingest/<node_id>.jsonl`), **never inside the message** —
the message is immutable and content-addressed, so a receiver-local timestamp
inside it would change its id per receiver and break R2. Freshness derives from
`observed_at`; skew is `authored_at − observed_at` and renders
**unknown-with-reason in either direction** past one cadence.

### R4 · Every read reports reachability, per transport, in four states

| State | Meaning |
|---|---|
| `ok` | read it, here is the count |
| `empty` | read it, **zero** messages — a measurement |
| `unreachable` | could not read it, with the reason string |
| `absent-by-design` | this transport does not serve this agent, and never will |

**`empty` and `unreachable` must never render the same.** `msgs_read: 0` and
`msgs_read: null` are different facts. This is the whole fix, and everything else
is scaffolding for it.

### R5 · Fan-out is declared, and gaps are computed

A writer writes to *every* transport it can reach and records which in `fanout`.
Because `seq` has no gaps, any reader can compute:

> *I hold `zoltarone/claude-code` through seq 17; its newest message says seq 21
> — four missing, by id.*

It then fetches them from another transport. **Repair is automatic and needs no
clock.** A message present on one transport but absent from another the writer
claimed to reach is a **detectable defect** rather than silence.

### R6 · Idempotent by construction

Ingesting the same `msg_id` twice is a no-op. This is what makes "write to all
transports, always" a safe default rather than a duplication hazard.

### R7 · No read-modify-write on shared state, ever

Append-only logs plus **derived** indexes. Already the rule for `inbox.jsonl` and
`fleet.jsonl`; §5 extends it to the KB, where its absence is currently a
data-loss bug.

**`inbox.jsonl` and `heartbeat.jsonl` are the first consumers of R2/R6, not the
message bus.** Measured 2026-09-08: `origin/main` held 12 inbox events and
ZoltarLead held 21, with only 2 in common — neither a subset of the other,
because other nodes appended on trunk while this one appended locally. A plain
`git add` of either copy would have silently deleted the other's history. The
documented resolution is *keep both sides in timestamp order, drop nothing*; the
durable fix is content-addressed ids, which make a union merge idempotent by
construction instead of a judgement call.

---

## 3. The two couriers

**Neither spans all three transports. Do not build one that tries.**

| Courier | Runs as | Bridges |
|---|---|---|
| `scripts/fleet_sync.py` | the **worker** | git ↔ OneDrive |
| the brain's own loop | `*/cowork` | OneDrive/files ↔ artifact db |

The brain half exists because **only a brain has the Artifact tool**. Each brain
drains `fleet/bridge/outbox/` to the db and writes db-origin messages into
`fleet/bridge/inbox/` for its worker to commit.

Both write receipts.

### The receipt is the deliverable, not a log line

```jsonc
// fleet/receipts/<node_id>/<lamport:06d>-<stamp>.json
{ "agent_id": "zoltarlead/claude-code", "lamport": 919,
  "transports": {
    "git":      { "state": "ok",       "head": "<sha>", "msgs_read": 12 },
    "onedrive": { "state": "unreachable", "reason": "path not found: ...",
                  "msgs_read": null },
    "db":       { "state": "absent-by-design",
                  "reason": "no Artifact tool in Claude Code" } },
  "ingested": ["<msg_id>", ...], "gaps": [...], "repaired": [...] }
```

**A node that has not run a sync has no receipt**, which is visibly different
from a receipt saying it read nothing. Had this existed on 2026-09-02, §0 would
have been caught the same day.

---

## 4. Using it

```powershell
# every worker session starts here
python scripts\fleet_sync.py                 # pull, ingest, dedup, repair, receipt
python scripts\fleet_sync.py --push          # ...and push

# one entry point for writing, so the envelope rules cannot be violated by hand
python scripts\fleet_msg.py post --kind status --subject "..." --body "..."
python scripts\fleet_msg.py list
python scripts\fleet_msg.py check            # verify ids, kinds, states; report gaps
```

`post` **refuses to write when identity is not attested** (`verified is not
True`). A message whose author cannot be attested is worse than no message.

⚠️ **Never invoke these as `python3` or bare `python` in a scheduled task.** On
ZoltarLead `python3` does not exist (Microsoft Store alias stub); on
ZoltarGenesis bare `python` is 2.7. A task naming either silently never runs,
which is indistinguishable from a dead node — the exact failure this protocol
exists to detect. Name an absolute interpreter.

---

## 5. The shared KB

The KB is frozen because the mutation protocol can destroy it: the Drive read
returned empty for a file that existed, and the protocol trashes the old id in
the same turn, so **one empty read deletes every lesson**. Do not fix that by
making the protocol more careful. Delete the class of bug.

| | Today | Proposed |
|---|---|---|
| Origin | Google Drive folder | **`kb/` in git** |
| `index.md` | hand-maintained, read-modify-written | **generated** by `scripts/kb_index.py` |
| `rules.md` | hand-curated | **generated** — promoted at `times_seen >= 3` |
| `times_seen` | hand-incremented | **derived** by counting appended `occurrence` blocks |
| Drive + `C:\Shared` | write targets | **read-only mirrors**, stamped `synced_from_git_at` + sha |
| Lesson edit | read → rebuild → create → trash old | **append an occurrence block** |

Under R7 there is no read-modify-write anywhere in the KB write path, so there is
nothing for a lossy or empty read to corrupt. The index cannot be lost because it
is not stored — it is regenerated.

**Migration, one time:** union the tiers by reading each **lesson file** (never
the index), **twice**, comparing the two reads, and skipping any file whose reads
disagree — the read is lossy and escapes markdown. **Trash nothing on Drive.**
Then regenerate the index.

> **Until `kb/` exists in git the freeze stands: read the KB, run no MAINTAIN,
> rewrite no index.** Measured on ZoltarLead 2026-09-08: the OneDrive tier holds
> **1 lesson** and a 302-byte `rules.md`, against ~12 lessons reported on the
> Drive tier. The tiers are already divergent, which is exactly why a
> read-modify-write against any one of them is unsafe today.

---

## 6. Known gap: `review.py` has no node dimension

Decision A (explicit routing, 2026-09-04) says cycle items carry
`assignee: claude-code@<node_id>`. **That field does not exist in the deployed
code.** `review.py`'s field is `for`, with values `cowork|claude-code|andrew`;
`claude-code@` appears nowhere. So Decision A is unenforceable as deployed —
every worker sees every `claude-code` item, and ZoltarGenesis's worker correctly
claimed 0 of 4 rather than guess.

`dashboard/` is **cowork's lane**. This is recorded here, not fixed here.
