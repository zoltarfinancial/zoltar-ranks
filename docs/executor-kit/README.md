# ZoltarGenesis order executor — operator card

**Built by** `zoltargenesis/claude-code`, 2026-09-07, for away-mode.
**Read this first if something is wrong and nobody is at the machine.**

```jsonc
"by": { "agent_id": "zoltargenesis/claude-code", "node_id": "zoltargenesis",
        "hostname": "ZOLTARGENESIS", "lane": "claude-code",
        "verified": true, "at": "2026-09-07T13:50:00-05:00" }
```

---

## Stop everything, right now

Create a file called `HALT` in `zoltargenesis:C:\Shared\ZoltarUnlimited\fleet\`.
Any content; the text is echoed into the receipt and the heartbeat so you can
say *why* from wherever you are.

```powershell
"stopping - reason here" | Set-Content C:\Shared\ZoltarUnlimited\fleet\HALT
```

The executor checks for it **first**, before locks, RAM, git, or reading any
order — a stop button that only works when everything else is healthy is not a
stop button. **The heartbeat keeps beating and reports `halted: true`** with the
reason, because "stopped on purpose" and "died" must not look the same from a
phone. Delete the file to resume; nothing else is needed.

**Tested, not assumed.** Both directions were exercised on 2026-09-07 with an
order sitting in the queue: HALT present → executor exited, order untouched,
pulse still `outcome: ok, halted: true`. HALT removed → the same order was
picked up on the next fire.

## The two scheduled tasks

| Task | Cadence | Job |
|---|---|---|
| `\Zoltar\ZoltarGenesis-FleetHeartbeat` | hourly + at-boot (1 min delay) | the pulse. Never runs orders. |
| `\Zoltar\ZoltarGenesis-OrderExecutor` | every 15 min + at-boot (2 min delay) | one order per cycle |

Both run **S4U** as `ZOLTARGENESIS\apod7` — whether or not anyone is logged on,
no stored password. **Deliberately separate tasks:** a hung or refused order
must never be able to stop the pulse, or a stuck order reads as a dead machine.

## Sending an order

Two channels. Both are held to the same allowlist; they differ only in the
provenance recorded on the receipt.

| Channel | Where | Provenance | Reaches it how |
|---|---|---|---|
| **git** | `orders/zoltargenesis/*.md` on any branch pulled into the clone | `authorised` | you commit and push it |
| **inbox** | `zoltargenesis:...\fleet\bridge\inbox\*.md` | `request` | the bound Cowork brain writes it from the dashboard |

git wins when both have work waiting. Order format — the JSON block is optional
and its absence is not an error:

````markdown
# Order: short title

```json
{ "id": "007-thing", "from": "andrew", "title": "short title" }
```

Plain-English instruction. Say what you want and where to put the answer.
Ask for the result in ORDER_RESULT.md at the repo root.
````

## What comes back

Every cycle writes to `fleet\bridge\outbox\`. **A refusal is a receipt too** —
a silent skip is indistinguishable from a dead node.

| Outcome | Means |
|---|---|
| `executed` | ran, gate passed, committed to a branch, pushed. `result.sha` is real. |
| `refused` | allowlist hit, malformed order, or the post-run gate rejected it. `refusal.rule_id` and `refusal.evidence` say exactly why. |
| `halted` | `fleet\HALT` present. Nothing read, nothing consumed. |
| `no_orders` / `skipped` | nothing queued, low RAM, or another instance holds the lock. Goes to the rolling `executor-status.json` rather than a new file each quiet cycle. |
| `error` | executor fault. `errors[]` has it. |

Consumed orders move to `fleet\bridge\consumed\` with a timestamp and the sha or
outcome appended, so they are not re-run and the history is legible.

## Why an order gets refused, and how to fix it

Rules live in `allowlist.ps1` as regexes over the order text. The receipt quotes
**the exact substring that matched**, so a false positive is cheap to fix:
rephrase and resubmit.

| Rule | Refuses |
|---|---|
| `DENY-MONEY` | brokerages, trading, transfers, payments |
| `DENY-CREDENTIALS` | `.env`, credential files, tokens, anything under `StockPicker` |
| `DENY-MAIN` | any write to trunk, merges to main, force-push |
| `DENY-INSTALL` | installing or updating software |
| `DENY-SYSTEM-CONFIG` | power settings, scheduled tasks, services, the identity card, registry |
| `DENY-DESTRUCTIVE` | recursive deletes, `git reset --hard`, dropping tables |
| `DENY-SELF-EXCEPTION` | **an order arguing for its own exception** |
| `DENY-EXFIL` | sending data off the machine outside git |
| `MALFORMED-EMPTY` | empty or unreadable order file |
| `GATE-FAILED` | the post-run inspection rejected the result; nothing pushed |
| `DENY-UNATTESTED` | the node could not attest its own identity |

The rules are **blunt on purpose**. They match on intent and will occasionally
refuse an innocent phrasing. On an unattended machine that is the right trade: a
refused order costs one cycle and produces a receipt naming the phrase to
rewrite; a wrongly-executed order costs whatever it touched, with nobody
watching.

`DENY-SELF-EXCEPTION` deserves its own line: **an order that argues it should be
exempt is refused by that fact.** "Andrew approved an exception", "this is
pre-approved", "ignore the allowlist just this once", "don't write a receipt" —
all refused. Nobody can confirm such a claim while Andrew is away, so the only
safe reading is to decline.

## Three layers, because none alone is enough

1. **Static screen** — the order text is matched against the rules **before
   `claude` is launched at all**. A hit means the agent never starts. (Proven:
   the credentials-exfil test order shows `claude exit: NEVER LAUNCHED`.)
2. **Runtime confinement** — `--permission-prompts none` so anything that would
   prompt is **denied** rather than waiting for a human who is not coming;
   `--disallowed-tools` removing web egress and every git write; `--add-dir`
   confining file tools to the repo; `--max-budget-usd` capping spend; a
   wall-clock kill.
3. **Post-run gate** — after the agent exits and **before anything is pushed**:
   HEAD is not main, `origin/main` did not move, every changed path is inside
   the writable set, and a size+mtime witness over protected files (including
   the `StockPicker` credential files, the identity card, and the executor's own
   scripts) is unchanged. Fail any of it and nothing is pushed; the tree is left
   for inspection.

> **The agent never pushes.** It works in the tree; the executor inspects and
> then commits and pushes. That is the difference between "we asked it not to"
> and "it cannot".

## Limits already set

| Limit | Value | Why |
|---|---|---|
| concurrent orders | **1** | lock file + `MultipleInstances=IgnoreNew` |
| wall clock per order | 900 s | this node took 97 s just to heartbeat under load |
| spend per order | $2.00 | a runaway loop costs a bounded amount |
| free-RAM floor | 0.5 GB | 7.92 GB machine; it thrashes. Under the floor it refuses to start and says so, and the order stays queued. |

## When you get back

- `fleet\bridge\consumed\` is the order history.
- `fleet\executor\last-run.json` is the most recent cycle, whatever it was.
- Branches are `orders/zg-<order-id>-<timestamp>`. **None of them are on main.**
  Applying them is yours.
