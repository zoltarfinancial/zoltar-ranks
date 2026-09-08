"""The fleet bus: content-addressing, causal order, gap repair, reachability.

`docs/fleet-protocol.md`. These are the assertions behind the one acceptance
that matters — *a message written here is ingested from git AND OneDrive,
deduped to one msg_id, and a deliberately withheld seq is detected as a gap and
repaired from the other transport, with every clock wrong on purpose.*

The cross-node half of that needs ZoltarOne and cannot be run from this machine.
What is proven here is the mechanism, on this node, with both transports real:
if the ordering survives scrambled clocks locally it survives them between
machines, because nothing in the ordering path reads a clock at all.
"""
from __future__ import annotations

import json
import random

import pytest

from scripts import fleet_msg as fm
from scripts import fleet_sync as fs


@pytest.fixture()
def bus(tmp_path, monkeypatch):
    """Two real directories standing in for git and OneDrive."""
    git = tmp_path / "git" / "msgs"
    one = tmp_path / "onedrive" / "msgs"
    monkeypatch.setattr(fm, "MSG_DIR", git)
    monkeypatch.setattr(fm, "RECEIPT_DIR", tmp_path / "receipts")
    monkeypatch.setattr(fm, "LEDGER_DIR", tmp_path / "ingest")
    monkeypatch.setattr(fm, "onedrive_root", lambda: one)

    real_state = fm.transport_state

    def state(name, node_id):
        # Only redirect THIS node's git; other nodes must still resolve through
        # DECLARED_TRANSPORTS, or the zoltargenesis assertion tests the fixture
        # instead of the code.
        if name == "git" and node_id == "zoltarlead":
            return "ok", None, git
        return real_state(name, node_id)
    monkeypatch.setattr(fm, "transport_state", state)

    by = {"agent_id": "zoltarlead/claude-code", "verified": True,
          "node_id": "zoltarlead", "hostname": "ZOLTARLEAD"}

    def make(subject, agent="zoltarlead/claude-code", known=None):
        return fm.build(agent, "note", subject, f"body of {subject}",
                        node_id="zoltarlead", by=by, known=known or [])
    return {"git": git, "onedrive": one, "make": make, "by": by}


# --------------------------- R2: one envelope, identical bytes ---------------------------

def test_msg_id_is_content_addressed(bus):
    msg, raw = bus["make"]("hello")
    assert msg["msg_id"] == fm.compute_msg_id(msg)
    assert len(msg["msg_id"]) == 16


def test_same_content_gives_the_same_id(bus):
    a, _ = bus["make"]("same")
    b = dict(a)
    b.pop("msg_id")
    assert fm.compute_msg_id(b) == a["msg_id"], (
        "recomputing the id from the same body must reproduce it, or the same "
        "message on two transports becomes two messages")


def test_tampering_with_the_body_is_detected(bus):
    msg, _ = bus["make"]("original")
    msg["body"] = "quietly changed"
    problems = fm.verify(msg)
    assert any("msg_id mismatch" in p for p in problems)


def test_the_bytes_written_to_both_transports_are_identical(bus):
    msg, raw = bus["make"]("fanout")
    for t in ("git", "onedrive"):
        ok, err = fm.write_transport(t, "zoltarlead", msg, raw)
        assert ok, err
    a = (bus["git"] / fm.filename(msg)).read_bytes()
    b = (bus["onedrive"] / fm.filename(msg)).read_bytes()
    assert a == b == raw, "R2 requires identical bytes, not equivalent JSON"


# ------------------------------ R6: dedup / idempotent ------------------------------

def test_the_same_message_on_two_transports_is_one_message(bus):
    msg, raw = bus["make"]("once")
    for t in ("git", "onedrive"):
        fm.write_transport(t, "zoltarlead", msg, raw)
    known = fm.all_known("zoltarlead")
    assert len(known) == 1, "the same msg_id on two transports must dedup to one"


def test_ingesting_twice_is_a_no_op(bus, monkeypatch):
    msg, raw = bus["make"]("idem")
    fm.write_transport("git", "zoltarlead", msg, raw)
    monkeypatch.setattr(fs, "_git", lambda *a, **k: (0, "abc1234", ""))
    first = fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)
    second = fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)
    assert first["ingested"] == [msg["msg_id"]]
    assert second["ingested"] == [], "a second sync must ingest nothing new"


