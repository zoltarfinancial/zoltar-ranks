"""The repo-wide invariant: an unread file means a nonzero exit.

A harvester must survive one bad blob without abandoning the other 227 -- but it
must NOT then report success. A silently short archive is worse than a failed
run: a missing run timestamp looks exactly like a day the model produced no
rank, so the failure becomes indistinguishable from data, permanently.

These tests make every harvester prove it by breaking `read_pickle` and
asserting the exit code.
"""
from __future__ import annotations

import logging

import pytest

from zoltar_ranks.ingest.incomplete import Incomplete

HARVESTERS = ["harvest_ranks", "harvest_er", "harvest_shap", "harvest_daily_ranks"]


# ------------------------------ the tracker ------------------------------

def test_incomplete_is_falsy_and_zero_when_nothing_missed_pure():
    inc = Incomplete("t")
    assert not inc and len(inc) == 0
    assert inc.exit_code() == 0


def test_incomplete_exits_nonzero_after_one_failure_pure(caplog):
    inc = Incomplete("t")
    with caplog.at_level(logging.WARNING):
        inc.record("some/file.pkl@abc123", RuntimeError("boom"))
    assert inc and len(inc) == 1
    with caplog.at_level(logging.ERROR):
        assert inc.exit_code(intended=228) == 1
    joined = caplog.text
    assert "INCOMPLETE" in joined
    assert "some/file.pkl" in joined, "the unread file must be named, not just counted"


def test_incomplete_reports_scope_when_known_pure(caplog):
    inc = Incomplete("t")
    inc.record("a", "x")
    with caplog.at_level(logging.ERROR):
        inc.exit_code(intended=10)
    assert "of 10" in caplog.text


# --------------------- every harvester honours it ---------------------

@pytest.mark.parametrize("modname", HARVESTERS)
def test_harvester_uses_the_shared_tracker_pure(modname):
    """Cheap structural guard: a new harvester that forgets this fails here."""
    import importlib
    mod = importlib.import_module(f"zoltar_ranks.ingest.{modname}")
    src = __import__("inspect").getsource(mod)
    assert "Incomplete" in src, (
        f"{modname} does not use ingest.incomplete.Incomplete. Every harvester "
        f"that reads files it might fail to read must exit nonzero when it does.")
    assert "exit_code" in src, f"{modname} never calls Incomplete.exit_code()"
    assert "return 0" not in src.split("def main")[-1], (
        f"{modname}.main() has a bare `return 0`; it must return "
        f"tracker.exit_code() so an unread file cannot report success.")


@pytest.mark.parametrize("modname", ["harvest_er", "harvest_shap"])
def test_harvester_exits_nonzero_when_every_read_fails(modname, tmp_path, monkeypatch):
    """Behavioural proof, not just structural: break reads, expect rc != 0."""
    import importlib
    from zoltar_ranks.sources.git_archive import UpstreamMirror

    mod = importlib.import_module(f"zoltar_ranks.ingest.{modname}")
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(f"duckdb_path: {(tmp_path / 'x.duckdb').as_posix()}\n")

    monkeypatch.setattr(UpstreamMirror, "ensure", lambda self: None)
    monkeypatch.setattr(
        UpstreamMirror, "commits_touching",
        lambda self, path, since=None: [
            __import__("zoltar_ranks.sources.git_archive", fromlist=["Snapshot"]).Snapshot(
                sha="deadbeef" * 5,
                committed_at=__import__("datetime").datetime(2026, 9, 1, 12, 0),
                path=path)])

    def boom(self, sha, path):
        raise RuntimeError("simulated unreadable blob")
    monkeypatch.setattr(UpstreamMirror, "read_pickle", boom)

    rc = mod.main(["--mode", "daily", "--config", str(cfg_path)])
    assert rc != 0, (
        f"{modname}.main() returned {rc} after EVERY read failed. A short "
        f"archive must never report success.")
