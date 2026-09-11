# ORDER_RESULT — t-20260908-courier-zoltarlead

**Order:** Install a Claude-free courier on ZoltarLead (approved by Andrew
2026-09-08T15:21:58-05:00, board event `e-000073-z7kax8`, class `fleet`).
**Executed by:** `zoltarlead/claude-code`, 2026-09-10, on `ZOLTARLEAD`.
**Tooling branch:** `fleet/courier-zoltarlead` — **waiting_trunk, not merged.** Only Andrew merges.

## Result

The courier is installed, running under the scheduler, and all five acceptance
checks pass with real shas below. It lands this node's dispatch paths on
`origin/fleet/dispatch` every 10 minutes with no model and no human running git.
`main` was not touched.

| # | Acceptance | Result |
|---|---|---|
| 1 | Task exists: S4U, 10-min repetition, at-boot, StartWhenAvailable | **PASS** |
| 2 | A dropped file lands within 10 min, under the scheduler | **PASS** — `88899a3` (Start-ScheduledTask), `2a1c797` (natural trigger) |
| 3 | The three order files are on `origin/fleet/dispatch` at a real sha | **PASS** — all three byte-identical at `88899a3` |
| 4 | A deliberately broken run still writes a receipt naming the failing step | **PASS** — `failed_step: 1.fetch`, exact stderr; that receipt itself landed at `b35437f` |
| 5 | `main` unchanged | **PASS** — `a246ccf` before and after |

## Preconditions checked before building

| The order said | Measured |
|---|---|
| `FETCH_HEAD` 15.8 h old | **~64 h** old (2026-09-08 00:22 → 2026-09-10 16:49). Older, not fresher. |
| `origin/main` in this clone reads `a3ad2ff`; board says `a246ccf` | Both true. Local ref was stale at `a3ad2ff`; `git ls-remote` gave **`a246ccf`**. The board was right. |
| Three order files uncommitted in `orders/` | All three present, untracked. |
| — | `origin/fleet/dispatch` **did not exist**. The courier created it. |
| — | My rejoin branch `9115058` **is now in `main`** (`a246ccf` is the merge of PR #3). |

## What was built

| File | What |
|---|---|
| `scripts/fleet_courier.py` | The courier. Stdlib only. Runs git and `scripts/fleet_sync.py`; never a model. |
| `scripts/install_courier.ps1` | Registers the task and **fails loudly** unless interval=PT10M, duration empty, S4U, StartWhenAvailable. |
| `tests/test_fleet_courier.py` | 22 tests against a real throwaway bare remote. All pass. |

Nothing else was installed. No packages, no remotes, no credential changes, no
credential file read. Two local artifacts outside the repo, both git-managed or
plain logs:

- `C:\Shared\ClaudeWork\zoltar-ranks.courier-wt\` — the courier's git worktree
- `C:\Shared\ClaudeWork\zoltar-ranks.courier-wt.runs.jsonl` — one line per fire

### Registration

```powershell
.\scripts\install_courier.ps1
```

which does, in effect:

```powershell
$action  = New-ScheduledTaskAction -Execute C:\Shared\ClaudeWork\zoltar-ranks\.venv\Scripts\python.exe `
             -Argument '"C:\Shared\ClaudeWork\zoltar-ranks\scripts\fleet_courier.py"' `
             -WorkingDirectory C:\Shared\ClaudeWork\zoltar-ranks
$every10 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 10)
$boot    = New-ScheduledTaskTrigger -AtStartup; $boot.Delay = 'PT2M'
$p       = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited
$s       = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 9) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskPath '\Zoltar\' -TaskName 'ZoltarLead-Courier' -Action $action `
  -Trigger @($every10,$boot) -Principal $p -Settings $s
```

### Task XML (exported)

```xml
<Principal id="Author"><UserId>S-1-5-21-1418099759-1048462268-650730769-1002</UserId><LogonType>S4U</LogonType></Principal>
<ExecutionTimeLimit>PT9M</ExecutionTimeLimit>
<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
<StartWhenAvailable>true</StartWhenAvailable>
<TimeTrigger>
  <StartBoundary>2026-09-10T17:02:24-05:00</StartBoundary>
  <Repetition><Interval>PT10M</Interval><StopAtDurationEnd>true</StopAtDurationEnd></Repetition>
</TimeTrigger>
<BootTrigger><Delay>PT2M</Delay></BootTrigger>
<Exec>
  <Command>C:\Shared\ClaudeWork\zoltar-ranks\.venv\Scripts\python.exe</Command>
  <Arguments>"C:\Shared\ClaudeWork\zoltar-ranks\scripts\fleet_courier.py"</Arguments>
  <WorkingDirectory>C:\Shared\ClaudeWork\zoltar-ranks</WorkingDirectory>
