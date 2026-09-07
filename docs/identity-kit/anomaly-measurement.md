# Anomaly measurement — did something revert the brain's write?

**Run by:** `zoltargenesis/claude-code`, 2026-09-07 ~18:27–18:33 UTC (13:27–13:33 CDT)
**Asked by:** the `20260907-0530-package-identity-kit` work order, Task 2
**Question:** the brain's write of the v2 `identity.json` reported success at
~04:23, and 19 minutes later a `cat` read v1. Either the write did not land
despite reporting success, or something restored the v1 copy.

```jsonc
"by": { "agent_id": "zoltargenesis/claude-code", "node_id": "zoltargenesis",
        "hostname": "ZOLTARGENESIS", "lane": "claude-code",
        "verified": true, "at": "2026-09-07T13:35:00-05:00" }
```

---

## Result: no reversion observed. And this does NOT explain the anomaly.

| Target | Elapsed | Exists | Bytes | SHA-256 | Content | mtime | Verdict |
|---|---|---|---|---|---|---|---|
| `zoltargenesis:C:\Shared\ZoltarUnlimited\fleet\anomaly-witness.txt` | 330 s | yes | 91 → 91 | identical | identical | **unchanged** | **INTACT** |
| `C:\Users\apod7\OneDrive\ZoltarUnlimited\anomaly-witness.txt` | 330 s | yes | 91 → 91 | identical | identical | **unchanged** | **INTACT** |

- Windows Defender: **no detections** in the preceding 30 minutes.
- Resident security software confirmed running: **AVG** (`AVG Antivirus`,
  `AVG Tools`, `avgAvDumper64`, `avgbIDSAgent`, `AVGWscReporter`) and
  **ZoneAlarm** (`vsmon`).
- `LastAccessTimeUtc` did advance (18:32:45) — that is my own re-read hashing
  the files, not a third party.

## A false alarm in my own harness, corrected

My first pass printed **ALTERED** for both files. That was wrong, and the cause
is worth recording because it is the same failure class the KB already carries.

The verdict compared `mtime` before and after. The "before" value had been
round-tripped through `ConvertTo-Json` → `ConvertFrom-Json`, which turned the
ISO-8601 UTC string into a `DateTime` and dropped the `Z`. `[datetime]::Parse`
then read it as **local** time. The computed delta was **−17999.6 s** — exactly
−5 h, this machine's UTC offset. The files had not moved at all.

**Nothing was altered; my comparison was.** This is
`time__local-stamp-written-from-utc-clock` (KB, `times_seen: 2`) appearing a
third time, now in a measurement harness rather than a published record — a
timestamp compared across a timezone boundary without normalising first. Had I
reported the first output, I would have handed the brain a fabricated anomaly on
top of a real one.

## What this does and does not establish

**Does:** files written to the workspace and to the OneDrive mirror by a
PowerShell process on this node survive intact for at least 5.5 minutes, with
AVG and ZoneAlarm resident. There is no blanket "writes to these directories get
reverted" behaviour. Consistent with the whole of sessions 1–3, in which every
file this lane wrote persisted.

**Does not:** reproduce the brain's write path. The Cowork brain writes through
the Claude desktop bridge — a different process, different code path, different
integrity level. **This test used the wrong writer**, and the order's own framing
said the hypothesis was AV reverting *a foreign process's* write. My process is
not foreign; it is the same lane that has been writing successfully all day.

**So the anomaly is still open, and it is still the right thing to be worried
about.** "A write tool reported success and the bytes were not there" is a trust
problem for every file this fleet moves.

## What would actually settle it

Requires the brain, and I cannot drive it from here:

1. Brain writes a known unique string to `fleet\bridge\anomaly-probe-brain.txt`
   and **reports the exact byte count and timestamp it believes it wrote**.
2. This lane reads it immediately (< 10 s), then at 60 s, 300 s and 1800 s,
   recording hash and `LastWriteTimeUtc` **normalised to UTC** each time.
3. If the first read already disagrees → the write never landed and the bridge's
   success report is wrong. That is the serious case.
4. If the first read agrees and a later one does not → something on this machine
   is reverting it, and the interval bounds what.

Step 3 versus step 4 is the whole question, and one immediate read separates
them. Until that runs, **no cause should be assigned** — the brain was right not
to file a lesson for it, and I am not filing one either.

## Files

`witness-before.json`, `witness-after.json` (carries the corrected reading),
`control.txt`. The witness files themselves are left in place at
`fleet\anomaly-witness.txt` and the OneDrive mirror for anyone who wants to
re-check them at a longer interval.
