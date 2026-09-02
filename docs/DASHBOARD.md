# Dashboard contract — how two workstreams stay out of each other's way

Two people are building this repo at once:

| Workstream | Owner | Owns these paths |
|---|---|---|
| **Backend** — archive, prices, benchmark, stats, hypothesis testing | the Claude Code session on Andrew's machine | `src/`, `scripts/`, `tests/`, `config/`, `docs/PLAN.md`, `docs/FINDINGS.md` |
| **Dashboard** — the research console Andrew reads and iterates on | the Cowork session | `dashboard/` |

They meet at exactly one place: **`data/results/dashboard_data.json`**. The
backend writes it; the dashboard reads it. Neither side reaches past it.

Rules:

- The dashboard **never** connects to DuckDB, imports `zoltar_ranks`, or reads
  Parquet. One JSON file, produced by the backend, is the whole interface.
- The backend **never** edits anything under `dashboard/`.
- Changing the JSON schema is a two-sided change: update this file first, then
  both sides.
- `docs/HYPOTHESES.md` stays the source of truth for the register. The exporter
  parses it; the dashboard renders what it is given. Nobody hand-maintains a
  second copy of the hypothesis list.

## Backend deliverable: `analysis/export_dashboard_data.py`

Appended to `scripts/daily.py::STEPS` so the JSON refreshes on every run.
Write it atomically (temp file, then replace) so the dashboard never reads a
half-written file. Every numeric result carries its uncertainty interval —
a dashboard that displays point estimates without CIs re-introduces exactly the
overconfidence the plan exists to prevent.

```jsonc
{
  "generated_at": "2026-09-02T18:30:00",       // tz-naive America/Chicago
  "schema_version": 1,

  "archive_health": {
    "feeds": [
      { "feed": "daily", "risk_bucket": "low", "runs": 234,
        "first_run": "2025-10-01T07:46:57", "last_run": "2026-09-02T00:00:00",
        "rows": 277831 }
    ],
    "last_harvest_at": "2026-09-02T18:00:04",
    "hours_since_last_run_ts": 3.2,            // dashboard alarms above ~24
    "failed_steps": [],                        // from last_run_status.json
    "expected_returns": { "as_of_dates": 31, "first": "...", "last": "..." },
    "shap": { "snapshots": 214, "segments": ["Large","Mid","Small"] }
  },

  "signal_health": {
    // Information Coefficient: Spearman rank corr of score vs forward return.
    "ic_by_horizon": [
      { "horizon_days": 1, "risk_bucket": "low", "ic": 0.021,
        "ci_low": -0.004, "ci_high": 0.046, "n_obs": 254000 }
    ],
    "ic_rolling_20d": [
      { "date": "2026-08-31", "risk_bucket": "low", "ic": 0.03,
        "ci_low": 0.001, "ci_high": 0.059 }
    ],
    "ic_by_segment":  [ { "segment": "Small", "horizon_days": 5, "ic": 0.04,
                          "ci_low": 0.01, "ci_high": 0.07, "n_obs": 61000 } ],
    "quantile_spread": [ { "decile": 10, "mean_fwd_return": 0.004,
                           "ci_low": 0.001, "ci_high": 0.007 } ]
  },

  "benchmarks": {
    "strategies": [
      { "id": "BASELINE_ASIS",   "label": "Streamlit engine, as-is",
        "total_return": 0.41, "annualized": 0.55, "sharpe": 1.9,
        "sharpe_ci": [0.7, 3.1], "max_drawdown": -0.12, "hit_rate": 0.58,
        "trades": 212, "avg_hold_days": 2.4, "exposure": 0.81,
        "note": "same-bar execution - NOT tradable, kept as the bias reference" },
      { "id": "BASELINE_HONEST", "label": "Same rules, realistic fills", "...": "..." },
      { "id": "RANDOM_5",        "label": "Random 5, same exit rule",    "...": "..." },
      { "id": "SPY",             "label": "SPY buy & hold",              "...": "..." }
    ],
    "equity_curves": [
      { "id": "BASELINE_HONEST",
        "points": [ { "date": "2025-10-01", "value": 1.0 } ] }
    ],
    "comparisons": [
      { "a": "BASELINE_ASIS", "b": "BASELINE_HONEST", "metric": "annualized",
        "diff": 0.23, "ci_low": 0.09, "ci_high": 0.38, "p": 0.003,
        "fdr_p": 0.015, "n_eff": 118 }
    ]
  },

  "hypotheses": [
    { "id": "H9", "statement": "...", "lever": "execution", "status": "confirmed",
      "date_registered": "2026-09-01", "date_resolved": "2026-09-14",
      "test": "...", "mde": 0.05, "result": 0.23,
      "ci_low": 0.09, "ci_high": 0.38, "fdr_p": 0.015,
      "learning": "One or two sentences. THIS is what Andrew comes back to read.",
      "links": ["data/results/benchmark_report.html"] }
  ],

  "timing": {
    // Phase 6. Null until it runs; the dashboard must render an empty state.
    "decay_curve": [ { "minutes_after_run": 30, "ic": 0.02,
                       "ci_low": 0.0, "ci_high": 0.04 } ],
    "grid": [ { "entry_time": "09:35", "exit_rule": "bracket_-1_+2",
                "vintage": "morning", "order_type": "market",
                "net_return_bps": 12.4, "ci_low": -3.0, "ci_high": 27.8 } ],
    "recommendation": { "entry_time": "10:00", "vintage": "latest_intraday",
                        "expected_bps": 9.1, "ci_low": 1.2, "ci_high": 17.0,
                        "deflated_sharpe": 0.61, "configs_searched": 1296,
                        "walk_forward_holds": true }
  },

  "shap_drift": [
    { "snapshot_ts": "2026-09-01T10:30:40", "segment": "Large",
      "feature": "Slope_3w_woe", "mean_abs_shap": 0.0031, "rank": 1 }
  ]
}
```