</Exec>
```

`<Repetition>` has an `Interval` and **no `<Duration>` element**, which means
indefinite. This is checked on purpose: the fleet's 2026-09-01 outage was a
`-Once` trigger with a *bounded* 14h30m duration that expired after one day while
the task still read `Ready`. `ZoltarFleetHeartbeat` uses this exact unbounded shape
and had fired hourly for 2.5 days with 0 missed runs when this was installed.

## Acceptance evidence

### 1 — the task

```
TaskPath/Name     : \Zoltar\ZoltarLead-Courier
Principal         : owner  LogonType=S4U  RunLevel=Limited
StartWhenAvailable: True   MultipleInstances=IgnoreNew   ExecutionTimeLimit=PT9M
Action            : C:\Shared\ClaudeWork\zoltar-ranks\.venv\Scripts\python.exe "...\scripts\fleet_courier.py"
Trigger           : MSFT_TaskTimeTrigger  interval=PT10M  duration=''  enabled=True
Trigger           : MSFT_TaskBootTrigger  delay=PT2M      enabled=True
LastRunTime=09/10/2026 17:12:25  LastTaskResult=0  NextRunTime=09/10/2026 17:22:24  Missed=0
```

### 2 — a dropped file lands, with no git run by hand

Probes went into `fleet/msgs/` through `scripts/fleet_msg.py post`, not into
`orders/`. A probe file in `orders/` could be picked up by an executor as a real
order.

| Run | How it fired | Probe | Landed at | Proof it was *this* run |
|---|---|---|---|---|
| 2a | `Start-ScheduledTask` at 17:00:55 | `00f737026d108876` | **`88899a372b18e9cac87816699fae98d4279dc53c`** | first commit on the branch; parent `a246ccf` |
| 2b | **natural trigger**, fired 17:02:24 (its scheduled time) | `23953c0a5ebb74b6`, dropped 17:02:12 | **`2a1c7974c88b1fe12e08819556130091b7657380`** | absent from `88899a3`, present in `2a1c797` |

Both confirmed with `git ls-remote`, independent of the courier's own receipt.
Probe 2 landed 12 seconds after it was dropped.

**Push auth works under S4U.** That was the one real unknown going in: S4U has no
desktop, so a credential prompt cannot be answered. The courier sets
`GIT_TERMINAL_PROMPT=0` and `GCM_INTERACTIVE=never` so it could never hang on
one. It did not need to — the push succeeded.

### 3 — the three order files

At `88899a3`, each compared by blob hash against the file on disk:

```
orders/zoltarlead/c-0002.1.md                              ON BRANCH, byte-identical (88899a3)
orders/zoltarone/t-20260908-executor-dispatch-branch.md    ON BRANCH, byte-identical (88899a3)
orders/zoltargenesis/t-20260908-genesis-inventory.md       ON BRANCH, byte-identical (88899a3)
```

### 4 — a broken run still leaves a receipt

Run by hand with an unreachable URL as the remote *argument*, so no git config,
remote or credential changed (`git remote -v` identical before and after):

```
courier error  committed=None  pushed=None  failed_step=1.fetch
fleet/receipts/zoltarlead/courier-20260910T220310Z.json
  outcome      error
  failed_step  1.fetch
  stderr       fatal: unable to access 'https://unreachable.invalid/zoltar-ranks.git/': Could not resolve host: unreachable.invalid
