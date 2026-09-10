"""The ZoltarLead courier, proven against a real throwaway bare remote.

Every test builds its own bare repo and clone under tmp_path. Nothing here can
reach the real origin, and nothing touches this repo's refs.

The properties are the ones the order's acceptance depends on, plus the two that
would make a 10-minute courier dangerous rather than useful: it must never touch
`main`, and it must never destroy content that exists on the branch but not here.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts import fleet_courier as fc


def sh(args, cwd):
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {args}: {p.stderr}"
    return p.stdout.strip()


@pytest.fixture()
def world(tmp_path):
    remote = tmp_path / "remote.git"
    sh(["init", "--bare", "-b", "main", str(remote)], tmp_path)
    repo = tmp_path / "clone"
    sh(["clone", str(remote), str(repo)], tmp_path)
    for k, v in (("user.name", "test"), ("user.email", "test@example.invalid")):
        sh(["config", k, v], repo)
    (repo / "data" / "review").mkdir(parents=True)
    (repo / "data" / "review" / "inbox.jsonl").write_text(
        '{"at":"2026-09-01T00:00:00","by":"main","type":"note"}\n', encoding="utf-8")
    (repo / "README.md").write_text("trunk\n", encoding="utf-8")
    sh(["add", "-A"], repo)
    sh(["commit", "-m", "trunk"], repo)
    sh(["push", "origin", "main"], repo)
    # the worker lane sits on a feature branch, as it does on the real node
    sh(["switch", "-c", "feature/work"], repo)
    return {"remote": remote, "repo": repo, "wt": tmp_path / "courier-wt"}


def run(w, **kw):
    return fc.run(w["repo"], w["wt"], do_sync=False, **kw)


def remote_sha(w, branch):
    out = sh(["ls-remote", "--heads", str(w["remote"]), branch], w["repo"])
    return out.split()[0] if out else None


def remote_file(w, branch, path):
    p = subprocess.run(["git", "--git-dir", str(w["remote"]), "show", f"{branch}:{path}"],
                       capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


# --------------------------- acceptance #2 / #3 ---------------------------

def test_a_dropped_order_lands_on_fleet_dispatch(world):
    (world["repo"] / "orders" / "zoltarone").mkdir(parents=True)
    (world["repo"] / "orders" / "zoltarone" / "t-1.md").write_text("do it\n", encoding="utf-8")
    r = run(world)
    assert r["outcome"] == "ok", r
    assert r["pushed_sha"] and remote_sha(world, "fleet/dispatch") == r["pushed_sha"]
    assert remote_file(world, "fleet/dispatch", "orders/zoltarone/t-1.md") == "do it\n"


def test_a_new_branch_is_cut_from_origin_main_not_the_feature_head(world):
    """Cutting from 'current head' would leak the worker's unmerged commits."""
    (world["repo"] / "secret-feature.txt").write_text("wip\n", encoding="utf-8")
    sh(["add", "-A"], world["repo"])
    sh(["commit", "-m", "unmerged feature work"], world["repo"])
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    run(world)
    assert remote_file(world, "fleet/dispatch", "secret-feature.txt") is None


# --------------------------- acceptance #5: main ---------------------------

def test_main_is_never_moved(world):
    before = remote_sha(world, "main")
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    run(world)
    (world["repo"] / "orders" / "o2.md").write_text("y\n", encoding="utf-8")
    run(world)
    assert remote_sha(world, "main") == before


@pytest.mark.parametrize("ref", ["main", "HEAD:main", "HEAD:refs/heads/main",
                                 "HEAD:refs/heads/master"])
def test_the_push_guard_refuses_trunk(ref):
    with pytest.raises(RuntimeError, match="REFUSING"):
        fc.assert_not_main(ref)


def test_the_push_guard_allows_fleet_dispatch():
    fc.assert_not_main("HEAD:refs/heads/fleet/dispatch")


