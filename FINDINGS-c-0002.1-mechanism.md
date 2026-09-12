# c-0002.1 — mechanism: what the CODE says about the −25 minute offset

**Question asked.** How much wall-clock time elapses between the moment a run
READS prices and the moment it stamps `available_at` on the resulting rank?

**Answer, in one line.** The code **cannot speak to it**: in every repository and
working copy reachable from this machine, *nothing reads a price on the path that
produces a rank*. `close_price` arrives as a value already inside an upstream
pickle, and `available_at` is computed from three stamps that upstream wrote.
The read→stamp interval happens entirely inside a program that has never been
committed anywhere we can read.

Nothing below changes a measurement, a rule, or the verdict. The anchor's
pre-registered verdict remains **`STOP_TIMEZONE`**, and the measured offset
remains **−25 min, 95% CI [−27, −24]**. This file adds no number to the anchor.

---

## 1. The actual path, step by step

All line references are to this branch's tree (`origin/main` @ `1a05afa`), plus
the upstream mirror at `apod-1/ZoltarFinancial` @ `7834369`.

### 1.1 Where `close_price` comes from

It is **copied, unmodified, out of an upstream pickle column**.

* `src/zoltar_ranks/ingest/harvest_ranks.py:47` — the rename map:

  ```python
  "Close_Price": "close_price",
  ```

* `src/zoltar_ranks/ingest/harvest_ranks.py:72-89` — `normalize()` renames,
  selects `keep`, coerces `run_ts` to datetime, uppercases `symbol`, drops
  duplicates. There is **no arithmetic on `close_price` anywhere in it**, and no
  provider call.

* The bytes come from a git blob, not a quote feed:
  `src/zoltar_ranks/sources/git_archive.py:113-122`

  ```python
  def read_blob(self, sha: str, path: str) -> bytes:
      """Fetch one file's bytes as of one commit. Triggers a lazy blob fetch."""
      return _run(["git", "cat-file", "-p", f"{sha}:{path}"], cwd=self.dir, binary=True)
  ```

* It lands in `ranks.close_price` (`src/zoltar_ranks/db/schema.sql:28`), keyed
  `(run_ts, symbol, risk_bucket)` (`schema.sql:36`), append-only by rule 2.

The repo's own price providers (`src/zoltar_ranks/sources/prices.py`) exist, but
they feed `prices_daily` / `prices_intraday` and the anchor's bar cache. **No
harvester on the `ranks` path calls one.**

### 1.2 Where `available_at` is set

**It is never "set".** There is no write-time assignment: it is a derived column
of a SQL view, computed at query time from three upstream stamps.

`src/zoltar_ranks/db/schema.sql:183-217`, the arithmetic at `:193-195`:

```sql
       least(coalesce(p.build_stamp,  r.run_ts),
             coalesce(p.committed_at, r.run_ts),
             r.run_ts)                                       AS available_at,
```

and the contract above it, `schema.sql:157-160`:

```
--   available_at = min(build_stamp, committed_at, run_ts)
--
-- Each is an UPPER BOUND on when the run was public, so the earliest is the
-- tightest truth, and min() needs no fallback chain and no special-casing.
```

The three inputs:

| input | where it is produced | where the code takes it |
|---|---|---|
| `run_ts` | the pickle's own `Date` column, written by upstream | `harvest_ranks.py:36` (`"Date": "run_ts"`), parsed at `:83` |
| `build_stamp` | the upstream **filename** `all_{low,high}_risk_PROD_YYYYMMDD_HHMMSS.pkl` | `harvest_daily_ranks.py:53` (`PROD_RE`), `:78` `datetime.strptime(d + t, "%Y%m%d%H%M%S")`, stored `:198-202` into `harvest_manifest.build_stamp` (`schema.sql:12-16`) |
| `committed_at` | the upstream **git commit time** (`%cI`) | `harvest_daily_ranks.py:62-69` |

**All three are upstream clocks.** Not one of them is a clock in this repo. The
only clock this repo writes is `harvest_manifest.harvested_at`
(`schema.sql:11`, `DEFAULT current_timestamp`), and it appears nowhere in
`available_at`.

