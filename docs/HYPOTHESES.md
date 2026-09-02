# Hypothesis register

Every experiment gets a row here **before** it runs. Rejected hypotheses stay.
The row count is the denominator for the Benjamini-Hochberg FDR correction
(q = 0.10) applied across the whole register.

Status: `proposed` | `powered` | `underpowered` | `running` | `confirmed` | `rejected`

| ID | Date | Statement | Lever | Pre-registered test | MDE | Status | Result | 95% CI | FDR p |
|----|------|-----------|-------|---------------------|-----|--------|--------|--------|-------|
| H9 | 2026-09-01 | Most of BASELINE_ASIS's edge is same-bar execution, not signal | execution | BASELINE_ASIS vs BASELINE_HONEST, identical selection, 15-min latency, intraday exit evaluation | TBD | proposed | | | |
| H10 | 2026-09-01 | The -1%/+2% bracket, not the ranks, is the source of return | attribution | RANDOM_5 with identical exit rule, 1000 bootstrap draws, vs BASELINE_HONEST | TBD | proposed | | | |
| H1 | 2026-09-01 | Realized IC peaks at a shorter horizon than Score_HoldPeriod (7.5d) | exit rule | IC by horizon 1,2,3,5,7,10,14d on validate_oot rows only | TBD | proposed | | | |
| H2 | 2026-09-01 | Delaying entry past the open improves net entry price without losing edge | entry timing | entry-time grid, Phase 6a | TBD | proposed | | | |
| H3 | 2026-09-01 | The latest intraday re-score beats the morning score at the same entry time | rank vintage | vintage x entry-time cross, holding entry time fixed | TBD | proposed | | | |
| H4 | 2026-09-01 | Scores are not comparable across cap segments; per-segment z-scoring beats pooled top-5 | normalization | pooled vs per-segment-z top-5, same execution | TBD | proposed | | | |
| H5 | 2026-09-01 | Excluding the lowest ADV/price quintile raises net-of-cost return | universe | filtered vs unfiltered BASELINE_HONEST | TBD | proposed | | | |
| H6 | 2026-09-01 | The -1%/+2% bracket is asymmetric in the wrong direction given observed skew | exit rule | grid over stop x target, walk-forward validated | TBD | proposed | | | |
| H7 | 2026-09-01 | omit_first > 0 improves returns (top name is crowded or already moved) | selection | omit_first in 0..3 | TBD | proposed | | | |
| H8 | 2026-09-01 | SHAP feature drift predicts IC decay and is usable as a live risk-off signal | monitoring | rolling feature-importance distance vs forward 20d IC | TBD | proposed | | | |
| H12a | 2026-09-01 | **Data advance, model held fixed.** The last intraday re-score's top-5 differs materially from that morning's, and the disagreement is informative rather than noise | data advance | overlap and rank correlation, morning vs last-intraday top-N same date; then paired-by-date forward return of intraday-only vs morning-only names | TBD - **compute before building** | proposed | | | |
| H12b | 2026-09-01 | **Retrain value, data roughly fixed.** The nightly RETRAINED model's top-5 differs materially from the last intraday re-score's, and the disagreement is informative rather than noise | model retrain | overlap and rank correlation, last-intraday vs nightly-retrain top-N same date; then paired-by-date forward return of nightly-only vs intraday-only names. **defined against the `evening_retrains` view** (F4), which excludes the day's final intraday re-score | TBD (n~64 paired days) | proposed | | | |
| H11 | 2026-09-01 | A portfolio entered in extended hours on the nightly-build ranks, on extended-hours-eligible tickers only, beats the same selection entered at the next regular open, net of a widened extended-hours spread assumption | entry timing x venue | nightly-vintage top-5, `available_at` + latency fills in extended hours with an EH cost model, vs. the same selection filled at the next regular open | **0.157%/run** (n=63, ~9.9%/yr) | proposed | | | |

## Notes

- H9 and H10 are the falsification tests for the whole premise. Run them first.
  If H10 is confirmed, the ranking model is not the value driver and the
  research priority shifts entirely to exit-rule design.
- "MDE" = minimum detectable effect at 80% power given the available sample.
  Fill it in before moving a row to `running`. If the MDE exceeds the effect
  size worth acting on, mark `underpowered` and do not run the test.

### H11 — MDE derivation and the power call (computed 2026-09-01)

Reported before any Phase 6 nightly machinery is written, as instructed.

