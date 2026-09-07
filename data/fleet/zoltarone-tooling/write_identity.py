#!/usr/bin/env python3
"""Write fleet/identity.json for zoltarone, schema v2.

Every value that CAN be measured is measured here rather than typed, per the KB
lesson time__local-stamp-written-from-utc-clock (promoted, times_seen 4):
stamps must be read at write time, not authored from recall.

Schema field list comes from the 2026-09-07 prompt. ZoltarGenesis's
fleet/identity.json is named there as the reference template, but it is NOT in
git on any ref -- so this file could not be cross-checked against it. That is
recorded inside the file itself, per handoff__described-intent-as-completed-state
("mark in the file that the worker authored it and why").
"""
import json
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path(r"C:\Shared\ZoltarUnlimited")


def ps(expr: str) -> str | None:
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", expr],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> int:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    # --- live reads, three independent sources plus two registry values -----
    env_name = ps("$env:COMPUTERNAME")
    exe_name = ps("hostname.exe")
    dns_name = socket.gethostname()
    active = ps(r"(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ActiveComputerName').ComputerName")
    pending = ps(r"(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\ComputerName\ComputerName').ComputerName")

    observed = [n for n in (env_name, exe_name, dns_name) if n]
    distinct_ci = sorted({n.lower() for n in observed})
    # attested = every distinct CASING actually observed, so a case-sensitive
    # consumer still matches. Comparison itself is case-insensitive.
    attested = sorted({n for n in observed}, key=str.lower)

    rename_pending = None
    if active and pending and active.lower() != pending.lower():
        rename_pending = {
            "from": active, "to": pending,
            "authorised_by": None, "authorised_at": None, "expires_at": None,
            "_note": "DETECTED, not authorised. ActiveComputerName != ComputerName "
                     "means a rename is staged and takes effect at next reboot. "
                     "Andrew must fill authorised_by/at/expires_at.",
        }

    doc = {
        "schema_version": 2,
        "node_id": "zoltarone",
        "alias": "ZoltarOne",

        "attested_hostnames": attested,
        "_former_hostnames": {
            "DESKTOP-7FJV5QQ": {
                "retired_at": "2026-09-04T23:23:00-05:00",
                "_note": "Renamed at the 23:23 reboot on 2026-09-04. Retained "
                         "because the node card, every heartbeat stamp before "
                         "that reboot, six bridge/outbox reports and "
                         "data/fleet/hostmap.json on fleet/join-zoltargenesis "
                         "all carry this name. Those records are not rewritten, "
                         "so the map must still resolve them.",
            },
            "l460": {
                "retired_at": "2026-09-04T13:45:00-05:00",
                "_note": "Never a hostname -- an early node_id, killed by "
                         "20260904-1345-naming-and-bus. Listed so a lookup of "
                         "the string resolves instead of dead-ending.",
            },
        },
        "rename_pending": rename_pending,

        "workspace_root": str(WORKSPACE),
        "repo_root": str(WORKSPACE / "zoltar-ranks"),
        "onedrive_mirror": r"C:\Users\zolta\OneDrive\ZoltarUnlimited",

        "lanes": {
            "owns": ["data/fleet/", "the ZoltarUnlimited workspace"],
            "denied": ["src/", "scripts/", "tests/", "docs/", "config/",
                       "data/build/manifest.yaml", "dashboard/"],
            "denied_exceptions": ["docs/courier-protocol.md"],
            "trunk": "never -- main is Andrew's, applied outside every agent session",
            "_assignment": "A with B underneath, decided 2026-09-04T18:50. This "
                           "node works items assigned claude-code@zoltarone.",
        },
        "agents": ["zoltarone/claude-code", "zoltarone/cowork"],

        "transports": {
            "git": {
                "remote": "https://github.com/zoltarfinancial/zoltar-ranks",
                "role": "source of truth; the one that must not fail",
                "note": "push works via Git Credential Manager; gh is installed "
                        "but unauthenticated",
            },
            "onedrive": {
                "path": r"C:\Users\zolta\OneDrive\ZoltarUnlimited\fleet",
                "account": "zoltarfinancial@ (fleet account)",
                "role": "the only surface all four agents can read and write",
                "measured_latency_one_way": "3-18 minutes, never seconds",
                "LIMITATION": "OneDrive has NO service on this machine. It starts "
                              "only from HKCU\\...\\Run and a scheduled task with a "
                              "LOGON trigger. With AutoAdminLogon=0, an unattended "
                              "reboot means it never starts and this mirror stops "
                              "syncing until a human logs in.",
            },
            "bridge": {
                "path": str(WORKSPACE / "bridge"),
                "role": "node-local channel to zoltarone/cowork, which has no shell",
            },
        },

        "verification_rule": {
            "compare": "live hostname against attested_hostnames",
            "case_sensitivity": "CASE-INSENSITIVE. This machine reports itself "
                                "two ways in one session: $env:COMPUTERNAME "
                                "uppercase, hostname.exe and DNS mixed-case.",
            "states": {
                "true": "a live read matched an attested hostname",
                "false": "a live read matched nothing attested",
                "null": "the hostname could not be read at all",
            },
            "never": "null must NEVER be collapsed into false. Unreadable and "
                     "mismatched are different facts.",
            "v1_fallback": "A consumer MUST also accept singular `attested_hostname`. "
                           "ZoltarLead is unmigrated, and a guard that only knows v2 "
                           "fails closed on it.",
        },

        "_evidence": {
            "measured_at": now,
            "measured_by": "zoltarone/claude-code, live on the machine",
            "reads": {
                "$env:COMPUTERNAME": env_name,
                "hostname.exe": exe_name,
                "[System.Net.Dns]::GetHostName()": dns_name,
                "registry ActiveComputerName": active,
                "registry ComputerName": pending,
            },
            "distinct_case_insensitive": distinct_ci,
            "agreement": ("ALL AGREE case-insensitively" if len(distinct_ci) == 1
                          else f"DISAGREE: {distinct_ci}"),
            "rename_state": ("no rename staged -- ActiveComputerName == ComputerName"
                             if not rename_pending else "RENAME STAGED"),
        },

        "_provenance": {
            "authored_by": "zoltarone/claude-code",
            "why": "The 2026-09-07 prompt names ZoltarGenesis's fleet/identity.json "
                   "as the reference template for schema v2. That file is NOT in git "
                   "on any ref -- verified by searching every commit reachable from "
                   "--all for 'fleet/identity.json', 'identity-kit', and "
                   "'node-identity-protocol'; zero hits. So this file was written "
                   "from the prompt's enumerated field list and could NOT be "
                   "cross-checked against the reference.",
            "risk": "Field NAMES here match the prompt. Field SEMANTICS may diverge "
                    "from ZoltarGenesis's file. Reconcile before any consumer treats "
                    "the two as interchangeable.",
            "precedent": "KB handoff__described-intent-as-completed-state (rev 15, "
                         "raised as F-ZG-13): read the artifact a brief describes "
                         "before acting on the description; if only the brief has "
                         "the fields, the migration was described, not performed.",
        },
    }

    for target in (WORKSPACE / "fleet" / "identity.json",
                   WORKSPACE / "zoltar-ranks" / "data" / "fleet" / "identity" / "zoltarone.json"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        print(f"wrote {target}")

    print(f"\nattested_hostnames : {attested}")
    print(f"agreement          : {doc['_evidence']['agreement']}")
    print(f"rename_pending     : {rename_pending}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
