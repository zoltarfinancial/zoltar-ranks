# `identity.json` — schema v1 and v2

**Origin:** `zoltargenesis/claude-code`, 2026-09-07, lifted from the working v2
card on ZoltarGenesis. Extends `docs/node-identity-protocol.md`.

The card answers one question: **which node am I, and am I allowed to act as
it?** It lives at `<workspace>\fleet\identity.json` and is read at the start of
every session and every heartbeat fire.

---

## The rule underneath both versions

| Fact | Strength | Weakness |
|---|---|---|
| `identity.json` in the workspace | says the node's name, roots, lanes, transports | **copyable** — travels with a synced folder |
| the live hostname | **not copyable** — a machine property | says nothing about roles |

Bind them. The card supplies meaning, the hostname supplies proof, the match is
what makes it evidence. Neither alone is worth anything.

## v2 — current

```jsonc
{
  "schema_version": 2,
  "node_id": "zoltargenesis",
  "alias": "ZoltarGenesis",

  // ARRAY. Match the live hostname case-insensitively against ANY entry.
  "attested_hostnames": ["ZOLTARGENESIS"],

  // Names this machine used to answer to. NOT attested - kept so that
  // historical rows, stamps and findings written before a rename still resolve
  // to this node. A card that still attested an old name would let a stale
  // clone verify against a name the machine no longer has.
  "_former_hostnames": ["DESKTOP-N0SMR7I"],
  "renamed_at": "2026-09-07T04:28:10-05:00",

  "workspace_root": "C:\\Shared\\ZoltarUnlimited",
  "repo_root": "C:\\Shared\\ZoltarUnlimited\\zoltar-ranks",
  "lanes": ["cowork", "claude-code"],
  "transports": ["git", "artifact_db"],

  "rename_pending": null,        // see below
  "verification_rule": "..."     // prose restatement, for a human reading the card
}
```

### Why an array

v1's singular `attested_hostname` cannot express a machine that is legitimately
known by two names across a rename. It forces a choice between a guard that
trips on a healthy node and one that has been switched off during the change —
and the second is how guards quietly stay off.

## v1 — legacy, still live on ZoltarOne and ZoltarLead

```jsonc
{
  "schema_version": 1,
  "node_id": "zoltarone",
  "attested_hostname": "DESKTOP-7FJV5QQ"   // SINGULAR
}
```

**Consumers must keep the v1 fallback.** Read `attested_hostnames` first; if it
is absent or empty, fall back to the singular `attested_hostname`. A guard that
only understands v2 fails closed on every node that has not migrated, which is
most of the fleet.

## `rename_pending` — authorising a rename in advance

Set this **before** renaming the machine, so the guard treats the new name as a
match rather than an intrusion.

```jsonc
"rename_pending": {
  "from": "DESKTOP-N0SMR7I",
  "to": "ZOLTARGENESIS",
  "authorised_by": "andrew",
  "authorised_at": "2026-09-07T04:00:00-05:00",
  "expires_at": "2026-09-14T00:00:00-05:00"
}
```

### Consumer rules

1. Live hostname equals `rename_pending.to` (case-insensitive) → **MATCH**,
   `verified: true`. Emit a **notice**, not an error, telling the operator to
   complete the rename.
2. `expires_at` has passed **and** the live hostname is still
   `rename_pending.from` → `verified: false`, and say the rename expired. An
   expired authorisation is not an authorisation.
3. On first observation of `rename_pending.to`, **complete the rename**: prune
   `attested_hostnames` to the new name, move the old name into
   `_former_hostnames`, set `renamed_at`, and set `rename_pending` to null.

> **A satisfied rename is `outcome: ok`.** The follow-up instruction belongs in
> a `notices` array. Putting it in `errors` degrades the outcome and makes a
> success read as a fault — a real bug found and fixed on 2026-09-07.

## The three states of `verified` — never two

| `verified` | Meaning | Render as |
|---|---|---|
| `true` | hostname read, matched an attested name | node badge, normal |
| `false` | hostname read, matched **nothing** | **red** — an agent wrote while claiming a node it is not on |
| `null` | hostname or card **could not be read** | **amber, "unattested"** — not green, not red |

`null` is the one people want to drop. Don't. A session whose device is offline
can still write, and a row from it is genuinely unattested. Rendering that as
verified is the evidence-replaced-by-claim substitution this console has already
been audited for repeatedly. Collapsing it into `false` is the opposite error:
it accuses a healthy node of impersonation because a file was briefly locked.

## Case sensitivity is a bug, not a rule

One machine, one session, three spellings:

```
$env:COMPUTERNAME            -> ZOLTARGENESIS
hostname.exe                 -> ZoltarGenesis
[Net.Dns]::GetHostName()     -> ZoltarGenesis
```

and the device bridge reports lowercase again. **Compare case-insensitively
everywhere**, including in `data/fleet/hostmap.json` lookups.

## The `by` stamp every write carries

```jsonc
"by": {
  "agent_id": "zoltargenesis/claude-code",
  "node_id":  "zoltargenesis",
  "hostname": "ZOLTARGENESIS",      // as read LIVE, this session
  "lane":     "claude-code",
  "verified": true,                  // true | false | null, per the table above
  "at":       "2026-09-07T13:40:00-05:00",

  // added 2026-09-07: when a guard failed, nobody could tell WHICH card it had
  // read. A stamp that names its own source settles that without re-derivation.
  "identity_file":   "C:\\Shared\\ZoltarUnlimited\\fleet\\identity.json",
  "identity_schema": 2
}
```

`verified` is the honest part, and it is `true` **only if you actually compared
the hostname this session**. If you could not read it, it is `null` — not
`true`. An unattested row must be able to say so.
