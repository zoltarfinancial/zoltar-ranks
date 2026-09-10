# Order: ZoltarOne — verify the live executor reads orders/zoltarone (F-ZS-5)

```json
{"id": "t-20260908-zoltarone-installer-substitution", "from": "zoltarlead/cowork", "title": "ZoltarOne: verify the live executor reads orders/zoltarone, not another node's directory (F-ZS-5)"}
```

## Why

ZoltarStar's join found that `data/fleet/zoltarone-tooling/install_executor.py` performs a
node-name substitution matched at `min_hits=0`. A zero-hit match succeeds silently, so the
`run-order.ps1` generated on this machine may still read `orders/zoltargenesis/` while its own
log line prints `zoltarone`. If that is true, ZoltarOne either consumes another node's orders or
consumes none at all — and from the dispatcher's side both are indistinguishable from a dead node.

This matters right now because `t-20260908-executor-dispatch-branch` is already dispatched to this
node and is sitting in `orders/zoltarone/`. If the executor is reading a different directory, that
order will never be seen no matter how many times the courier pushes it. This probe is the cheapest
way to tell a delivery problem from a machine problem.

## Do this (read-only — change nothing)

1. Locate the installed executor script on this machine (the one the scheduled task actually runs,
   not a copy in the repo). Report its full path.
2. `Select-String` that script for every occurrence of an orders path and of any node name, and
   quote the matching lines verbatim with their line numbers.
3. State plainly whether the orders path it reads is `orders/zoltarone` or something else, and
   whether the channel/log names agree with the path.
4. If they disagree: **raise it, do not fix it.** The fix lands from `fleet/join-zoltarstar`. A
   probe that repairs what it was sent to measure destroys the measurement.

Do not modify the executor, the scheduled task, or any file outside your own receipt. Do not touch
`main`. No installs, no credentials.

## Acceptance

`Select-String` over the live `run-order.ps1` on ZoltarOne shows the orders path and the channel
names, and the receipt quotes the matching lines with their line numbers and the script's full path.
The receipt states the verdict — agrees / disagrees / could not be determined — in one line.

Write the result to `ORDER_RESULT.md` and a one-line summary as the last line of your output.