def test_the_main_clone_head_and_branch_are_untouched(world):
    """No `git switch`: the harvest and the worker lane run from this tree."""
    head_before = sh(["rev-parse", "HEAD"], world["repo"])
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    run(world)
    assert sh(["rev-parse", "--abbrev-ref", "HEAD"], world["repo"]) == "feature/work"
    assert sh(["rev-parse", "HEAD"], world["repo"]) == head_before


# ------------------------- additive, never destructive -------------------------

def test_append_only_logs_are_unioned_not_overwritten(world):
    """The 2026-09-08 near-miss: trunk held events this node did not."""
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "seed.md").write_text("s\n", encoding="utf-8")
    run(world)                                   # something to land creates the branch
    # another node's event lands on fleet/dispatch
    other = world["repo"].parent / "other"
    sh(["clone", "-b", "fleet/dispatch", str(world["remote"]), str(other)], world["repo"].parent)
    for k, v in (("user.name", "o"), ("user.email", "o@example.invalid")):
        sh(["config", k, v], other)
    with (other / "data" / "review" / "inbox.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at":"2026-09-05T00:00:00","by":"zoltarone","type":"note"}\n')
    sh(["commit", "-am", "remote event"], other)
    sh(["push", "origin", "fleet/dispatch"], other)
    # meanwhile this node appended its own
    with (world["repo"] / "data" / "review" / "inbox.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"at":"2026-09-06T00:00:00","by":"zoltarlead","type":"note"}\n')

    r = run(world)
    assert r["outcome"] == "ok", r
    text = remote_file(world, "fleet/dispatch", "data/review/inbox.jsonl")
    assert '"by":"zoltarone"' in text, "the courier deleted another node's event"
    assert '"by":"zoltarlead"' in text
    assert '"by":"main"' in text
    ats = [json.loads(l)["at"] for l in text.splitlines()]
    assert ats == sorted(ats), "union must be in timestamp order"


def test_a_differing_file_on_the_branch_is_a_conflict_not_an_overwrite(world):
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("v1\n", encoding="utf-8")
    run(world)
    other = world["repo"].parent / "other"
    sh(["clone", "-b", "fleet/dispatch", str(world["remote"]), str(other)], world["repo"].parent)
    for k, v in (("user.name", "o"), ("user.email", "o@example.invalid")):
        sh(["config", k, v], other)
    (other / "orders" / "o.md").write_text("edited elsewhere\n", encoding="utf-8")
    sh(["commit", "-am", "edit"], other)
    sh(["push", "origin", "fleet/dispatch"], other)

    (world["repo"] / "orders" / "new.md").write_text("n\n", encoding="utf-8")  # something to land
    r = run(world)
    assert "orders/o.md" in r["carried"]["conflicts"]
    assert remote_file(world, "fleet/dispatch", "orders/o.md") == "edited elsewhere\n", (
        "never silently pick a winner (courier-protocol rule 1)")


def test_one_writer_paths_are_updated(world):
    hb = world["repo"] / "data" / "fleet" / "heartbeat"
    hb.mkdir(parents=True)
    (hb / "zoltarlead.json").write_text('{"at":"1"}\n', encoding="utf-8")
    run(world)
    (hb / "zoltarlead.json").write_text('{"at":"2"}\n', encoding="utf-8")
    r = run(world)
    assert "data/fleet/heartbeat/zoltarlead.json" in r["carried"]["updated_one_writer"]
    assert remote_file(world, "fleet/dispatch", "data/fleet/heartbeat/zoltarlead.json") == '{"at":"2"}\n'


# ---------------------------- no commit loop ----------------------------

def test_nothing_new_means_no_commit(world):
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    first = run(world)
    second = run(world)
    assert second["committed"] is None
    assert remote_sha(world, "fleet/dispatch") == first["pushed_sha"]


def test_routine_receipts_alone_do_not_trigger_a_commit(world):
    """fleet_sync writes a receipt every run. If that alone made the index dirty,
    step 4 could never be false: ~144 commits a day of receipts about receipts."""
    run(world)
    rd = world["repo"] / "fleet" / "receipts" / "zoltarlead"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "000009-20260910T000000.json").write_text('{"kind":"sync"}\n', encoding="utf-8")
    sha = remote_sha(world, "fleet/dispatch")
    r = run(world)
    assert r["committed"] is None and "ride with the next real commit" in r["_no_commit_reason"]
    assert remote_sha(world, "fleet/dispatch") == sha


