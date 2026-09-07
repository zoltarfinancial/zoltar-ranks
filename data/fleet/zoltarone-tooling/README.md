# zoltarone node tooling — durable copies

These run from `C:\Shared\ZoltarUnlimited\` on the node itself. Copies live here
because **git is this node's primary transport**: its OneDrive mirror has no
service and stops syncing after any unattended reboot, so anything that exists
only on that disk is one power blip from being unreachable.

| File | Runs as | What it is |
|---|---|---|
| `heartbeat_task.py` | `\Zoltar\ZoltarOne-FleetHeartbeat`, hourly + at-boot | the pulse. Attests identity every fire, three-state `verified`, stamps on failure, reports `halted` |
| `install_executor.py` | one-shot | copies `docs/executor-kit/` to `fleet\executor\` with node-identity substitutions. Every substitution is listed in the file so the diff against the kit is auditable |
| `push-receipt.ps1` | called by `run-order.ps1` | the one ADDITION to the kit: pushes receipts to `fleet/receipts-zoltarone` via a worktree, so reporting does not depend on OneDrive |
| `write_identity.py` | one-shot | wrote `fleet\identity.json` v2 from live reads |

The executor itself is **not** copied here — it is `docs/executor-kit/` plus the
substitutions `install_executor.py` performs. Re-run that script to rebuild it
rather than editing a fork.

`fleet\executor\run-order.ps1` and `allowlist.ps1` are NOT committed on purpose:
they are generated, and a second copy in git would drift from the kit.
