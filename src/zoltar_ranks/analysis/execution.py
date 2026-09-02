"""Execution primitives and the two rules that make a fill honest.

`c-0001.3`. Written **before** the backtest engine, and deliberately: a gate
written after the result it should have guarded tends to get written to pass.
The way to avoid a gate that passes vacuously is not to wait for the engine — it
is to write the violating engine first and prove the gate catches it.
`tests/test_execution_gates.py` does exactly that, in the same test run.

Two rules, and they are checked against **evidence carried on the fill**, never
against a flag the engine sets about itself:

* **Rule 3 — no same-bar execution.** `fill_at` must be at least
  `execution_latency_minutes` after `decision_at`. A latency of 0 is a bug, not
  a default.
* **Rule 5 — decisions key off `available_at`, never `run_ts`.** A `Fill` carries
  both of the signal's timestamps, so an engine that keyed off `run_ts` is caught
  by arithmetic rather than by self-report. On a forward-stamped evening retrain
  the two differ by ~24 h, which is precisely when it matters.

The latency floor lives in `Config`, in one place, for a measured reason. While
recomputing H11 (`c-0001.1`) 27 of 138 evening runs turned out to pair a
forward-stamped retrain with the **same evening's other build, pushed 13-15
seconds later in the same commit** — a different `run_ts`, a different file, and
a return of exactly 0.000%. It satisfies rule 5 and the *letter* of rule 3
(strictly after), so both guardrails pass while the fill is same-bar in every way
that matters. And it biases in the flattering direction: zero-variance pairs
shrink dispersion and make every study look better powered than it is. A floor
of 15 minutes catches it; a floor of "greater than zero" does not.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["Signal", "Fill", "SameBarViolation", "RunTsViolation",
           "check_no_same_bar", "check_no_run_ts_execution", "latency_floor"]


@dataclass(frozen=True)
class Signal:
    """A rank the strategy acted on."""
    symbol: str
    run_ts: datetime            # descriptive metadata; NEVER a decision input
    available_at: datetime      # the information timestamp (rule 5)


@dataclass(frozen=True)
class Fill:
    """A simulated fill, carrying enough evidence to audit itself.

    `decision_at` is the moment the engine claims it decided, and both of the
    signal's timestamps travel with it so the claim can be checked rather than
    believed.
    """
    symbol: str
    decision_at: datetime
    fill_at: datetime
    price: float
    signal_run_ts: datetime
    signal_available_at: datetime

    @property
    def latency(self) -> timedelta:
        return self.fill_at - self.decision_at


@dataclass(frozen=True)
class SameBarViolation:
    fill: Fill
    latency: timedelta
    required: timedelta

    def __str__(self) -> str:
        return (f"{self.fill.symbol} filled {self.latency} after the decision "
                f"({self.fill.decision_at} -> {self.fill.fill_at}); rule 3 "
                f"requires at least {self.required}")


@dataclass(frozen=True)
class RunTsViolation:
    fill: Fill

    def __str__(self) -> str:
        return (f"{self.fill.symbol} decided at {self.fill.decision_at}, which is "
                f"the signal's run_ts ({self.fill.signal_run_ts}), not its "
                f"available_at ({self.fill.signal_available_at}); rule 5")


def latency_floor(cfg) -> timedelta:
    """Rule 3's configured latency. Zero is rejected here, not downstream."""
    minutes = float(getattr(cfg, "execution_latency_minutes", 0) or 0)
    if minutes <= 0:
        raise ValueError(
            "execution_latency_minutes must be > 0. Rule 3: a latency of 0 is a "
            "bug, not a default -- it fills at the price the rank was computed "
            "from, which is the single largest source of fake edge (H9).")
    return timedelta(minutes=minutes)


def check_no_same_bar(fills, required: timedelta) -> list[SameBarViolation]:
    """Every fill must be at least `required` after the decision that caused it.

    Note the comparison is `<`, not `<=` against zero. "Strictly after" is not
    enough: the 13-second twin that started this passes any positive-latency
    test while being the same bar.
    """
    return [SameBarViolation(f, f.latency, required)
            for f in fills if f.latency < required]


def check_no_run_ts_execution(fills) -> list[RunTsViolation]:
    """No decision may key off `run_ts` (rule 5).

    Detected by arithmetic, not by self-report: where the signal's two timestamps
    differ, a `decision_at` equal to `run_ts` is a decision made on a timestamp
    the strategy could not have known. Where they agree the check is silent,
    because there is nothing to distinguish -- which is why a forward-stamped
    run is the case that carries the information.
    """
    out = []
    for f in fills:
        if f.signal_run_ts == f.signal_available_at:
            continue
        if f.decision_at != f.signal_available_at:
            out.append(RunTsViolation(f))
    return out
