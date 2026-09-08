"""Prove the identity guard before a single stamp is trusted.

The kit ships `verify-guard.ps1` with seven fixtures and says "do not skip
this". On ZoltarLead it **cannot be run**: it uses
`ProcessStartInfo.ArgumentList`, which is .NET Core only, and this node is on
Windows PowerShell 5.1 (Desktop). It fails silently, so a guard that never ran
and a guard that passed look identical. These are the same fixtures in the lane
that can execute here.

The invariant under all of them: `verified` is **three-state**. `True` matched,
`False` mismatched, `None` could not check. Collapsing `False` and `None` is the
exact absence-versus-failure substitution the whole fleet-protocol effort exists
to remove — a copied workspace and an unreadable hostname are different facts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import fleet_identity as fi

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def card(tmp_path, monkeypatch):
    """Point the guard at a throwaway card; never touch the real one."""
    d = tmp_path / "identity"
    d.mkdir()
    monkeypatch.setattr(fi, "CANONICAL_CARD", d)
    monkeypatch.setattr(fi, "MIRROR_CARD", tmp_path / "nonexistent.json")

    def write(payload, node_id="testnode"):
        p = d / f"{node_id}.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p
    return write


def _host(monkeypatch, name):
    monkeypatch.setattr(fi, "live_hostnames",
                        lambda: {"env_COMPUTERNAME": name,
                                 "socket_gethostname": name, "platform_node": name})


# ----------------------------- the seven fixtures -----------------------------

def test_v2_array_match(card, monkeypatch):
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["TESTNODE", "TestNode"]})
    _host(monkeypatch, "TESTNODE")
    assert fi.attest("testnode")["verified"] is True


def test_v1_singular_fallback(card, monkeypatch):
    """A reader that only understands v2 fails closed on every unmigrated node."""
    card({"schema_version": 1, "node_id": "testnode",
          "attested_hostname": "TESTNODE"})
    _host(monkeypatch, "TESTNODE")
    by = fi.attest("testnode")
    assert by["verified"] is True and by["identity_schema"] == 1


def test_case_insensitive_match(card, monkeypatch):
    """This machine answers ZOLTARLEAD and ZoltarLead to different calls.
    A case-sensitive check fails on a HEALTHY node."""
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["TESTNODE"]})
    _host(monkeypatch, "testnode")
    assert fi.attest("testnode")["verified"] is True


def test_mismatch_is_false_not_none(card, monkeypatch):
    """A copied workspace must say so rather than impersonate the node."""
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["TESTNODE"]})
    _host(monkeypatch, "SOMEONE-ELSE")
    by = fi.attest("testnode")
    assert by["verified"] is False
    assert "matches none" in by["reason"]


def test_unreadable_hostname_is_none_not_false(card, monkeypatch):
    """Could-not-check is NOT a mismatch. This is the whole three-state rule."""
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["TESTNODE"]})
    monkeypatch.setattr(fi, "live_hostnames", lambda: {"env_COMPUTERNAME": None,
                                                       "socket_gethostname": None,
                                                       "platform_node": None})
    by = fi.attest("testnode")
    assert by["verified"] is None, "an unreadable hostname must never read as a mismatch"


def test_missing_card_is_none_not_false(card, monkeypatch):
    _host(monkeypatch, "TESTNODE")
    by = fi.attest("nosuchnode")
    assert by["verified"] is None and by["reason"]


def test_card_attesting_nothing_is_none(card, monkeypatch):
    card({"schema_version": 2, "node_id": "testnode", "attested_hostnames": []})
    _host(monkeypatch, "TESTNODE")
    by = fi.attest("testnode")
    assert by["verified"] is None, "no attested name means nothing to compare, not a mismatch"


def test_rename_pending_satisfied_is_a_notice_not_an_error(card, monkeypatch):
    """A satisfied rename needs a follow-up but is not a failure.

    Routing it through the error channel made a successful rename render as
    `degraded` on ZoltarGenesis -- the same conflation this design removes.
    """
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["OLDNAME"],
          "rename_pending": {"to": "NEWNAME"}})
    _host(monkeypatch, "NEWNAME")
    by = fi.attest("testnode")
    assert by["verified"] is True
    assert "rename_pending satisfied" in by["notice"]
    assert not by.get("reason")


def test_rename_pending_to_a_different_name_still_fails(card, monkeypatch):
    card({"schema_version": 2, "node_id": "testnode",
          "attested_hostnames": ["OLDNAME"],
          "rename_pending": {"to": "NEWNAME"}})
    _host(monkeypatch, "UNRELATED")
    assert fi.attest("testnode")["verified"] is False


def test_the_resolved_card_path_is_always_reported(card, monkeypatch):
    """Which file produced a stamp must never be a guess (F-ZG-13)."""
    p = card({"schema_version": 2, "node_id": "testnode",
              "attested_hostnames": ["TESTNODE"]})
    _host(monkeypatch, "TESTNODE")
    assert fi.attest("testnode")["identity_file"] == str(p)


# ------------------------- this node's real card -------------------------

def test_this_nodes_card_is_v2_and_attests_the_live_hostname():
    by = fi.attest("zoltarlead")
    assert by["identity_schema"] == 2
    assert by["node_id"] == "zoltarlead"
    assert by["verified"] is True, (
        f"ZoltarLead failed to attest itself: {by.get('reason')}")


def test_the_two_card_copies_are_byte_identical():
    """The kit's guard reads <workspace>/fleet/identity.json; this repo's
    committed convention is data/fleet/identity/<node>.json. Both exist, so pin
    them equal -- two identity files read by different callers is exactly the
    hazard F-ZG-13 feared, and drift between them would be undetectable.
    """
    a = (REPO / "data" / "fleet" / "identity" / "zoltarlead.json").read_bytes()
    b = (REPO / "fleet" / "identity.json").read_bytes()
    assert a == b, ("the canonical card and its guard-facing mirror have "
                    "diverged; regenerate the mirror from the canonical file")


def test_hostmap_resolves_this_node_case_insensitively():
    for spelling in ("ZOLTARLEAD", "ZoltarLead", "zoltarlead"):
        node, how = fi.resolve_hostmap(spelling)
        assert node == "zoltarlead", f"{spelling!r} resolved to {node!r} via {how}"


def test_hostmap_no_longer_lists_this_node_as_unknown():
    hm = json.loads((REPO / "data" / "fleet" / "hostmap.json").read_text(encoding="utf-8"))
    assert "zoltarlead" not in hm.get("unknown", {}), (
        "zoltarlead is still recorded as an unknown hostname after attestation")
    assert hm["map"].get("ZOLTARLEAD") == "zoltarlead"


def test_hostmap_miss_is_a_gap_not_an_absence():
    node, how = fi.resolve_hostmap("NEVER-SEEN-BEFORE")
    assert node is None and "NOT KNOWN" in how
