"""Attest this node's identity: the card supplies meaning, the hostname supplies proof.

Stdlib only. No `zoltar_ranks` import, no DuckDB, so it cannot fail a harvest.

This is the Python sibling of `docs/identity-kit/verify-guard.ps1`, and it exists
because that guard **cannot run on this node**. It uses
`ProcessStartInfo.ArgumentList`, which is .NET Core only, and ZoltarLead is on
Windows PowerShell **5.1.26100.9168 (Desktop)**. The failure is silent, which is
the worst kind: a guard that cannot run and a guard that passed look identical
from the outside. Measured, not assumed — see `data/fleet/identity/zoltarlead.json`.

The rule, from `docs/identity-kit/identity.schema.md`:

    identity.json is COPYABLE — it travels with a synced folder.
    the live hostname is NOT — it is a property of the machine.
    Bind them, and the match is what makes either one evidence.

Three things this keeps that are easy to lose:

* **v1 AND v2.** Read `attested_hostnames` (array) first; fall back to v1's
  singular `attested_hostname`. A reader that only understands the newest schema
  fails closed on every unmigrated node, which is how a guard quietly stays off.
  That is F-ZG-13, and it fired on a healthy node.
* **Case-insensitive.** This machine answers `ZOLTARLEAD`, `ZoltarLead` and
  `ZoltarLead` to three different calls. A case-sensitive check fails on a
  healthy node.
* **`verified` is three-state.** `True` on a match, `False` on a mismatch,
  `None` when the hostname or card could not be read at all. Never collapse
  `False` and `None`: a copied workspace and an unreadable hostname are
  different facts, and the whole fleet-protocol effort exists because absence
  and failure kept rendering the same.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Canonical card, matching what is already on main for zoltarone. The mirror at
#: fleet/identity.json exists only because the identity kit's PowerShell guard
#: hardcodes <workspace>/fleet/identity.json; tests pin the two byte-identical.
CANONICAL_CARD = REPO_ROOT / "data" / "fleet" / "identity"
MIRROR_CARD = REPO_ROOT / "fleet" / "identity.json"
HOSTMAP = REPO_ROOT / "data" / "fleet" / "hostmap.json"


def live_hostnames() -> dict[str, str | None]:
    """Every way this machine names itself. They routinely disagree on case."""
    out: dict[str, str | None] = {}
    for label, fn in (("env_COMPUTERNAME", lambda: os.environ.get("COMPUTERNAME")),
                      ("socket_gethostname", socket.gethostname),
                      ("platform_node", platform.node)):
        try:
            v = fn()
            out[label] = v or None
        except Exception as exc:                # noqa: BLE001
            out[label] = None
            out.setdefault("_errors", "")       # type: ignore[arg-type]
            out["_errors"] = f"{out.get('_errors') or ''}{label}: {exc}; "
    return out


def attested_names(card: dict) -> list[str]:
    """v2 array first, v1 singular as the fallback. Never one without the other."""
    names = [str(n).strip() for n in (card.get("attested_hostnames") or []) if str(n).strip()]
    if not names:
        single = card.get("attested_hostname")
        if single and str(single).strip():
            names = [str(single).strip()]
    return names


def load_card(node_id: str | None = None) -> tuple[dict | None, Path | None, str | None]:
    """Return (card, path, error). The resolved path is reported, always.

    Reporting which file was actually read is the cheap defence against the
    hazard F-ZG-13 feared: two identity files consumed by different callers,
    with nothing recording which one produced a given stamp.
    """
    candidates: list[Path] = []
    if node_id:
        candidates.append(CANONICAL_CARD / f"{node_id}.json")
    else:
        candidates.extend(sorted(CANONICAL_CARD.glob("*.json")))
    candidates.append(MIRROR_CARD)
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8")), p, None
            except Exception as exc:            # noqa: BLE001
                return None, p, f"unreadable card at {p}: {exc}"
    return None, None, f"no identity card found (looked in {CANONICAL_CARD}, {MIRROR_CARD})"


def attest(node_id: str | None = None) -> dict:
    """The `by` block every fleet row carries, with `verified` three-state."""
    card, path, err = load_card(node_id)
    live = live_hostnames()
    observed = live.get("env_COMPUTERNAME") or live.get("socket_gethostname") \
        or live.get("platform_node")

    result = {
        "agent_id": None, "node_id": None, "hostname": observed,
        "lane": "claude-code", "verified": None,
        "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "identity_file": str(path) if path else None,
        "identity_schema": card.get("schema_version") if card else None,
        "hostname_reads": live, "reason": err,
    }
    if card is None:
        result["reason"] = err or "no card"
        return result                       # verified stays None: could not check

    result["node_id"] = card.get("node_id")
    result["agent_id"] = f"{card.get('node_id')}/claude-code"
    if observed is None:
        result["reason"] = "live hostname unreadable; cannot attest"
        return result                       # None, NOT False

    names = attested_names(card)
    if not names:
        result["reason"] = "card attests no hostname at all"
        return result                       # None: nothing to compare against

    matched = observed.strip().lower() in {n.lower() for n in names}
    result["verified"] = bool(matched)
    if not matched:
        pend = card.get("rename_pending") or {}
        expected = str(pend.get("to") or "").strip().lower()
        if expected and expected == observed.strip().lower():
            # A satisfied rename is a NOTICE, not an error: outcome stays ok and
            # a follow-up is owed. Routing it through the error channel made a
            # successful rename render as `degraded` on ZoltarGenesis.
            result["verified"] = True
            result["notice"] = (
                f"rename_pending satisfied: machine now reports {observed!r}. "
                f"Add it to attested_hostnames and clear rename_pending.")
        else:
            result["reason"] = (
                f"live hostname {observed!r} matches none of {names}. This card "
                f"may have been copied from another machine.")
    return result


def resolve_hostmap(hostname: str) -> tuple[str | None, str]:
    """hostname -> node_id, case-insensitively, `map` then `former_names`.

    A miss returns None with a reason. `None` means NOT KNOWN, never
    not-existing; the caller renders a gap, not an absence.
    """
    if not HOSTMAP.exists():
        return None, f"hostmap absent at {HOSTMAP}"
    hm = json.loads(HOSTMAP.read_text(encoding="utf-8"))
    want = hostname.strip().lower()
    for k, v in (hm.get("map") or {}).items():
        if k.strip().lower() == want:
            return v, "map"
    for k, v in (hm.get("former_names") or {}).items():
        if k.strip().lower() == want:
            return (v or {}).get("node_id"), "former_names"
    return None, "no entry -- hostname is NOT KNOWN to the fleet, which is a gap, not an absence"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--node-id", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--require-verified", action="store_true",
                    help="exit non-zero unless verified is exactly True")
    args = ap.parse_args(argv)

    by = attest(args.node_id)
    if args.json:
        print(json.dumps(by, indent=2))
    else:
        print(f"  agent_id        {by['agent_id']}")
        print(f"  hostname        {by['hostname']}")
        print(f"  identity_file   {by['identity_file']}")
        print(f"  identity_schema v{by['identity_schema']}")
        print(f"  verified        {by['verified']}"
              + (f"   ({by['reason']})" if by.get("reason") else ""))
        if by.get("notice"):
            print(f"  NOTICE          {by['notice']}")
        for k, v in (by.get("hostname_reads") or {}).items():
            print(f"    {k:<20} {v}")
        if by["hostname"]:
            node, how = resolve_hostmap(by["hostname"])
            print(f"  hostmap         {node!r} via {how}")
    if args.require_verified and by["verified"] is not True:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