```

A failure receipt is written to the main clone so the next run that can push
carries it. It did: the **17:12:25 natural fire landed it at
`b35437f6ad38daae881173573ad4d6e76a69e5fd`**, whose commit counted exactly **1**
commit-worthy path — the failure receipt — with the routine receipts riding along.

### The hop the order exists to close, observed live

Between the 17:12 and 17:22 fires, `zoltarlead/cowork` wrote a **new cycle,
`data/review/cycles/c-0014.json`**. Nobody ran git. The **17:22:25 natural fire
landed it at `6785ce201ab3652bc17d0c4334716a6848bc4e19`**, where it was the single
commit-worthy path and the routine receipts rode along with it.

That is the five-day split-brain's root cause, closed: brain-originated work
leaving this machine within 10 minutes, unattended.

### And no commit loop, observed live

The **17:32:25 natural fire returned `ok-nothing-to-land`** and pushed nothing —
`fleet/dispatch` stayed at `6785ce2` — even though the previous fire and
`fleet_sync.py` had both left fresh receipts on disk. Every commit so far carried
at least one genuinely commit-worthy path (15, then 3, then the failure receipt,
then `c-0014`), and a fire with only routine receipts committed nothing.

Full run log for the first 40 minutes:

```
17:00:56  ok                   88899a3   Start-ScheduledTask  (acceptance 2a, 3)
17:02:24  ok                   2a1c797   natural              (acceptance 2b)
17:03:10  error  1.fetch       -         broken on purpose    (acceptance 4)
17:12:25  ok                   b35437f   natural  -- landed the 17:03 failure receipt
17:22:25  ok                   6785ce2   natural  -- landed cowork's new c-0014
17:32:25  ok-nothing-to-land   -         natural  -- routine receipts only, no commit
```

### 5 — `main`

```
before  a246ccf6975f28ebd5acedda55d536c41dbdbbb5  refs/heads/main
after   a246ccf6975f28ebd5acedda55d536c41dbdbbb5  refs/heads/main
```

`git diff --name-only origin/main origin/fleet/dispatch` lists **0 files outside
the five dispatch paths.** The push target is built as
`HEAD:refs/heads/fleet/dispatch` and passes through `assert_not_main()` first;
four tests prove the guard refuses `main`, `HEAD:main`, `refs/heads/main` and
`master`.

## Where this departs from the order and the prior art, and why

Prior art read first: `docs/courier-protocol.md` and
`data/fleet/zoltarone-tooling/` (`push-receipt.ps1`, `heartbeat_task.py`,
`install_executor.py`, `README.md`).

1. **No `git switch` in the main clone** — the order's step 2. Two scheduled tasks
   execute their code from this working tree (`ZoltarRanksHarvest` every 30 min,
   `ZoltarFleetHeartbeat` hourly), and the worker lane commits on branches there.
   Switching HEAD every 10 minutes would change the code those tasks run whenever
   branches diverge, and could put a worker's commit on `fleet/dispatch`. The
   courier uses a **dedicated worktree** instead, which is what the prior art does
   for the same reason — `push-receipt.ps1`: *"committing receipts must never
   touch the order branch, the working tree, or HEAD."* Proven by
   `test_the_main_clone_head_and_branch_are_untouched`.
2. **A new `fleet/dispatch` is cut from `origin/main`, not "the current head".**
   The current head is whatever branch the worker lane is on; cutting from it
   leaks unmerged feature commits into the dispatch channel. Proven by
   `test_a_new_branch_is_cut_from_origin_main_not_the_feature_head`. This matches
   `push-receipt.ps1`, which also cuts from `origin/main`.
3. **Additive, never destructive.** An existing file on the branch with different
   content is reported as a conflict and left alone, except for paths this node is
   the one writer of. Append-only `*.jsonl` is **union-merged** — keep both sides,
   drop nothing, order by `at`. A plain `git add data/review/` on 2026-09-08 would
   have deleted 10 inbox events that existed on trunk but not here; doing that
   every 10 minutes would make the courier a data-loss machine. Enforces
   courier-protocol rule 1: *"do not quietly pick a winner."*
4. **Routine receipts do not trigger a commit on their own.** The order's step 4
   says commit only if the index is dirty — but `fleet_sync.py` writes a receipt
   **on every run**, so step 4's condition could never be false and the branch
   would take ~144 commits a day of receipts about receipts. Routine receipts ride
   along with the next real commit, at most an hour later because the hourly fleet
   heartbeat is always new content. **Failure receipts are the exception** and
   commit on their own. A committing run carries its own receipt inside the same
   commit, so it is never left behind to make the next run dirty.
5. **Python, not PowerShell, for the courier body.** This node runs Windows
   PowerShell 5.1, where the fleet has already been bitten
   (`verify-guard.ps1` fails silently on `ProcessStartInfo.ArgumentList`). A
   stdlib Python courier is testable under pytest against a real bare remote.
   The scheduled task is still registered by PowerShell, with an absolute
   interpreter path.
6. **`fleet_sync.py` is never run with `--push`.** Its `--push` does a bare
   `git push` of whatever branch the main clone is on — the exact thing this
   courier must never do. Asserted by a test.
7. **Differences from `heartbeat_task.py`:** that script "never runs git" by
   design, because an unattended push was then out of scope. This order makes an
   unattended push the point, so the courier does run git — but only to
   `refs/heads/fleet/dispatch`, behind a guard.

## Failed, or needs Andrew

1. **Cycle files on `fleet/dispatch` fail `review.py check` — 10 blocking.** The
   courier correctly carried `c-0008..c-0013`, written on this node by
   `zoltarlead/cowork` on 2026-09-08. Six items use `kind: note`, which is not an
   allowed kind, and `c-0010.2`/`c-0010.4` set `depends_on` to the **order id**
   `t-20260908-courier-zoltarlead` rather than a cycle item id. A transport should
   not judge content, so this is reported rather than blocked — **but merging
   `fleet/dispatch` into `main` as it stands would turn `main`'s review check
   red.** Cycles are immutable and cowork's lane; I have not touched them.
2. **The order's title and its step 3 disagree on `bridge/`.** The title says the
   courier lands "bridge/, orders/ and fleet/msgs"; the enumerated step 3 omits
   `bridge/`. I followed the enumeration rather than adding scope. If `bridge/`
   should ride too, it is one entry in `DISPATCH_PATHS`.
3. **The review protocol's own heartbeat is late since 2026-09-08T13:12.**
   `review.py check` reports it. Cowork's lane. Not cleared, not `beat`-ed.
4. **The stamp-cutover canary is still failing** and still correct — the evening
   retrain is published under both conventions (raised 2026-09-08). Left per
   rule 9.

Suite: **224 passed, 4 skipped, 2 failed.** The two failures are items 1 and 4
above; neither is in the courier's code.

## Branch

`fleet/courier-zoltarlead`, cut from `origin/main` (`a246ccf`), carrying only the
courier tooling and this report. **waiting_trunk — not merged.**
