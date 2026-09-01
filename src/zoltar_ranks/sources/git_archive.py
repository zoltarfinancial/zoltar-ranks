"""Read point-in-time snapshots out of the upstream ZoltarFinancial git history.

Why this exists
---------------
The upstream repo overwrites `production/*_latest.pkl` on every run. Two of
those files are *append-only archives* (`low/high_risk_PROD_latest.pkl`, back to
2026-01-01, one snapshot per day) and two are *rolling buffers* capped at 200 run
timestamps (`all_*_PROD_latest.pkl`, which carry the intraday snapshots and lose
roughly 130 timestamps every two weeks).

Verified 2026-09-01: scores are NEVER restated. Comparing the 2026-08-18 blob to
HEAD gave 185,880 overlapping rows with 100% identical Score and Close_Price. So
the union of all historical blobs is a genuine, leak-free point-in-time archive.

The only way to recover intraday history older than the rolling window is to read
older git blobs. That is what this module does, using a *blobless* clone
(`--filter=blob:none`) so we pay ~1 MB for the commit graph and then fetch only
the blobs we actually need.
"""
from __future__ import annotations

import io
import pickle
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None, binary: bool = False):
    proc = subprocess.run(
        args, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise GitError(f"{' '.join(args[:4])}... failed: {proc.stderr.decode(errors='replace')[:800]}")
    return proc.stdout if binary else proc.stdout.decode(errors="replace")


@dataclass(frozen=True)
class Snapshot:
    sha: str
    committed_at: datetime
    path: str


class UpstreamMirror:
    """A blobless mirror of the upstream repo, used purely as a time machine."""

    def __init__(self, url: str, mirror_dir: Path, branch: str = "main"):
        self.url = url
        self.dir = Path(mirror_dir)
        self.branch = branch

    # ---------- lifecycle ----------

    def ensure(self) -> None:
        """Clone if missing, otherwise fetch. Safe to call on every run."""
        if (self.dir / "HEAD").exists() or (self.dir / ".git").exists():
            _run(["git", "fetch", "--filter=blob:none", "origin",
                  f"+refs/heads/{self.branch}:refs/heads/{self.branch}"], cwd=self.dir)
            return
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--filter=blob:none", "--bare",
              "--branch", self.branch, self.url, str(self.dir)])

    # ---------- history ----------

    def commits_touching(self, path: str, since: str | None = None) -> list[Snapshot]:
        """Newest-first list of commits that changed `path`."""
        args = ["git", "log", self.branch, "--format=%H\t%cI"]
        if since:
            args.append(f"--since={since}")
        args += ["--", path]
        out = _run(args, cwd=self.dir)
        snaps: list[Snapshot] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            sha, iso = line.split("\t")
            snaps.append(Snapshot(sha=sha, committed_at=datetime.fromisoformat(iso), path=path))
        return snaps

    # ---------- blob access ----------

    def read_blob(self, sha: str, path: str) -> bytes:
        """Fetch one file's bytes as of one commit. Triggers a lazy blob fetch."""
        return _run(["git", "cat-file", "-p", f"{sha}:{path}"], cwd=self.dir, binary=True)

    def read_pickle(self, sha: str, path: str) -> pd.DataFrame:
        raw = self.read_blob(sha, path)
        obj = pickle.loads(raw)
        if not isinstance(obj, pd.DataFrame):
            raise GitError(f"{path}@{sha[:8]} unpickled to {type(obj)}, expected DataFrame")
        return obj

    def read_pickle_head(self, path: str) -> pd.DataFrame:
        return self.read_pickle(self.branch, path)
