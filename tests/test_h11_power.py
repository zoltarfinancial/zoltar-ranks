"""H11's power calculation (`c-0001.1`).

The two exclusions are the whole point of these tests. Both were found by
looking at the hold distribution rather than at the answer, and both would have
gone unnoticed in a number typed into the register:

* **27 of 138 runs were same-bar execution.** A forward-stamped evening retrain
  has `available_at = committed_at`, and its next observation is the same
  evening's other build pushed 13-15 seconds later in the same commit. Those
  pairs return exactly 0.000%, and 27 zero-variance observations shrink the sd,
  shrink the MDE, and make the study look better powered than it is. Rule 3.
* **Two runs marked 66 and 105 days later**, which is a quarter, not an
  overnight gap. They alone moved the sd from 0.32% to 0.80%.

They push in opposite directions, so neither filter alone gives the right answer.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from zoltar_ranks.analysis import h11_power as h11

BASE = dt.datetime(2026, 8, 3, 20, 0)


def _pairs(rows) -> pd.DataFrame:
    """rows: (run_offset_days, hold_hours, per_leg_return) -> a 5-leg basket."""
    out = []
    for i, (day, hold, ret) in enumerate(rows):
        avail = BASE + dt.timedelta(days=day)
        for leg in range(5):
            out.append({
                "run_ts": avail, "available_at": avail, "symbol": f"S{leg}",
                "rk": leg + 1, "entry_px": 100.0, "exit_px": 100.0 * (1 + ret),
                "exit_at": avail + dt.timedelta(hours=hold),
            })
    return pd.DataFrame(out)


def test_same_bar_pairs_are_excluded_rule_3():
    """A 13-second hold is the same bar. Rule 3: a zero-latency fill is a bug."""
    pairs = _pairs([(0, 13 / 3600, 0.0), (1, 12.3, 0.01), (2, 12.4, -0.01)])
    per = h11.per_run_returns(pairs, 5)
    assert list(per["excluded"]) == ["same_bar", "", ""]
    assert len(h11.usable(per)) == 2


def test_stale_marks_are_excluded():
    """A 66-day hold is a quarter, not H11's overnight gap."""
    pairs = _pairs([(0, 12.3, 0.01), (1, 66 * 24, -0.30), (2, 12.4, -0.01)])
    per = h11.per_run_returns(pairs, 5)
    assert list(per["excluded"]) == ["", "stale_mark", ""]


def test_the_two_filters_push_in_opposite_directions():
    """Neither alone gives the right sd, which is why both exist.

    Same-bar pairs are zero-variance and pull the sd DOWN; a stale mark is a
    huge return and pulls it UP. Dropping only one leaves a biased answer, and
    the same-bar bias is the flattering one.
    """
    rows = ([(i, 13 / 3600, 0.0) for i in range(10)]          # same-bar zeros
            + [(20 + i, 12.3, 0.003 * (-1) ** i) for i in range(10)]   # real gaps
            + [(50, 66 * 24, -0.30)])                          # stale mark
    per = h11.per_run_returns(_pairs(rows), 5)
    sd_all = per["ret"].std(ddof=1)
    sd_no_stale = per[per["excluded"] != "stale_mark"]["ret"].std(ddof=1)
    sd_clean = h11.usable(per)["ret"].std(ddof=1)
    assert sd_no_stale < sd_all, "the stale mark inflates the sd"
    assert sd_clean > sd_no_stale, (
        "dropping only the stale mark leaves the same-bar zeros, which deflate "
        "the sd -- the flattering direction")


def test_incomplete_baskets_are_dropped_not_averaged():
    """Averaging surviving legs changes the estimand to a survivorship statistic."""
    pairs = _pairs([(0, 12.3, 0.01), (1, 12.3, 0.01)])
    pairs = pairs.drop(pairs.index[0])          # one leg of run 0 has no exit
    per = h11.per_run_returns(pairs, 5)
    assert len(per) == 1, "a 4-leg basket must not be counted as a top-5 basket"


def test_mde_scales_as_one_over_sqrt_n():
    assert h11._mde(0.01, 100) == pytest.approx(h11._mde(0.01, 25) / 2)


def test_mde_carries_a_bootstrap_interval_that_brackets_it():
    """Rule 6: no bare point estimate."""
    rng = np.random.default_rng(0)
    x = rng.normal(0, 0.0035, 109)
    ci = np.quantile(h11._boot_block(x, 2000, h11.BLOCK_LEN, rng), [0.025, 0.975])
    assert ci[0] < h11._mde(float(x.std(ddof=1)), len(x)) < ci[1]
    assert ci[0] > 0


def test_block_bootstrap_falls_back_when_the_sample_is_short():
    rng = np.random.default_rng(0)
    out = h11._boot_block(rng.normal(0, 0.01, 3), 50, 5, rng)
    assert len(out) == 50 and np.all(np.isfinite(out))
