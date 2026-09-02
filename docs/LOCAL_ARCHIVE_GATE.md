# Acceptance gate for the local intraday archive

**Status: design only. Nothing here is built, and no local row enters `ranks`
until Andrew answers §4 and the gate in §1 passes.**

## Why a gate at all

The git-sourced archive is *proven* point-in-time: FINDINGS F1 measured 185,880
overlapping rows with **100.0% identical `Score` and `Close_Price`** and max
absolute difference 0.0. That property is what makes a backtest on it honest.

A local file archive has no equivalent proof. Windows mtimes do not survive
copies, moves, or restores reliably, and nothing about a pickle on disk records
when it was written or whether it was captured live or regenerated later. So the
local archive is not a peer of the git archive — it is a **candidate** measured
against it. Git is the spine; local rows are admitted only where they cannot
contradict it.

The asymmetry is deliberate: git rows can never be corrected by local rows, in
either direction. Rule 2 (append-only) already forbids the `UPDATE`; this gate
forbids the softer version, where a disagreement gets "reconciled" into a blend.

---

## 1. Identity gate — the hard precondition

On every `(run_ts, symbol, risk_bucket)` present in **both** the local archive and
`ranks`:

- `Score` **100% identical**
- `Close_Price` **100% identical**

Same assertion as F1/F2, and it **fails loudly**. No tolerance band, no rounding
window beyond float equality at the stored precision, no "close enough" path, and
above all **no reconciliation** — if the two sources disagree about what the model
said at a past instant, exactly one of them is wrong about history, and merging
them produces an archive that is wrong in a way no later test can detect.

A failure here is a **finding to report**, not a bug to fix:

- **Zero overlap** → the gate has not passed, it has been skipped. Treat as
  failure. An archive that cannot be checked has not been verified.
- **Partial mismatch** → report the mismatching rows, their run classes, and
  their date range. Do not admit *any* local rows until the cause is understood;
  a source that is wrong about one instant is not trustworthy about others.
- **Wholesale mismatch** → likely a different `Cap_Size` vintage, a regenerated
  file, or an adjusted-price variant. Diagnose before ingesting anything.

Overlap should be substantial before the pass means much. Suggest requiring at
least ~20 distinct run timestamps and ~10k rows of overlap; below that, report
the pass as provisional rather than treating it as F1-grade evidence.

---

## 2. Provenance required for `available_at`

Rule 5 keys every execution decision off `available_at`, and rule 3 measures
latency from it. So:

> **An untrusted availability timestamp is worse than no row at all.** A missing
> row costs sample size, which is visible in every CI. A wrong `available_at`
> silently shifts a fill earlier than it could have happened and manufactures
> edge that no test will catch.

A local file may source `available_at` only from evidence that is *intrinsic* or
*independently corroborated*:

| tier | evidence | `availability_source` | usable for |
|---|---|---|---|
| **A** | Filename build stamp **and** the same run present in git, agreeing | `local_corroborated` | anything, incl. H11 |
| **B** | Filename build stamp, run absent from git, convention validated on the overlap | `local_build_stamp` | anything, flagged |
| **C** | Capture-log / manifest written at capture time, naming the file and the clock time | `local_capture_log` | anything, flagged |
| **D** | mtime only | `local_mtime_untrusted` | **nothing timing-sensitive** |
| **E** | nothing | — | **row is not admitted** |

Tier D is not a weaker version of C — it is a different claim. An mtime says when
the bytes were last written, which is not when the information became knowable,
and a single robocopy makes it a lie. **Tier D rows must be excluded from H11,
H12a/b and all of Phase 6**, and may be used only for signal-quality work that
never simulates a fill (IC by horizon, SHAP drift).

**The fallback when provenance is absent is exclusion, not estimation.** Do not
infer `available_at` from `run_ts` for local rows the way `ranks_pit` does for git
rows: that inference is licensed by the step-2 audit, which measured filename
stamp == newest `Date` against *git* files. It has not been shown to hold for
local ones, and assuming it would import the conclusion into the evidence.

Before trusting tier B or C at scale, validate the convention **on the overlap**:
for runs present in both, check that the local-derived `available_at` matches the
git-derived one. Report the distribution of the difference. A tight distribution
centred on zero earns the tier; a skewed one does not.

---

## 3. Admitting rows that git does not have

These are the point of the exercise — the intraday runs the rolling buffer
destroyed — and also the rows nothing can cross-check. They are admitted, but
never silently:

- **`feed = 'local_archive'`**, so provenance is a first-class column and every
  downstream query can partition on it. Existing feeds (`daily`, `all`,
  `daily_ranks`) are untouched.
- **`availability_source`** carries the tier from §2, so a result can be
  recomputed on tier A+B only and compared against A–D. Any headline number
  should be reported both ways; if they disagree, the weaker-provenance rows are
  driving it and that is itself the finding.
- **Append-only, as always.** A local row whose `(run_ts, symbol, risk_bucket)`
  already exists in `ranks` is **dropped, not merged** — git wins by construction.
  The §1 gate has already established they agree, so dropping loses nothing.
- **A coverage report** stating, per date, how many run timestamps came from git,
  from local-only, and at which tier. Phase 6 must be able to say exactly which
  part of its evidence base has no independent corroboration.

The seam matters as much as the rows. Any result spanning the boundary between
git-corroborated and local-only history should report the split, because a change
in measured edge at that boundary is more likely a provenance artifact than a
change in the market.

---

## 4. What I need from Andrew before ingesting

1. **Directory layout.** One flat directory or nested by date? Are the two risk
   buckets and the `all_` variant all present?
2. **Naming convention.** Same `{all_,}{low,high}_risk_PROD_YYYYMMDD_HHMMSS.pkl`
   shape as upstream, or something else? Does the stamp mean build **start** or
   **completion**? (Step 2 could not distinguish these for git files; for local
   files it decides whether tier B is an upper bound or an optimistic one.)
3. **Are the mtimes original?** Specifically: were these files ever copied,
   moved, restored from backup, synced through OneDrive/Dropbox, or unzipped? Any
   one of those makes every mtime tier D.
4. **Was anything regenerated rather than captured live?** A file rebuilt later
   from a stored model is scored by a *newer* model than the one that ran that
   day — the exact in-sample contamination rule 4 exists to prevent, and it would
   look like an unusually good signal rather than an error. If any subset was
   regenerated, it must be identified and excluded, not just flagged.
5. **Is there a capture log** — a script, a scheduled task, a manifest — that
   recorded when each file was written? That is the difference between tier C and
   tier D for everything without a filename stamp.
6. **Does the local archive contain `*_rankings_*` files?** If so they are
   excluded under rule 4 exactly as upstream's are, and I will not read them.

Question 4 is the one that would do the most damage if answered wrong, so it is
worth checking rather than recalling — a regenerated file is indistinguishable
from a captured one by inspection, and it fails the §1 gate only if it happens to
overlap git.