### Schema addenda — 2026-09-02, when the exporter was first built

Four additive changes. No existing field changed shape.

- **`archive_health.hours_since_last_run_ts` is computed from `available_at`,
  not from `run_ts`**, and `freshness_basis` records that. This is not a
  preference — upstream forward-stamps the evening retrain, so the newest
  `run_ts` in the archive is routinely in the **future** (`2026-09-03 00:00:00`
  observed while it was 2026-09-02). Computed from `run_ts` this field goes
  **negative**, the ~24h staleness alarm never fires, and a dead harvester
  renders as perfectly healthy. Rule 5, and measured.
- **`archive_health.last_run_ts` and `.last_available_at`** are both emitted, so
  the gap between them is visible rather than implied.
- **`sections_absent`** — an object mapping each unbuilt section
  (`signal_health`, `benchmarks`, `timing`) to the reason it is missing. The
  contract already said the dashboard renders "not yet measured"; this lets it
  say *why* instead of just showing a gap.
- **`hypotheses[].mde_raw`** — the register's MDE cell verbatim. `mde` is
  numeric only when the cell **leads** with a number (`0.157%/run (n=63, ...)`
  → `0.157`). A loose numeric search would read `TBD (n~64 paired days)` as an
  MDE of `64` — not a missing value but a fabricated one, which is worse,
  because it renders as a real number.

Currently emitted: `archive_health`, `hypotheses`, `shap_drift`.
`shap_drift` is capped at the newest 60 snapshots x top 10 features per segment
(~1,100 rows, ~200 KB); uncapped it is multi-megabyte for no extra signal.

Notes on fields that are easy to get wrong:

- `ic_by_horizon.n_obs` is the **cross-sectional** count (symbols × days), which
  is large. `comparisons.n_eff` is the **effective** sample after accounting for
  autocorrelation, which is small. Displaying the first where the second belongs
  makes thin evidence look strong — the dashboard must label them differently.
- `configs_searched` exists so the timing recommendation can never be shown
  without its multiple-testing context.
- Sections may be absent while their phase is unbuilt. The dashboard renders an
  explicit "not yet measured" state, never a zero or a blank chart.

## Dashboard deliverable

A self-contained HTML page in `dashboard/`, opened locally, that answers:

1. Is the archive healthy and is the harvester running? (alarm if stale)
2. What have we tested, what did we learn, what is still open?
3. Where does the signal actually live — horizon, segment, liquidity, regime?
4. What does the timing study say, and how much should we believe it?
5. **What should we test next?** — the exploratory surface: the point of the
   visual layer is that Andrew spots patterns the pre-registered tests missed.

Design intent: this is a research console Andrew will look at daily for months.
It should be genuinely beautiful — dense without being cluttered, calm, readable
in a dark room at 6am. Every estimate shows its uncertainty. Nothing is
displayed as a bare number when it is really a distribution.
