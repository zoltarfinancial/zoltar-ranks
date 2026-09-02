"""The two blocker gates, proven against engines that deliberately violate them.

`c-0001.3`. A gate that passes because the code under test does not exist is a
green light meaning nothing — so rather than wait for `analysis/backtest.py`,
this file ships the violating engines first and demonstrates, **in the same test
run**, that each gate FAILS against the violator and PASSES against a correct
one. A gate proven to catch the violation before the real engine exists cannot
quietly be written to pass afterwards.

The violating engines are not textbook. They model the two failures this archive
actually produces:

* `SameBarEngine` fills at the decision price — FINDINGS F5's `update_strategy`,
  which buys at the `Close_Price` the rank was computed from.
* `TwinCommitEngine` fills 13 seconds later at an identical price. This is the
  real one: measured on H11, 27 of 138 evening runs pair a forward-stamped
  retrain with the same evening's other build pushed 13-15 seconds later in the
  same commit. Different `run_ts`, different file, return exactly 0.000%. It
  satisfies rule 5 and the letter of rule 3, so a naive "strictly after" gate
  waves it through.
* `RunTsEngine` decides on `run_ts`. On a forward-stamped evening retrain that
  timestamp is ~24 h in the future, so it is a decision made on information the
  strategy could not have had.
"""
from __future__ import annotations

import datetime as dt

import pytest

from zoltar_ranks.analysis.execution import (
    Fill, Signal, check_no_run_ts_execution, check_no_same_bar, latency_floor)
from zoltar_ranks.config import Config

#: A forward-stamped evening retrain: published 2026-09-01 19:36, stamped for the
#: next evening. run_ts and available_at differ by ~24 h, which is the only
#: configuration in which rule 5 can be tested at all.
FORWARD = Signal(symbol="AAPL",
                 run_ts=dt.datetime(2026, 9, 2, 19, 34, 49),
                 available_at=dt.datetime(2026, 9, 1, 19, 36, 25))

#: An ordinary intraday run, where the two agree. Rule 5 is silent here, and it
#: is included so the gate is shown not to fire on honest rows.
HONEST = Signal(symbol="MSFT",
                run_ts=dt.datetime(2026, 9, 2, 10, 52, 7),
                available_at=dt.datetime(2026, 9, 2, 10, 52, 7))

SIGNALS = [FORWARD, HONEST]


# ------------------------------ the engines ------------------------------

def same_bar_engine(signals):
    """FINDINGS F5: buys at the Close_Price the rank was computed from."""
    return [Fill(s.symbol, s.available_at, s.available_at, 100.0,
                 s.run_ts, s.available_at) for s in signals]


def twin_commit_engine(signals):
    """The real-world violator: the same build's twin, 13 seconds later."""
    return [Fill(s.symbol, s.available_at, s.available_at + dt.timedelta(seconds=13),
                 100.0, s.run_ts, s.available_at) for s in signals]


def run_ts_engine(signals, latency):
    """Decides on run_ts. Correct latency, wrong clock."""
    return [Fill(s.symbol, s.run_ts, s.run_ts + latency, 100.0,
                 s.run_ts, s.available_at) for s in signals]


def correct_engine(signals, latency):
    """Decides on available_at, fills a full configured latency later."""
    return [Fill(s.symbol, s.available_at, s.available_at + latency, 100.0,
                 s.run_ts, s.available_at) for s in signals]


@pytest.fixture()
def latency():
    return latency_floor(Config.load())


# --------------------------- gate: no_same_bar ---------------------------

def test_no_same_bar_execution(latency):
    """Rule 3. FAILS against both violators, PASSES against the correct engine."""
    assert check_no_same_bar(same_bar_engine(SIGNALS), latency), (
        "the gate did not catch a fill at the decision price -- that is H9's "
        "entire hypothesis and FINDINGS F5's first execution bias")

    twin = check_no_same_bar(twin_commit_engine(SIGNALS), latency)
    assert twin, (
        "the gate did not catch the 13-second twin commit. This is the one that "
        "actually happens: 27 of 138 evening runs in the archive. It is strictly "
        "after the decision, so a positive-latency test waves it through.")
    assert all(v.latency < v.required for v in twin)

    assert not check_no_same_bar(correct_engine(SIGNALS, latency), latency), (
        "the gate fired on a compliant engine; a gate that cannot pass is not a "
        "gate, it is an outage")


def test_no_same_bar_execution_rejects_a_zero_latency_config():
    """Rule 3: a latency of 0 is a bug, not a default."""
    cfg = Config.load()
    cfg.execution_latency_minutes = 0
    with pytest.raises(ValueError, match="must be > 0"):
        latency_floor(cfg)


def test_no_same_bar_execution_boundary_is_inclusive(latency):
    """Exactly the configured latency is compliant; one second less is not."""
    ok = correct_engine(SIGNALS, latency)
    assert not check_no_same_bar(ok, latency)
    short = [Fill(f.symbol, f.decision_at, f.fill_at - dt.timedelta(seconds=1),
                  f.price, f.signal_run_ts, f.signal_available_at) for f in ok]
    assert check_no_same_bar(short, latency)


# ----------------------- gate: no_run_ts_execution -----------------------

def test_no_run_ts_execution(latency):
    """Rule 5. Detected by arithmetic, never by an engine's self-report."""
    bad = check_no_run_ts_execution(run_ts_engine(SIGNALS, latency))
    assert bad, (
        "the gate did not catch an engine deciding on run_ts. On this signal "
        "run_ts is ~24h AFTER available_at, so the strategy acted on a timestamp "
        "it could not have known.")
    assert {v.fill.symbol for v in bad} == {"AAPL"}, (
        "only the forward-stamped signal is distinguishable; firing on the "
        "honest one would mean the check is not comparing the two timestamps")

    assert not check_no_run_ts_execution(correct_engine(SIGNALS, latency)), (
        "the gate fired on an engine that decided on available_at")


def test_no_run_ts_execution_is_silent_when_the_stamps_agree(latency):
    """Where run_ts == available_at there is nothing to distinguish.

    Pinned so nobody later 'strengthens' the gate into firing on every row and
    then relaxes it back out again when it becomes noise.
    """
    assert not check_no_run_ts_execution(run_ts_engine([HONEST], latency))


def test_no_run_ts_execution_catches_it_even_with_a_generous_latency():
    """The two gates are independent: a long latency does not launder the clock."""
    bad = check_no_run_ts_execution(run_ts_engine(SIGNALS, dt.timedelta(days=2)))
    assert bad, "rule 5 must not be satisfiable by waiting longer"


# --------------------------- both, on one engine ---------------------------

def test_a_compliant_engine_passes_both_gates(latency):
    fills = correct_engine(SIGNALS, latency)
    assert not check_no_same_bar(fills, latency)
    assert not check_no_run_ts_execution(fills)


def test_the_twin_commit_passes_rule_5_while_violating_rule_3(latency):
    """Why both gates are needed, and why neither is redundant.

    The twin-commit fill keys off available_at correctly -- rule 5 is satisfied --
    and is strictly after the decision. Only the latency floor catches it.
    """
    fills = twin_commit_engine(SIGNALS)
    assert not check_no_run_ts_execution(fills), "rule 5 is genuinely satisfied here"
    assert check_no_same_bar(fills, latency), "only rule 3's floor catches it"
