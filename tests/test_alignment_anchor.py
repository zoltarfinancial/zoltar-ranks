"""The daily alignment anchor's decision logic (`docs/ALIGNMENT_ANCHOR.md` §3).

The measurement itself needs the network and is recorded in FINDINGS F8. What is
pinned here is the part that must not drift: the session grid, the Wilson
interval, and above all the **verdict**, which has to stop the phase rather than
reconcile. Rule 9 -- a failed anchor stops Phase 2; it is not widened.
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from zoltar_ranks.analysis import alignment_anchor as aa


def _detail(rows):
    """rows: (run_ts, subset, candidate, rate)."""
    return pd.DataFrame([
        {"run_ts": r, "subset": s, "candidate": c,
         "tolerance_bps": round(aa.DECISION_TOL * 10000), "rate": v}
        for r, s, c, v in rows])


# ------------------------------- the verdict -------------------------------

def test_verdict_pass_when_prior_close_wins_everywhere():
    d = _detail([("r1", "pre_open", "close_T_minus_1", 0.99),
                 ("r1", "pre_open", "close_T", 0.03),
                 ("r2", "pre_open", "close_T_minus_1", 0.97),
                 ("r2", "pre_open", "close_T", 0.02)])
    assert aa.verdict(d)["state"] == "PASS"


def test_verdict_stops_on_lookahead():
    """close(T) winning means a morning build carries a price it could not know.

    That invalidates Phases 3 and 6, not just Phase 2, so it gets its own state
    rather than being folded into 'ambiguous'.
    """
    d = _detail([("r1", "pre_open", "close_T_minus_1", 0.30),
                 ("r1", "pre_open", "close_T", 0.98)])
    assert aa.verdict(d)["state"] == "STOP_LOOKAHEAD"


def test_verdict_stops_when_no_candidate_clears_the_bar():
    d = _detail([("r1", "pre_open", "close_T_minus_1", 0.60),
                 ("r1", "pre_open", "close_T", 0.05)])
    v = aa.verdict(d)
    assert v["state"] == "STOP_AMBIGUOUS"
    assert "do NOT widen" in v["note"] or "not widen" in v["note"].lower()


def test_one_bad_date_fails_the_whole_verdict():
    """Per-date, never pooled. A DST bug affects exactly one date, and a pooled
    rate would average it away."""
    rows = [(f"r{i}", "pre_open", "close_T_minus_1", 0.99) for i in range(14)]
    rows += [(f"r{i}", "pre_open", "close_T", 0.02) for i in range(14)]
    rows += [("bad", "pre_open", "close_T_minus_1", 0.40),
             ("bad", "pre_open", "close_T", 0.03)]
    v = aa.verdict(_detail(rows))
    assert v["state"] == "STOP_AMBIGUOUS", (
        "14 clean dates averaged away one catastrophic one")
    assert v["min_p_prior"] == pytest.approx(0.40)


def test_verdict_requires_separation_not_just_a_high_rate():
    """A tolerance wide enough to make both candidates match is worthless."""
    d = _detail([("r1", "pre_open", "close_T_minus_1", 0.99),
                 ("r1", "pre_open", "close_T", 0.97)])
    assert aa.verdict(d)["state"] == "STOP_AMBIGUOUS"


def test_verdict_on_no_data_is_not_a_pass():
    assert aa.verdict(pd.DataFrame()) ["state"] == "NO_DATA"


def test_decision_tolerance_is_tighter_than_the_reconciliation_one():
    """5 bps, not PLAN 2c's 50: at 50 bps a third of symbol-days match BOTH
    candidates and the test cannot separate them."""
    assert aa.DECISION_TOL == 0.0005
    assert aa.DECISION_TOL in aa.TOLERANCES
    assert min(aa.TOLERANCES) == aa.DECISION_TOL


# ------------------------------ the session grid ------------------------------

GRID = [date(2026, 3, 5), date(2026, 3, 6), date(2026, 3, 9), date(2026, 3, 10)]


def test_prev_session_skips_the_weekend():
    """`T-1` is the prior trading SESSION. A naive `date - 1 day` is wrong on
    every Monday, which presents as ~25% noise rather than as an error."""
    assert aa._prev_session(GRID, date(2026, 3, 9), 1) == date(2026, 3, 6)
    assert aa._prev_session(GRID, date(2026, 3, 9), 2) == date(2026, 3, 5)


def test_prev_session_returns_none_when_the_grid_runs_out():
    assert aa._prev_session(GRID, date(2026, 3, 5), 1) is None


def test_prev_session_is_strictly_before():
    assert aa._prev_session(GRID, date(2026, 3, 10), 1) == date(2026, 3, 9)


# -------------------------------- the interval --------------------------------

def test_wilson_brackets_the_point_estimate():
    lo, hi = aa.wilson(98, 100)
    assert lo < 0.98 < hi and 0 <= lo and hi <= 1


def test_wilson_stays_inside_zero_one_at_the_extremes():
    assert aa.wilson(0, 50)[0] == 0.0
    assert aa.wilson(50, 50)[1] == 1.0


def test_wilson_is_wider_on_less_data():
    narrow = aa.wilson(990, 1000)
    wide = aa.wilson(99, 100)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])