def test_routine_receipts_ride_along_with_the_next_real_commit(world):
    run(world)
    rd = world["repo"] / "fleet" / "receipts" / "zoltarlead"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "sync-a.json").write_text('{"kind":"sync"}\n', encoding="utf-8")
    run(world)
    (world["repo"] / "orders").mkdir(exist_ok=True)
    (world["repo"] / "orders" / "real.md").write_text("r\n", encoding="utf-8")
    run(world)
    assert remote_file(world, "fleet/dispatch", "fleet/receipts/zoltarlead/sync-a.json")


def test_a_committing_run_carries_its_own_receipt(world):
    """So the receipt is never left behind to make the next run dirty."""
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    r = run(world)
    name = f"fleet/receipts/zoltarlead/courier-{r['stamp']}.json"
    assert remote_file(world, "fleet/dispatch", name)


# ----------------------- acceptance #4: failure receipts -----------------------

def test_an_unreachable_remote_still_writes_a_receipt_naming_the_step(world):
    sh(["remote", "set-url", "origin", str(world["repo"].parent / "does-not-exist.git")],
       world["repo"])
    r = run(world)
    assert r["outcome"] == "error"
    assert r["failed_step"] == "1.fetch"
    assert r["stderr"], "the exact git stderr must be recorded"
    rec = world["repo"] / "fleet" / "receipts" / "zoltarlead" / f"courier-{r['stamp']}.json"
    saved = json.loads(rec.read_text(encoding="utf-8"))
    assert saved["failed_step"] == "1.fetch" and saved["outcome"] == "error"


def test_a_failure_receipt_is_carried_by_the_next_run_that_can_push(world):
    good = sh(["remote", "get-url", "origin"], world["repo"])
    sh(["remote", "set-url", "origin", str(world["repo"].parent / "nope.git")], world["repo"])
    bad = run(world)
    sh(["remote", "set-url", "origin", good], world["repo"])
    ok = run(world)
    assert ok["committed"], "a failure receipt must be commit-worthy on its own"
    assert remote_file(world, "fleet/dispatch",
                       f"fleet/receipts/zoltarlead/courier-{bad['stamp']}.json")


def test_push_auth_failure_stops_and_names_the_command_for_andrew(world, monkeypatch):
    real = fc.git

    def fake(args, cwd, timeout=180):
        if args and args[0] == "push":
            return {"cmd": "git push", "rc": 128, "stdout": "",
                    "stderr": "fatal: Authentication failed for 'https://github.com/...'"}
        return real(args, cwd, timeout)
    monkeypatch.setattr(fc, "git", fake)
    (world["repo"] / "orders").mkdir()
    (world["repo"] / "orders" / "o.md").write_text("x\n", encoding="utf-8")
    r = run(world)
    assert r["failed_step"] == "5.push" and r["outcome"] == "error"
    assert r["needs_andrew"] and "push origin fleet/dispatch" in r["needs_andrew"]
    assert "Do not change the remote URL" in r["needs_andrew"], "never switch transports"


def test_the_runlog_records_every_fire_including_no_ops(world):
    run(world)
    run(world)
    log = world["wt"].parent / f"{world['wt'].name}.runs.jsonl"
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2


# ------------------------------- no model -------------------------------

def test_the_courier_never_invokes_a_model():
    src = Path(fc.__file__).read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith(("#", "*")))
    for banned in ('"claude"', "'claude'", "claude -p", "anthropic"):
        assert banned not in code.split('"""', 2)[-1], banned


def test_fleet_sync_is_never_run_with_push():
    """fleet_sync's --push pushes whatever branch the MAIN clone is on."""
    src = Path(fc.__file__).read_text(encoding="utf-8")
    assert '"--push"' not in src and "'--push'" not in src