### 1.3 Every step in between — and what it adds

```
  [upstream machine]  reads prices  ──► ??? ──►  writes Date + filename stamp, writes pickle
                                                          │
                                   upstream git commit ───┤ committed_at
                                                          ▼
  [this repo]  git fetch (blobless mirror) ──► read_blob ──► normalize() ──► INSERT ranks
                                                                                  │
                                        ranks_pit view: least(build_stamp, committed_at, run_ts)
                                                                                  ▼
                                                                           available_at
```

The `???` is the interval the question asks about. Everything to its right is
timestamp *transport*: the harvest adds latency to when a row becomes **visible
to us**, and none at all to `available_at`, because `least(...)` selects a value
fixed upstream before the push.

### 1.4 Is the gap configurable? Does it vary per run?

**Not from here, on either count.**

* `config/config.yaml` holds `price_provider`, four `baseline_*` knobs and
  `stamp_cutover_date`. `stamp_cutover_date` only labels `stamp_convention`
  (`schema.sql:212-215`); it does not enter `available_at`.
* Rule 3's latency is an *execution* parameter applied **after** `available_at`
  and is required to be strictly positive —
  `src/zoltar_ranks/analysis/execution.py:89-99`:

  ```python
  def latency_floor(cfg) -> timedelta:
      """Rule 3's configured latency. Zero is rejected here, not downstream."""
  ```

  It moves fills later, so it cannot produce a negative offset in the anchor.
* The harvest cadence (30 minutes; `scripts/daily.py::STEPS:113-125`) moves
  `committed_at → harvested_at`, never `available_at`.

Per-run variation in the measured offset therefore has **no code-visible source
in this repo**: the same three lines of SQL run for every row, with no per-run
branch, no rounding, and no clock of ours.

---

## 2. What the code *does* pin

Three things worth stating, because they are the only hard constraints the code
supplies — none of them is the answer to the question.

1. **`available_at` for an intraday run is a single upstream clock reading.**
   `schema.sql:161-165` records the step-2 audit: for morning, intraday and
   placeholder builds the newest `Date` equals the filename build stamp
   **exactly** (delta 0.00h). The already-published result file carries the
   matching fact for this sample —
   `data/results/alignment_anchor_intraday.json`: `"run_ts_differs_from_available_at": 0`.
   So filename stamp and in-file `Date` are one `now()` in the producing process,
   and the archive neither adds nor subtracts time from it.

2. **The only build-timing instrument in the repo watches a different interval.**
   `harvest_daily_ranks.py:204-210` logs when the newest `Date` is more than
   60 seconds *after* the build stamp:

   ```python
   # Expected for the nightly retrain (~+24h). Anywhere else it would
   # mean the file is named at build START, which makes build_stamp
   # optimistic as an availability bound -- report, never silently use.
   ```

   That bounds *stamp-in-name vs. newest-Date*. It says nothing about when a
   price was read before either of them.

3. **The measurement side is not a candidate explanation.** Bars are keyed on
   **bar OPEN time** (`prices.py:106`, `schema.sql:105`), tz-converted once at
   the provider boundary (`prices.py:175-178`, `MARKET_TZ = "America/Chicago"`
   at `:45`), and the comparison price is "the close of the last bar whose OPEN
   time ≤ `available_at` + h", within a 10-minute staleness tolerance
   (`alignment_anchor.py:416-447`, `BAR_TOLERANCE_MIN` at `:374`). A bar-labelling
   convention can move the reported offset by at most **one minute**, not 25.

---

## 3. Where the answer would have to live — and why it is not reachable

The producing pipeline is **not in the upstream repository**, at HEAD or in
history.

* Every `.py`/`.ipynb`/`.sh`/`.bat`/`.ps1` ever *added* upstream
  (`git log --all --diff-filter=A --name-only`) is one of:
  `app/Strategy_Play*.py` (≈46 Streamlit variants), `ZoltarResearch/*.py`,
  `app/authentication.py`, `ZoltarBot/main.py`, `ZoltarBot/main_WIP.py`,
  `run_apps.sh`, `.github/workflows/streamlit.yml`.
