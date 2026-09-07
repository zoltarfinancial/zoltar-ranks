# Identity kit — bringing a new node's identity guard online

**Origin:** `zoltargenesis/claude-code`, 2026-09-07. Packaged because
ZoltarGenesis is currently the only node with a working guard and the next two
will copy it. **Copy this directory; do not re-derive it.**

```jsonc
"by": { "agent_id": "zoltargenesis/claude-code", "node_id": "zoltargenesis",
        "hostname": "ZOLTARGENESIS", "lane": "claude-code",
        "verified": true, "at": "2026-09-07T13:40:00-05:00" }
```

---

## What this solves

A path identifies a folder. It never identifies a machine.
`C:\Shared\ZoltarUnlimited` is the workspace on **ZoltarGenesis** and the clone
root on **ZoltarOne**; `C:\Shared\ZoltarUnlimited\zoltar-ranks` is the repo root
on both. Sync a workspace to a new laptop and every file in it, node card
included, now claims to be the original node. Nothing inside the folder can
detect that.

The fix is to bind the copyable thing to an uncopyable one: `identity.json`
carries the hostnames the node may answer to; the session reads the live
hostname and compares. **The file supplies meaning, the hostname supplies proof,
and the match is what makes it evidence.**

## Files

| File | What it is |
|---|---|
| `identity.schema.md` | the v1 and v2 card schemas, the `rename_pending` block, and the three-state `verified` rule |
| `heartbeat.ps1` | the working guard + heartbeat, parameterised. **Change only the param block.** |
| `verify-guard.ps1` | seven fixtures that prove the guard before you trust a single stamp |

## Do this, in order

### 1. Read the live hostname on the machine itself

```powershell
$env:COMPUTERNAME          # Windows reports this UPPERCASE
hostname.exe               # often mixed case
[System.Net.Dns]::GetHostName()
```

**Expect them to disagree on case.** On ZoltarGenesis, one session got
`ZOLTARGENESIS`, `ZoltarGenesis` and `ZoltarGenesis` from those three calls.
Every comparison in this fleet is case-insensitive for that reason — a
case-sensitive check fails on a *healthy* node, which is the worst kind of
guard: one that fires on the good case.

### 2. Write `<workspace>\fleet\identity.json`

Use the v2 shape in `identity.schema.md`. `attested_hostnames` gets the name you
just read. **Do not copy another node's card and edit it from memory** — read
the hostname on the machine and put that value in.

### 3. Install `heartbeat.ps1` and change only the param block

```powershell
param(
    [string]$NodeId    = 'CHANGEME',   # <- must match node_id in identity.json
    [string]$Lane      = 'claude-code',
    [string]$Workspace = 'C:\Shared\ZoltarUnlimited',
    [string]$RepoRoot  = 'C:\Shared\ZoltarUnlimited\zoltar-ranks',
    [string]$RemoteUrl = 'https://github.com/zoltarfinancial/zoltar-ranks',
    [string]$FireKind  = 'scheduled'
)
```

It writes `<Workspace>\fleet\heartbeat\<NodeId>.json`. Nothing below the param
block is node-specific — this was verified by running the template with
`-NodeId testnode` and confirming it produced `testnode.json` stamped
`testnode/claude-code`.

**It uses no WMI, deliberately.** ZoltarGenesis has no `Win32_*` classes at all
(F-ZG-4), so CPU and RAM come from perf counters, power from a `kernel32`
P/Invoke, and disk from the Storage namespace. All four work on a node whose WMI
is broken; the WMI path does not. Do not "improve" it back onto `Win32_*`.

### 4. Prove the guard before trusting any stamp

```powershell
pwsh -File verify-guard.ps1 -NodeId <your-node-id>
```

Seven fixtures, about a minute, exit 0 on success. **Do not skip this.** It
builds throwaway workspaces in temp and never touches your real card.

### 5. Only then schedule it

Match ZoltarOne's and ZoltarGenesis's shape: hourly, plus **at-startup with a
1-minute delay**, `-StartWhenAvailable`, **S4U principal** so it runs whether or
not a user is logged on with no stored password.

```powershell
$t1 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Hours 1)
$t2 = New-ScheduledTaskTrigger -AtStartup; $t2.Delay = 'PT1M'
$p  = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
```

The boot trigger is what turns a reboot into a liveness test rather than an
outage. On ZoltarGenesis it fired **3 min 12 s after a cold boot, unattended**.

## The four contracts the script keeps

Each exists because the fleet has been bitten:

1. **It always writes.** The write is in a `finally`. A fire that throws still
   stamps, with `outcome` and the error. *A node that only heartbeats when
   healthy is indistinguishable from one that is off.*
2. **Every metric fails independently.** One dead counter cannot null out the
   other five, and each failure is named in `errors` rather than silently
   becoming a null.
3. **Null is not zero, and null is not false.** Unmeasured is `null` and appears
   in `errors`. Nothing emits `0` to mean unknown.
4. **It re-attests every fire.** If the workspace is copied elsewhere, its
   stamps say `verified: false` instead of impersonating this node.

A fifth, learned the hard way on 2026-09-07: **`notices` are not `errors`.** A
satisfied `rename_pending` needs a follow-up action but is not a failure, so it
goes in `notices` and leaves `outcome: ok`. Routing it through the error channel
made a successful rename render as `degraded` — the same conflation the rest of
this design exists to prevent.

## Two traps this kit exists to stop you repeating

**Do not assume a card is at the schema a document says it is.** Handoff-02
stated ZoltarGenesis's `identity.json` "is now schema v2". It was still v1, and
the v2 field names existed nowhere on the machine except inside that document.
`grep` for the field names before writing any code that consumes them. KB
lesson: `handoff__described-intent-as-completed-state`.

**Do not describe a correct refusal as a false positive.** When the machine was
renamed and the card still attested only the old name, the guard refused —
correctly. Stale data, correct logic. Calling that a false positive invites
someone to weaken the one mechanism that worked.

## Fleet state as of 2026-09-07

| Node | Card schema | Guard | Hostname known |
|---|---|---|---|
| `zoltargenesis` | **v2** | ✅ proven, 7/7 fixtures | `ZOLTARGENESIS` |
| `zoltarone` | v1 | not installed | `DESKTOP-7FJV5QQ` (second-hand, unverified on the machine) |
| `zoltarlead` | none | not installed | **recorded nowhere** — see `data/fleet/hostmap.json` |

The v1 fallback in `heartbeat.ps1` exists precisely because of that middle row.
Keep it until every node is v2.
