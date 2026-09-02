"""The repo-wide invariant: `harvest_manifest` is the record of WORK DONE.

Every harvester must consult it **before reading**, not only when inserting.

Why this module exists
----------------------
`harvest_manifest` has PRIMARY KEY `(file_path, commit_sha)` and a row is written
only *after* a successful read. That makes it an exact, permanent record of what
has already been read -- not a heuristic. Yet a harvester can consult it when
writing and never when reading, and nothing downstream notices, because the rows
are already there and the insert is a no-op.

That is precisely the defect this module closes. `harvest_daily_ranks` parsed
`--mode` and never read it, so both modes ran the same path: **228 blobs,
~4.8 GB, ~866k rows staged, 0 inserted, every 30 minutes** -- 94% of the
scheduled tick (290.6 s of 309.5 s, measured 2026-09-02), on the machine that is
simultaneously running the live model re-scores.

**Row-idempotency is not work-idempotency.** "staged=866402 inserted=0, table
counts unchanged" proves the *rows* are idempotent and says nothing about the
*work*. Same shape as the `-Once` scheduler trigger: the field we checked was not
the field that mattered. `tests/test_manifest.py` asserts on the blob-read
count, not on rows.

Two properties that make the skip exact rather than approximate
---------------------------------------------------------------
* **`daily_ranks/` files are immutable once added.** A new build is a new
  filename, never a rewrite. So `(file_path, commit_sha)` present in the manifest
  means "fully read", permanently.
* **`production/*_latest.pkl` is rewritten in place**, but the manifest key is
  per-*commit*, so the same path at a new sha is correctly a different unit of
  work. The helper is right for both shapes.

Two traps, both load-bearing
----------------------------
1. **A zero-file guard belongs on files DISCOVERED, never on files TO READ.**
   After this filter, "zero new files" is the normal outcome on 28 ticks out of
   29. A guard moved to the post-skip list fires constantly, becomes noise, and
   stops guarding anything. `harvest_daily_ranks` keeps `if not files: return 1`
   on the enumeration, where it still catches the directory moving or the regex
   breaking.
2. **A failed read must still retry.** The manifest row is written only after a
   successful read, so an unreadable blob is never recorded and comes back on the
   next run. That is how the 8 unreadable `all_high` files of 2026-07-21/22/23
   were picked up. Do not "optimise" by recording attempts.
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")


def already_read(con, file_paths: Iterable[str]) -> set[tuple[str, str]]:
    """The `(file_path, commit_sha)` pairs the manifest says are fully read.

    Scoped to `file_paths` so this stays bounded as the manifest grows.
    """
    paths = sorted({p for p in file_paths})
    if not paths:
        return set()
    placeholders = ",".join("?" * len(paths))
    rows = con.execute(
        f"SELECT file_path, commit_sha FROM harvest_manifest "
        f"WHERE file_path IN ({placeholders})", paths).fetchall()
    return {(r[0], r[1]) for r in rows}


def unread(con, candidates: Sequence[T], *,
           key: Callable[[T], tuple[str, str]],
           log: logging.Logger | None = None,
           label: str = "",
           force: bool = False) -> list[T]:
    """Return only the candidates the manifest has no successful read for.

    `key` maps a candidate to its `(file_path, commit_sha)`. `force=True` ignores
    the manifest entirely -- the deliberate full re-read escape hatch. It warns,
    because on this data it is the difference between ~3 s and ~290 s.
    """
    items = list(candidates)
    if force:
        if log:
            log.warning("--force: manifest NOT consulted, re-reading all %d %s "
                        "file(s). This is the expensive path.", len(items), label)
        return items
    pairs = [key(c) for c in items]
    done = already_read(con, [p for p, _ in pairs])
    todo = [c for c, pair in zip(items, pairs) if pair not in done]
    if log:
        log.info("%s: %d known, %d already read, %d to read",
                 label or "files", len(items), len(items) - len(todo), len(todo))
    return todo
