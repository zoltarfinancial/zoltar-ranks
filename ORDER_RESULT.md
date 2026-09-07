# ORDER_RESULT — 004-count-docs (inventory the docs directory)

**Answer: 8 `.md` files under `docs/`** (recursive; no subdirectories exist).

Five largest by line count:

| # | File | Lines |
|---|------|-------|
| 1 | `docs/SESSION_LOG.md` | 700 |
| 2 | `docs/PLAN.md` | 417 |
| 3 | `docs/FINDINGS.md` | 403 |
| 4 | `docs/ALIGNMENT_ANCHOR.md` | 331 |
| 5 | `docs/HYPOTHESES.md` | 192 |

Remaining three: `DASHBOARD.md` (184), `LOCAL_ARCHIVE_GATE.md` (163),
`HANDOFF_CLAUDE_CODE.md` (149). Total 2539 lines.

## What changed

Only this file. Read-only inventory — no code, data, or docs were modified.

## Notes / not done

- Counts are newline counts (`wc -l`); a file with no trailing newline would
  read one line low.
- Did not run `pytest` or add a `docs/SESSION_LOG.md` entry: nothing executable
  changed and there was no decision, surprise, or workaround to log.
- One command (`find … | xargs -0 wc -l`) was blocked by the permission layer;
  the same counts were obtained via a plain `wc -l` over the globbed file list.
