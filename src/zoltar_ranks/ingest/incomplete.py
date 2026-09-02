"""The repo-wide invariant: a harvester that could not read something exits nonzero.

Every harvester reads many blobs and must survive one bad one -- abandoning 227
files because the 228th failed is worse than useless. So they log and continue.
The defect that pattern invites is the one this module exists to close:

    the run continues, finishes, and returns 0.

A silently short archive is worse than a failed run, because **nothing
downstream can tell the difference**. A missing run timestamp does not look like
an error; it looks like a day the model did not produce a rank, which is a
perfectly ordinary thing for it to look like. The failure is then indistinguishable
from data, forever.

So: record every intended-but-unread file, and let `exit_code()` decide. Callers
end with `return tracker.exit_code()`, and `scripts/daily.py` propagates a
nonzero step into `last_run_status.json` and the process exit code.

Enforced by tests/test_incomplete.py, which fails if any harvester returns 0
after a read failure.
"""
from __future__ import annotations

import logging


class Incomplete:
    """Tracks files a harvester intended to read but could not."""

    def __init__(self, name: str, log: logging.Logger | None = None):
        self.name = name
        self.log = log or logging.getLogger(name)
        self.items: list[str] = []

    def record(self, what: str, exc: BaseException | str) -> None:
        self.log.warning("unreadable %s: %s", what, str(exc)[:160])
        self.items.append(what)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def exit_code(self, intended: int | None = None) -> int:
        """0 only if nothing was missed. Logs loudly enough to act on."""
        if not self.items:
            return 0
        scope = f" of {intended}" if intended is not None else ""
        self.log.error(
            "INCOMPLETE: %s could not read %d%s file(s) it intended to read. "
            "The archive is SHORT by whatever they held, and nothing downstream "
            "can detect that. Fix the cause and re-run (harvesters are "
            "idempotent). Files: %s",
            self.name, len(self.items), scope, self.items[:10])
        return 1
