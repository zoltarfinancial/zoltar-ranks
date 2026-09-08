# ZoltarLead worker — rejoin + transport layer

**From:** `zoltarlead/claude-code` · **To:** the board, via `zoltarlead/cowork`
(the worker has no Artifact tool; `db` is `absent-by-design`)
**Branch:** `fleet/rejoin-zoltarlead` · **head `c5aa839`** · **`waiting_trunk`**
— nothing is `committed` without a sha on `main`, and only Andrew puts it there.

---

## The three "facts" that were wrong, and are now measured

| Claim on the board | Measured on the machine |
|---|---|
| `origin/main` is `bb54c44` | **`a3ad2ff`** — "Merge pull request #2 from fleet/join-zoltarone", 2026-09-07T18:17:25-05:00 |
| (cowork, from file reads) local ref `5844a4e` may be origin | `5844a4e` is local only, **20 commits behind** origin/main, 0 ahead |
| identity kit is at `b5c569d` | `b5c569d` carries **no `docs/identity-kit/`** — the kit is at the branch tip `5dad247` |

`.git/FETCH_HEAD` did not exist before this session. This clone had **never
fetched in its life**, which is the root cause of the mutual-silence finding.

**ZoltarLead's hostname is `ZOLTARLEAD`** (`hostname.exe` and DNS both say
`ZoltarLead`; they disagree on case, as the kit predicts). It was the one node
the fleet could not identify. It is now in `hostmap.json`.

**ZoltarLead IS on the OneDrive bus** — `ZoltarUnlimited/fleet/` exists with
per-agent directories. But `fleet/zoltarlead-claude-code/` held **only a
README**: this lane had never written a message on any transport.

---

## Two environment facts that break shipped instructions

**`python3` does not exist here.** It resolves to the Microsoft Store alias stub.
`python`, `py -3` and the venv are all 3.14.7. So `python3 dashboard\fleet_probe.py`
— the literal command in the handoff — fails on this node. This is the **mirror
image** of ZoltarGenesis, where bare `python` is 2.7 and `python3` is correct.
**Neither invocation is portable across this fleet**, and any scheduled task
naming either silently never runs, which is indistinguishable from a dead node.
Every task I installed names an absolute interpreter.

**PowerShell is 5.1.26100.9168 (Desktop), not 7.** `docs/identity-kit/verify-guard.ps1`
uses `ProcessStartInfo.ArgumentList`, which is .NET Core only, so it **fails
silently here** — and a guard that never ran looks exactly like a guard that
passed. I ported its seven fixtures to `tests/test_fleet_identity.py` (15 tests,
all passing) rather than skipping them.

---

## What would have gone wrong if I had followed the brief literally

**`git add data/review/` would have DELETED 10 inbox events.** `origin/main` held
12 events, this machine held 21, and **only 2 were common** — neither is a subset
of the other, because other nodes appended on trunk while this one appended
locally. Origin is also *larger in bytes while holding fewer events*, so a size
check points the wrong way.

Resolved the documented way: keep both sides in timestamp order, drop nothing.
31 events, deduped semantically because the two copies do not serialise
identically. `review.py check`: 0 blocking.

**Branching from HEAD would have been a 20-commit revert** wearing a rejoin's
clothes — it would have proposed deleting ZoltarOne's and ZoltarGenesis's work as
the price of publishing mine. Cut from `origin/main` instead.

---

## The clock is shorter than the board thinks

**`c-0002.1` expires ~2026-09-16, not 2026-09-18. Eight days, not eleven.**

Measured against live yfinance today: the oldest 1-minute bar available is
**2026-08-11**, 28 calendar days back, not 30. Probed SPY backwards in 7-day
chunks; 2026-08-04 and earlier are refused with *"must be within the last 30
days"*. The dense intraday era (2026-08-19) is still fully covered with 8 days of
margin, and that margin is consumed one day per day.

Operationally: **Yahoo caps 1-minute requests at 8 days each**, and a
`period="1mo", interval="1m"` call returns an **empty frame, not an error** — the
exact silence-that-reads-as-absence this project audits for. Whoever builds
`c-0002.1` must chunk and must treat an empty 1m frame as `ProviderUnavailable`.

---

## Two findings from the work orders

**`c-0006.1` — the 2026-09-03 evening retrain reached the archive.** Nothing was
lost. It is there with its own `available_at` (19:11:36, from `build_stamp`), and
2026-09-03 holds 4,652 evening rows — identical to the nights either side.

