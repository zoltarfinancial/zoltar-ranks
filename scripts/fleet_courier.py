"""ZoltarLead's courier: a Claude-free scheduled task that lands this node's
dispatch paths on ``origin/fleet/dispatch``.

Order ``t-20260908-courier-zoltarlead`` (approved by Andrew 2026-09-08T15:21:58-05:00,
board event e-000073-z7kax8). Stdlib only. It runs git and ``scripts/fleet_sync.py``
and nothing else -- it never runs a model, never installs anything, never touches a
credential file, and never touches ``main``.

Why it exists: the db->git hop on this node had no carrier. For five days the brain
wrote cycles and orders here and none of them left the machine, and the rest of the
fleet correctly concluded from ``origin/main`` that the review protocol was dead.

Each run
--------
1. ``git fetch --prune origin``
2. bring a DEDICATED WORKTREE to ``fleet/dispatch`` (see "Where this departs")
3. carry ``DISPATCH_PATHS`` from the main clone into it -- additively
4. commit only if there is something worth committing
5. ``git push origin HEAD:refs/heads/fleet/dispatch``
6. run ``scripts/fleet_sync.py`` with the venv interpreter
7. write a receipt -- ON FAILURE TOO, naming the failing step and the exact stderr

Where this departs from the order's literal steps, and why
---------------------------------------------------------
* **No ``git switch`` in the main clone.** The order says ``git switch
  fleet/dispatch``. This clone is also where ``ZoltarRanksHarvest`` (every 30 min)
  and ``ZoltarFleetHeartbeat`` (hourly) execute their code, and where the worker
  lane commits on feature branches. Switching HEAD every 10 minutes would change the
  code those tasks run whenever branches diverge, and could land a worker's commit
  on ``fleet/dispatch``. The prior art (``data/fleet/zoltarone-tooling/push-receipt.ps1``)
  uses a separate worktree for exactly this reason: "committing receipts must never
  touch the order branch, the working tree, or HEAD." Same here.
* **A new ``fleet/dispatch`` is cut from ``origin/main``, not "the current head".**
  The current head is whatever branch the worker lane is on; cutting from it would
  leak unmerged feature commits into the dispatch channel.
* **Additive, never destructive.** A file already on ``fleet/dispatch`` with
  different content is NOT overwritten -- it is reported as a conflict -- unless it
  is a path this node is the one writer of. Append-only logs (``*.jsonl``) are
  UNION-merged. On 2026-09-08 a plain ``git add data/review/`` would have deleted 10
  inbox events that existed on trunk but not here; a courier doing that every 10
  minutes would be a data-loss machine.
* **Routine receipts do not by themselves trigger a commit.** ``fleet_sync.py``
  writes a receipt on every run, and this courier writes one for every run that
  does something. If receipts alone made the index "dirty", step 4's condition could
  never be false and the branch would take ~144 commits a day of receipts about
  receipts. They ride along with the next real commit instead -- at most an hour
  late, because the hourly fleet heartbeat is always new content. FAILURE receipts
  are the exception: they are rare and they trigger a commit on the next run that
  can push.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE_ID = "zoltarlead"
BRANCH = "fleet/dispatch"
REMOTE = "origin"

#: Step 3 of the order, verbatim. Note the order's TITLE also names `bridge/`, but
#: its enumerated step 3 does not; the enumeration is followed and the discrepancy
#: is reported for Andrew rather than resolved here by adding scope.
DISPATCH_PATHS = ("orders", "fleet/msgs", "fleet/receipts",
                  "data/fleet/heartbeat", "data/review")

#: Paths this node is the ONLY writer of. Here, local wins over what the branch
#: holds, because nobody else can have a newer version.
ONE_WRITER = (f"data/fleet/heartbeat/{NODE_ID}.json",
              f"fleet/receipts/{NODE_ID}/*",
              f"fleet/msgs/*-{NODE_ID}-*")

#: Append-only logs: union, never overwrite (R7, and the 2026-09-08 near-miss).
APPEND_ONLY = ("*.jsonl",)

#: Receipts that describe routine success. They ride along; they never cause a
#: commit on their own (see module docstring).
ROUTINE_RECEIPT = (f"fleet/receipts/{NODE_ID}/*",)

#: git stderr that means "authenticate, a human has to do this". The order says to
#: stop and report, and never to switch transports.
AUTH_MARKERS = ("Authentication failed", "could not read Username",
                "terminal prompts disabled", "Invalid username or token",
                "Permission denied", "403", "401", "denied to")

AUTH_FIX = (f'git -C "{REPO_ROOT}" push {REMOTE} {BRANCH}   '
            "-- run ONCE in a normal interactive terminal on ZoltarLead and complete "
            "the Git Credential Manager browser prompt (or run `gh auth login`). "
            "Do not change the remote URL or transport.")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _env() -> dict:
    # Never block on a prompt under S4U: there is no desktop to answer it. These
    # are process environment variables, not credential changes.
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
            "PYTHONIOENCODING": "utf-8"}


def git(args, cwd: Path, timeout: int = 180) -> dict:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=_env())
        return {"cmd": "git " + " ".join(args), "rc": p.returncode,
                "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except Exception as exc:                                       # noqa: BLE001
        return {"cmd": "git " + " ".join(args), "rc": -1, "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}"}


class StepFailed(Exception):
    def __init__(self, step: str, result: dict):
        super().__init__(step)
        self.step, self.result = step, result


def _match(rel: str, patterns) -> bool:
    return any(fnmatch.fnmatch(rel, p) for p in patterns)


def assert_not_main(ref: str) -> None:
    """Belt and braces for the one constraint that must never move."""
    tail = ref.split(":")[-1]
    if tail in ("main", "refs/heads/main", "master", "refs/heads/master") or \
            tail.endswith("/main") and not tail.endswith(BRANCH):
        raise RuntimeError(f"REFUSING: courier asked to write {ref!r}. Trunk is Andrew's.")


# ------------------------------- step 3: carry -------------------------------

def union_jsonl(dest_text: str, src_text: str) -> str:
    """Keep BOTH sides, drop nothing, dedupe on canonical JSON, order by `at`.

    Unparseable lines are preserved verbatim, never repaired in place.
    """
    rows, seen = [], set()
    for origin, text in (("dest", dest_text), ("src", src_text)):
        for i, raw in enumerate(text.splitlines()):
            raw = raw.rstrip("\r")
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
                key = json.dumps(obj, sort_keys=True, ensure_ascii=False)
                at = str(obj.get("at") or obj.get("finished_at") or "") if isinstance(obj, dict) else ""
            except Exception:                                      # noqa: BLE001
                key, at = "RAW:" + raw, ""
            if key in seen:
                continue
            seen.add(key)
            rows.append((at, origin != "dest", i, raw))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    return "\n".join(r[3] for r in rows) + ("\n" if rows else "")


def carry(src_root: Path, wt: Path) -> dict:
    """Copy DISPATCH_PATHS into the worktree. Additive; conflicts are reported."""
    out = {"added": [], "updated_one_writer": [], "unioned": [], "conflicts": [],
           "missing_sources": []}
    for top in DISPATCH_PATHS:
        base = src_root / top
        if not base.exists():
            out["missing_sources"].append(top)
            continue
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
        for f in files:
            rel = f.relative_to(src_root).as_posix()
            dest = wt / rel
            data = f.read_bytes()
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                out["added"].append(rel)
                continue
            if dest.read_bytes().replace(b"\r\n", b"\n") == data.replace(b"\r\n", b"\n"):
                continue
            if _match(rel, APPEND_ONLY):
                merged = union_jsonl(dest.read_text(encoding="utf-8"),
                                     data.decode("utf-8", errors="replace"))
                if merged.encode() != dest.read_bytes():
                    dest.write_text(merged, encoding="utf-8", newline="\n")
                    out["unioned"].append(rel)
            elif _match(rel, ONE_WRITER):
                dest.write_bytes(data)
                out["updated_one_writer"].append(rel)
            else:
                # Never silently pick a winner (courier-protocol rule 1).
                out["conflicts"].append(rel)
    return out


# --------------------------------- the run ---------------------------------

def run(repo: Path = REPO_ROOT, wt: Path | None = None, remote: str = REMOTE,
        python: str | None = None, do_sync: bool = True) -> dict:
    wt = wt or repo.parent / f"{repo.name}.courier-wt"
    stamp = utc_stamp()
    rec: dict = {
        "schema_version": 1, "kind": "courier", "node_id": NODE_ID,
        "agent_id": f"{NODE_ID}/courier", "run_at": now_iso(), "stamp": stamp,
        "branch": BRANCH, "worktree": str(wt), "outcome": "error",
        "failed_step": None, "steps": [], "carried": None, "committed": None,
        "pushed_sha": None, "needs_andrew": None,
        "_note": "Written by a scheduled task that runs git and fleet_sync.py only. No model.",
    }

    def step(name, result, ok_codes=(0,)):
        rec["steps"].append({"step": name, **result})
        if result["rc"] not in ok_codes:
            raise StepFailed(name, result)
        return result

    try:
        # 1 ---------------------------------------------------------------
        step("1.fetch", git(["fetch", "--prune", remote], repo))
        remote_has = bool(git(["ls-remote", "--heads", remote, BRANCH], repo)["stdout"])

        # 2 --------------------------------------------------------------- worktree
        if not (wt / ".git").exists():
            if wt.exists():
                shutil.rmtree(wt, ignore_errors=True)       # stale dir from a killed run
            step("2.worktree-prune", git(["worktree", "prune"], repo))
            base = f"{remote}/{BRANCH}" if remote_has else f"{remote}/main"
            step("2.worktree-add", git(["worktree", "add", "--force", "-B", BRANCH,
                                        str(wt), base], repo))
            rec["base"] = base
        elif remote_has:
            # The worktree is disposable staging. Its content is REBUILT from the
            # main clone each run, so resetting to the remote loses nothing -- an
            # unpushed commit from a failed run is simply re-created below.
            step("2.reset-to-remote", git(["reset", "--hard", f"{remote}/{BRANCH}"], wt))
            rec["base"] = f"{remote}/{BRANCH}"
        else:
            rec["base"] = "existing-local-worktree"
        head = git(["rev-parse", "--abbrev-ref", "HEAD"], wt)["stdout"]
        if head != BRANCH:
            raise StepFailed("2.branch-check", {"cmd": "rev-parse", "rc": 1, "stdout": head,
                                                "stderr": f"worktree is on {head!r}, not {BRANCH}"})
        rec["base_sha"] = git(["rev-parse", "HEAD"], wt)["stdout"]

        # 3 --------------------------------------------------------------- carry
        rec["carried"] = carry(repo, wt)
        present = [d for d in DISPATCH_PATHS if (wt / d).exists()]
        if present:        # a pathspec that matches nothing makes `git add` exit 128
            step("3.add", git(["add", "--", *present], wt))
        staged = [p for p in git(["diff", "--cached", "--name-only"], wt)["stdout"].splitlines() if p]
        rec["staged"] = staged

        # 4 --------------------------------------------------------------- commit?
        failure_receipts = [p for p in staged if _is_failure_receipt(wt / p)]
        worthy = [p for p in staged if not _match(p, ROUTINE_RECEIPT)] + failure_receipts
        rec["commit_worthy"] = worthy
        if worthy:
            # the receipt for a committing run goes IN the commit, so it is never
            # left behind to make the next run dirty
            rec["outcome"] = "committing"
            _write_receipt(rec, repo, wt, stamp)
            step("3.add-receipt", git(["add", "--", f"fleet/receipts/{NODE_ID}"], wt))
            msg = (f"courier({NODE_ID}): {len(worthy)} dispatch path(s)\n\n"
                   "Landed by the ZoltarLead courier, a scheduled task that runs git and\n"
                   "scripts/fleet_sync.py only -- no model. Order t-20260908-courier-zoltarlead.\n")
            # the repo's configured identity; only the display name marks it as the
            # courier, so a courier commit is distinguishable from a session's
            step("4.commit", git(["-c", "user.name=ZoltarLead courier",
                                  "commit", "-m", msg], wt))
            rec["committed"] = git(["rev-parse", "HEAD"], wt)["stdout"]

            # 5 ----------------------------------------------------------- push
            target = f"HEAD:refs/heads/{BRANCH}"
            assert_not_main(target)
            step("5.push", git(["push", remote, target], wt, timeout=300))
            rec["pushed_sha"] = rec["committed"]
        else:
            rec["committed"] = None
            rec["_no_commit_reason"] = ("nothing to land" if not staged else
                                        "only routine receipts changed; they ride with the next real commit")

        # 6 --------------------------------------------------------------- sync
        if do_sync:
            py = python or str(repo / ".venv" / "Scripts" / "python.exe")
            # NEVER --push: fleet_sync's --push does a bare `git push` of whatever
            # branch the main clone is on, which is exactly what this courier must
            # never do.
            args = [py, str(repo / "scripts" / "fleet_sync.py")]
            try:
                p = subprocess.run(args, cwd=str(repo), capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=300, env=_env())
                r = {"cmd": " ".join(args), "rc": p.returncode,
                     "stdout": p.stdout.strip()[-1500:], "stderr": p.stderr.strip()[-1500:]}
            except Exception as exc:                               # noqa: BLE001
                r = {"cmd": " ".join(args), "rc": -1, "stdout": "", "stderr": str(exc)}
            step("6.fleet_sync", r)

        rec["outcome"] = "ok" if rec["committed"] else "ok-nothing-to-land"
    except StepFailed as exc:
        rec["outcome"] = "error"
        rec["failed_step"] = exc.step
        rec["stderr"] = exc.result.get("stderr")
        if exc.step == "5.push" and any(m in (exc.result.get("stderr") or "") for m in AUTH_MARKERS):
            rec["needs_andrew"] = AUTH_FIX
    except Exception:                                              # noqa: BLE001
        rec["outcome"] = "error"
        rec["failed_step"] = rec["failed_step"] or "unhandled"
        rec["stderr"] = traceback.format_exc()[-2000:]
    finally:
        # 7 --- ALWAYS leave evidence. A failure receipt goes to the main clone so
        #       the next run that can push carries it.
        if rec["outcome"] == "error" or rec["committed"]:
            try:
                _write_receipt(rec, repo, None, stamp)
            except Exception:                                      # noqa: BLE001
                traceback.print_exc()
        _append_runlog(rec, wt)
    return rec


def _is_failure_receipt(p: Path) -> bool:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d.get("kind") == "courier" and d.get("outcome") == "error"
    except Exception:                                              # noqa: BLE001
        return False


def _write_receipt(rec: dict, repo: Path, wt: Path | None, stamp: str) -> None:
    rel = Path("fleet") / "receipts" / NODE_ID / f"courier-{stamp}.json"
    body = json.dumps(rec, indent=2, ensure_ascii=False) + "\n"
    for root in (repo, wt):
        if root is None:
            continue
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")


def _append_runlog(rec: dict, wt: Path) -> None:
    """One line per fire, outside the repo -- the evidence the task ran even when
    nothing was worth committing. Same idea as zoltarone's heartbeat_runs.jsonl."""
    try:
        log = wt.parent / f"{wt.name}.runs.jsonl"
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"run_at": rec["run_at"], "outcome": rec["outcome"],
                                 "failed_step": rec["failed_step"],
                                 "committed": rec["committed"],
                                 "pushed_sha": rec["pushed_sha"]}) + "\n")
    except Exception:                                              # noqa: BLE001
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=str(REPO_ROOT))
    ap.add_argument("--worktree", default=None)
    ap.add_argument("--remote", default=REMOTE)
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    rec = run(Path(a.repo), Path(a.worktree) if a.worktree else None, a.remote,
              do_sync=not a.no_sync)
    if a.json:
        print(json.dumps(rec, indent=2))
    else:
        print(f"courier {rec['outcome']}  committed={rec['committed']}  "
              f"pushed={rec['pushed_sha']}  failed_step={rec['failed_step']}")
        if rec.get("needs_andrew"):
            print(f"NEEDS ANDREW: {rec['needs_andrew']}")
    # Exit 0 even on error: the receipt is the signal, and a non-zero exit would
    # only make Task Scheduler retry-storm on a condition already recorded.
    return 0


if __name__ == "__main__":
    sys.exit(main())
