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

## Notes

- H9 and H10 are the falsification tests for the whole premise. Run them first.
  If H10 is confirmed, the ranking model is not the value driver and the
  research priority shifts entirely to exit-rule design.
- "MDE" = minimum detectable effect at 80% power given the available sample.
  Fill it in before moving a row to `running`. If the MDE exceeds the effect
  size worth acting on, mark `underpowered` and do not run the test.