- **n = 63** of 64 nightly runs are usable (all five names priced at the nightly
  print and at the next observation). One run has no successor yet.
- **sd of the per-run paired difference = 0.445%** (0.422% winsorized at 1/99).
  H11's paired difference *is* the overnight gap on the selected basket, so this
  was estimated directly: equal-weight top-5 (`baseline_risk_bucket=low`,
  `baseline_top_x=5`) move from the nightly `close_price` to the next available
  `close_price`.
- **MDE = (1.96 + 0.84) x sd / sqrt(n) = 0.157% per run**, 80% power, two-sided
  alpha = 0.05. Winsorized: 0.149%. Cumulatively ~9.9%/yr over 63 nightly runs.

### H11 work order — the spread survey is a GATE, not a follow-up

**Do this before building any Phase 6 nightly machinery.** Survey observed
extended-hours bid-ask spreads on the extended-hours-eligible subset of the
nightly top-5. **If the median spread exceeds the 0.157% MDE, H11 is dead on
cost alone** — mark it `rejected (cost)` and save the ~4 days of machinery.

The survey needs a price provider, so it waits on credentials. It does not wait
on anything else, and nothing in Phase 6's nightly path should be built until it
returns. Sequence:

1. Credentials land -> implement a provider that serves EH quotes.
2. Populate `symbol_venue.extended_hours_eligible`.
3. Survey median EH spread on the eligible nightly top-5 names.
4. **Gate:** median spread vs. 0.157%. Above it, stop and record the rejection.
5. Only if it clears: recompute the MDE with the cost model, then build.

**The power call is Andrew's threshold, not a fact.** MDE ~9.9%/yr means:
if an edge worth acting on is >=10%/yr, H11 is `powered`; if the bar is 3-5%/yr,
it is `underpowered` and should not be run at this n. Left at `proposed` pending
that call.

Three caveats that could each move the MDE:

1. `close_price`'s split-adjustment status is unverified (FINDINGS F7), so the
   sd above may carry split artifacts. Winsorizing barely moved it (0.445% ->
   0.422%), which argues against gross contamination, but this is provisional
   until `corporate_actions` exists.
2. The proxy for "next regular open" is the next *rank observation*, not a real
   opening print. Phase 2 replaces it with an actual open, which will change sd.
3. The EH cost model adds variance the estimate above does not contain, so the
   real MDE is **at least** 0.157% and probably higher. Extended-hours spreads
   on thin names plausibly cost more per round trip than the entire MDE, so the
   realistic question is whether edge-minus-spread clears 0.157% — a materially
   harder test than edge alone. Recompute the MDE with the cost model attached
   before moving H11 to `running`.


### H12a / H12b — why the split, and the power warning

FINDINGS F4 records the run-type 2x2. The two hypotheses isolate one factor each:

| run | model | data |
|---|---|---|
| morning | fresh build | prior close |
| intraday | **morning's models, UNCHANGED** | partial-day, extrapolated |
| nightly | **fully retrained** | complete day |

So **morning -> intraday isolates the data advance with the model held exactly
fixed** (H12a), and **last-intraday -> nightly isolates the retrain with data
roughly fixed** (H12b). Together they decompose the morning-vs-nightly
difference, which a single H12 would have measured only as a blend.

**⚠️ H12a is underpowered TODAY but expected to improve.** It needs *multiple
intraday decision points per day*, which in git exist densely only from
**2026-08-19** (~13 days). That is **not** a permanent ceiling: FINDINGS F2 is
resolved -- the earlier intraday files were moved to offline SSD storage and
never committed, so the history exists and is captured-live. Once that archive is
ingested (a later phase), H12a's n grows substantially.

So: **compute H12a's MDE before building any machinery for it** -- same
discipline as H11 -- but treat a `underpowered` verdict as provisional and
re-compute after the offline archive lands, rather than abandoning the
hypothesis.

**H12b is defined against the `evening_retrains` view**, which already excludes
the early AFTERCLOSE mode. Those 7 runs at 15:27-15:31 are the day's **final
intraday re-score**, not a retrain -- 5 of them share a day with a real evening
retrain, arriving ~35 min after a PRECLOSE in the ordinary 30-minute cadence
(F4). They belong with H12a's population, not H12b's.

⚠️ **H12b must group by `stamp_convention`.** The 2026-09-02 cutover means
pre- and post-cutover evening rows encode availability two different ways, and
H11's extended-hours advantage was derived from the forward stamp. Pooling them
mixes conventions silently.