# ------------------- R3: order from causality, with the clocks WRONG -------------------

def test_ordering_survives_deliberately_scrambled_clocks(bus):
    """Every `authored_at` is wrong on purpose. The sequence must not move.

    This machine's own System log carries three "the system time has changed"
    events at 2026-09-04T00:44:29, so a wrong clock here is measured, not
    hypothetical.
    """
    known, msgs = [], []
    for i in range(6):
        m, _ = bus["make"](f"m{i}", known=known)
        msgs.append(m)
        known = known + [m]

    rng = random.Random(11)
    scrambled = []
    for m in msgs:
        c = dict(m)
        # Future-dated, past-dated, and out of order -- all four clock failures
        # this fleet has actually hit, applied at once.
        c["authored_at"] = f"20{rng.randint(10, 40):02d}-01-01T00:00:00-05:00"
        scrambled.append(c)
    rng.shuffle(scrambled)

    by_lamport = [m["subject"] for m in sorted(scrambled, key=lambda x: x["lamport"])]
    by_seq = [m["subject"] for m in fm.agent_chain(scrambled, "zoltarlead/claude-code")]
    assert by_lamport == [f"m{i}" for i in range(6)]
    assert by_seq == [f"m{i}" for i in range(6)]

    by_clock = [m["subject"] for m in sorted(scrambled, key=lambda x: x["authored_at"])]
    assert by_clock != by_lamport, (
        "the fixture failed to scramble the clock, so this proves nothing")


def test_authored_at_is_metadata_and_not_in_the_ordering_path(bus):
    """Changing only the clock changes the id -- but never the order."""
    m, _ = bus["make"]("clocked")
    n = dict(m)
    n["authored_at"] = "1999-01-01T00:00:00-05:00"
    assert fm.compute_msg_id(n) != m["msg_id"]
    assert n["seq"] == m["seq"] and n["lamport"] == m["lamport"]


def test_lamport_is_max_seen_plus_one(bus):
    assert fm.next_lamport([]) == 1
    assert fm.next_lamport([{"lamport": 7}, {"lamport": 3}]) == 8


def test_seq_chain_links_to_its_predecessor(bus):
    a, _ = bus["make"]("first", known=[])
    b, _ = bus["make"]("second", known=[a])
    assert a["seq"] == 1 and a["prev"] is None
    assert b["seq"] == 2 and b["prev"] == a["msg_id"]


# ------------------------- R5: gaps detected, then repaired -------------------------

def test_a_withheld_seq_is_detected_as_a_gap(bus):
    known, msgs = [], []
    for i in range(4):
        m, raw = bus["make"](f"g{i}", known=known)
        msgs.append((m, raw))
        known = known + [m]
    # Deliberately withhold seq 3 from BOTH transports.
    for m, raw in msgs:
        if m["seq"] == 3:
            continue
        fm.write_transport("git", "zoltarlead", m, raw)

    gaps = fm.find_gaps(fm.all_known("zoltarlead"))
    assert len(gaps) == 1
    assert gaps[0]["missing_seq"] == [3]
    assert gaps[0]["held_through"] == 4, (
        "the newest message says seq 4, so 3 is missing BY ID -- no clock needed")


def test_a_gap_on_one_transport_is_repaired_from_the_other(bus, monkeypatch):
    """The acceptance in miniature: withhold from git, hold on OneDrive, repair."""
    known, msgs = [], []
    for i in range(3):
        m, raw = bus["make"](f"r{i}", known=known)
        msgs.append((m, raw))
        known = known + [m]

    for m, raw in msgs:
        fm.write_transport("onedrive", "zoltarlead", m, raw)
        if m["seq"] != 2:                       # git is missing seq 2
            fm.write_transport("git", "zoltarlead", m, raw)

    missing = [m for m, _ in msgs if m["seq"] == 2][0]
    assert not (bus["git"] / fm.filename(missing)).exists()

    monkeypatch.setattr(fs, "_git", lambda *a, **k: (0, "abc1234", ""))
    r = fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)

    assert (bus["git"] / fm.filename(missing)).exists(), "repair did not happen"
    assert any(x["msg_id"] == missing["msg_id"] and x["to"] == "git"
               for x in r["repaired"])
    # bytes, not a re-serialisation: the id must survive the repair
    restored = json.loads((bus["git"] / fm.filename(missing)).read_text(encoding="utf-8"))
    assert restored["msg_id"] == missing["msg_id"]
    assert fm.compute_msg_id(restored) == missing["msg_id"]