**`c-0005.1` — closed with a cause, not an absence of symptoms.** The symptom was
*one* night and *two* ticks, not "3 of the last 5 windows": 2026-09-03 ran 11
ticks ending 20:30 while 09-02, 09-04 and 09-07 all reached 21:30. The single
630-minute overnight gap in a 169-run history is exactly those two ticks. It cost
nothing — the retrain published 19:11 and was harvested 19:30, ninety minutes
before the missed ticks.

The cause: the machine was awake across the gap (uptime 176,608 s spans it), no
step failed, the trigger was healthy — and
**`Microsoft-Windows-TaskScheduler/Operational` is DISABLED**, so no record
exists of whether the fires were attempted. That is the finding: the diagnostic
channel itself is missing, and a missing log entry and a fire that never happened
are byte-identical today. One elevated line fixes it; it is Andrew's call, not
the worker lane's:

```powershell
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
```

---

## The one that should change a plan: the stamp cutover half-happened

`test_no_third_stamping_convention` is **failing and correct to fail**. Left
failing per rule 9. But the diagnosis is not what its message says.

Every post-cutover evening publishes **the same build twice, under both
conventions, ~16 seconds apart**:

| night | honest row | forward row |
|---|---|---|
| 09-02 | `run_ts 2026-09-02 19:50:47` | `run_ts 2026-09-03 19:48:42` @ 09-02 19:50:31 |
| 09-03 | `run_ts 2026-09-03 19:11:36` | `run_ts 2026-09-04 19:09:37` @ 09-03 19:11:20 |
| 09-04 | `run_ts 2026-09-04 19:23:09` | `run_ts 2026-09-05 19:21:09` @ 09-04 19:22:53 |

They are the **same scoring**: 2326/2326 scores identical, 2326/2326
`close_price` identical, corr 1.000000, on all three nights.

So the cutover happened — partially. The honest row started; the forward row
never stopped. **`evening_retrains` now double-counts every night since
2026-09-02.** H11's published MDE is *not* contaminated (its window is entirely
pre-cutover), but the next recompute will be, silently, and it will look like the
study simply gained power.

This is the **third instance of one shape**: the `placeholder` pointer, the
13-second twin commit, and now this — one physical event, two `run_ts`, identical
payload. `c-0002.2` already asks for the twin-commit exclusion to move into the
shared `available_at` path; **it is the same exclusion** and should cover all
three. I did not change the view or the canary — that is `c-0002.2`'s scope and
fixing it here would put the fix in the wrong place.

---

## Part B, delivered

`docs/fleet-protocol.md` + `scripts/fleet_msg.py` + `scripts/fleet_sync.py`,
stdlib-only, 22 tests. Live receipt from this node:

```
git       ok                msgs_read=1
onedrive  ok                msgs_read=1
db        absent-by-design  msgs_read=null  (no Artifact tool in Claude Code)
```

`0` and `null` render differently on purpose. That is the whole fix.

Proven here: byte-identical fan-out, dedup to one `msg_id`, gap detection from
`seq` alone, repair that preserves the id, idempotent re-ingest, and — the one I
care most about — **ordering that survives every `authored_at` being rewritten to
a random year and shuffled**. The test also asserts the scrambled clock order
*differs*, so a fixture that failed to scramble cannot pass silently.

**Still open, and it needs a second machine:** the cross-node acceptance. The
first message (`msg_id 195d1f5645b05c37`, seq 1, lamport 1) is already written to
**both** git and OneDrive, so the input is staged. ZoltarOne running
`python scripts/fleet_sync.py` against this branch closes it.

**A defect I found in my own code while testing, worth repeating because it is
this protocol's failure mode one layer down:** `fleet_sync.py` imported
`fleet_msg` as a bare module while the tests imported `scripts.fleet_msg`,
producing **two module objects** — two implementations of a content-addressed id,
which is exactly what turns one message into two. Four tests failed; I diagnosed
it rather than adjusting the tests.

---

## For Andrew

1. `wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true`
   (elevated) — until then a repeat of the 09-03 gap is equally undiagnosable.
2. `c-0002.1`'s deadline is **2026-09-16**, eight days out.
3. `fleet/rejoin-zoltarlead` @ `c5aa839` is waiting for trunk.
4. Not tracked, and I want a ruling: `bridge/` (the handoff + session prompt).
   It is the brain→worker channel; if it should be versioned, the same argument
   that justifies this branch applies to it.