* In `app/Strategy_Play.py`, **every** reference to `*_PROD_*` is a read
  (`os.listdir`, `get_latest_file`, `os.path.join` on
  `{low,high}_risk_PROD_latest.pkl`). The single `pickle.dump` in that whole tree
  is an OAuth token cache at `app/authentication.py:202`.
* The `.pkl` files arrive as `Automated commit: <date> <time>` commits from a
  machine whose code is never committed.
* Nothing on this disk holds it either: a search of `C:\Shared` for `_PROD_`
  across `.py`, `.ipynb`, `.ps1`, `.bat`, `.sh` returns only this repo's own
  harvesters, tests and their backups.

Rule 1 was respected: upstream was read via a read-only, blobless sparse clone in
the session scratchpad. Nothing was written to upstream or to the mirror.

---

## 4. Verdict on corroboration: **the code CANNOT SPEAK TO the −25 minutes**

Stated plainly, in the three directions the order asks for:

* **It does not support the measurement.** There is no code path anywhere in this
  repo in which a price is read and a rank is later stamped. Code-visible elapsed
  time between those two events, on our side: **zero, because neither event
  happens here.** A mechanism cannot be confirmed from an artifact that records
  only the result.

* **It does not contradict the measurement.** Nothing in the code forbids an
  upstream build from carrying a `Close_Price` ≈25 minutes older than its own
  `now()`. `available_at` is the minimum of three upstream stamps, every one of
  which is at or after any read that preceded it; the archive can make
  `close_price` neither younger nor the stamp older.

* **It does rule out one family of explanations** — that the offset is an artifact
  of *this* repo. It is not produced by the 30-minute harvest tick, not by push
  lag in `committed_at` (the `least()` keeps the earliest bound), not by a
  timezone conversion (`prices.py:177` is the only conversion in the codebase and
  sits at the provider boundary; the archive is tz-naive Chicago end to end), and
  not by rule 3's execution latency (applied after `available_at`, strictly
  positive by construction at `execution.py:89-99`).

One direction statement, offered as arithmetic and not as a fit: because
`available_at = min(build_stamp, committed_at, run_ts)`, any residual error in it
is biased **early**, and an earlier anchor makes the measured offset *less*
negative. The only archive-side bias available therefore pushes toward zero — it
cannot manufacture −25 minutes. That is a bound on our side, not evidence for a
mechanism on Andrew's.

**Therefore: the mechanism in `docs/FINDINGS.md` (prices fetched, then ~25 min of
build, and/or a delayed quote feed) remains exactly as labelled there —
unverified.** It needs one line from Andrew: which call the build uses for
`Close_Price`, and when it runs relative to the build's `now()`. No code in
reach can answer it.

---

## 5. Scope note

* No measurement re-run, no file under `data/results/` touched, no constant in
  `analysis/alignment_anchor.py` changed, no gate re-gated.
* The pre-registered verdict rule is untouched and still returns `STOP_TIMEZONE`.
* This document is corroboration attempted from an independent source. The honest
  outcome of that attempt is **"the source is silent"**, which is reported here
  rather than dressed up as agreement.

### Appendix — how this was checked

```
git log --all --diff-filter=A --name-only --format="" -- "*.py" "*.ipynb" "*.sh" "*.bat" "*.ps1"   # upstream mirror, read-only
git ls-tree -r --name-only main                                                                    # upstream tree census
grep -n "available_at" src/ ; grep -n "Close_Price|close_price" src/
grep -rn "_PROD_" C:\Shared --glob "*.{py,ipynb,ps1,bat,sh}"
```

Files read for the trace: `src/zoltar_ranks/ingest/harvest_ranks.py`,
`src/zoltar_ranks/ingest/harvest_daily_ranks.py`,
`src/zoltar_ranks/sources/git_archive.py`, `src/zoltar_ranks/db/schema.sql`,
`src/zoltar_ranks/analysis/alignment_anchor.py`,
`src/zoltar_ranks/sources/prices.py`, `src/zoltar_ranks/analysis/execution.py`,
`scripts/daily.py`, `config/config.yaml`, and the upstream mirror at `7834369`.