def test_repair_only_targets_transports_the_writer_claimed(bus):
    m, raw = bus["make"]("claimed")
    assert set(m["fanout"]) <= {"git", "onedrive"}
    assert "db" not in m["fanout"], (
        "db is absent-by-design for a worker; claiming it would make every sync "
        "report a permanent unrepairable gap")


# --------------------- R4: four states, and 0 is not null ---------------------

def test_empty_and_unreachable_are_different_states(bus, monkeypatch):
    empty = fm.read_transport("git", "zoltarlead")
    assert empty["state"] == "empty" and empty["msgs_read"] == 0

    monkeypatch.setattr(fm, "transport_state",
                        lambda n, nid: ("unreachable", "drive offline", None))
    gone = fm.read_transport("git", "zoltarlead")
    assert gone["state"] == "unreachable"
    assert gone["msgs_read"] is None, (
        "msgs_read must be null when the transport could not be read. 0 means "
        "'I read it and it held nothing' -- collapsing them is the defect.")


def test_db_is_absent_by_design_not_unreachable(bus):
    state, reason, _ = fm.transport_state("db", "zoltarlead")
    assert state == "absent-by-design"
    assert "Artifact" in reason, "an absent-by-design transport must say WHY"


def test_zoltargenesis_is_declared_db_only(bus):
    """A node property to declare, not a fault to re-raise every session."""
    d = fm.DECLARED_TRANSPORTS["zoltargenesis"]
    assert d["git"] == "absent-by-design" and d["onedrive"] == "absent-by-design"
    assert d["db"] == "ok"
    for t in ("git", "onedrive"):
        state, reason, _ = fm.transport_state(t, "zoltargenesis")
        assert state == "absent-by-design" and reason


def test_every_declared_state_is_one_of_the_four(bus):
    m, _ = bus["make"]("states")
    for t, s in m["reachable"].items():
        assert s in fm.STATES, f"{t} declared {s!r}, not one of {fm.STATES}"


# ------------------------------- the receipt -------------------------------

def test_the_receipt_names_every_transport_including_the_unreachable_one(bus, monkeypatch):
    monkeypatch.setattr(fs, "_git", lambda *a, **k: (0, "abc1234", ""))
    r = fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)
    assert set(r["transports"]) == set(fs.TRANSPORTS), (
        "a transport omitted from the receipt is indistinguishable from one "
        "that was read and held nothing")
    assert r["transports"]["db"]["state"] == "absent-by-design"
    assert r["transports"]["db"]["msgs_read"] is None
    assert r["transports"]["db"].get("reason")


def test_a_receipt_is_written_to_disk(bus, monkeypatch):
    monkeypatch.setattr(fs, "_git", lambda *a, **k: (0, "abc1234", ""))
    r = fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)
    from pathlib import Path
    assert Path(r["_path"]).exists()
    saved = json.loads(Path(r["_path"]).read_text(encoding="utf-8"))
    assert saved["agent_id"] == "zoltarlead/claude-code"


def test_observed_at_is_assigned_by_the_receiver_not_carried_in_the_message(bus, monkeypatch):
    """R3. The message is immutable and content-addressed, so a receiver-local
    timestamp inside it would change its id per receiver and break R2."""
    m, raw = bus["make"]("obs")
    assert "observed_at" not in m
    fm.write_transport("git", "zoltarlead", m, raw)
    monkeypatch.setattr(fs, "_git", lambda *a, **k: (0, "abc1234", ""))
    fs.sync("zoltarlead", "zoltarlead/claude-code", do_pull=False)
    ledger = fs.load_ledger("zoltarlead")
    assert ledger[m["msg_id"]]["observed_at"]
    assert ledger[m["msg_id"]]["source"] == ["git"]


# ------------------------------- posting guard -------------------------------

def test_verify_accepts_a_well_formed_message(bus):
    m, _ = bus["make"]("good")
    assert fm.verify(m) == []


def test_verify_rejects_an_unknown_reachability_state(bus):
    m, _ = bus["make"]("bad-state")
    m["reachable"]["git"] = "probably fine"
    m["msg_id"] = fm.compute_msg_id(m)
    assert any("not one of" in p for p in fm.verify(m))
