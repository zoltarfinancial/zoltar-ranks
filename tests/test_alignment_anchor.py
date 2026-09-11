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


# =============================================================================
# The INTRADAY anchor (order c-0002.1, docs/ALIGNMENT_ANCHOR.md section 4).
#
# The measurement is only worth reporting if the method recovers an answer that
# was PLANTED. So the synthetic tests build a random-walk tape, write a rank
# whose close_price is the tape at available_at + k minutes, and require the
# method to find k -- and to call a planted one-hour shift a timezone failure.
# =============================================================================

import json as _json
import sys as _sys
import types as _types
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
RESULT = REPO / "data" / "results" / "alignment_anchor_intraday.json"


def _tape(dates, n_symbols=80, seed=7):
    """1-minute bars, 08:30-14:59 CT, random walk ~4 bps per minute."""
    rng = np.random.default_rng(seed)
    rows = []
    for d in dates:
        minutes = pd.date_range(f"{d} 08:30", f"{d} 14:59", freq="1min")
        for s in range(n_symbols):
            px = 50 * np.exp(np.cumsum(rng.normal(0, 0.0004, len(minutes))))
            rows.append(pd.DataFrame({"ts": minutes, "symbol": f"S{s:03d}", "close": px}))
    return pd.concat(rows, ignore_index=True)


def _runs(dates):
    return [pd.Timestamp(f"{d} {hh:02d}:{mm:02d}:17")
            for d in dates for hh, mm in ((9, 5), (10, 5), (11, 5), (12, 5), (13, 5), (14, 5))]


def _pairs(bars, runs, shift_min):
    """close_price = the close of the bar open at (available_at + shift)."""
    out = []
    idx = bars.set_index(["symbol", "ts"])["close"]
    for t in runs:
        target = (t + pd.Timedelta(minutes=shift_min)).floor("1min")
        if (bars["symbol"].iloc[0], target) not in idx.index:
            continue            # planted target outside the tape: no such price exists
        for s in bars["symbol"].unique():
            out.append({"run_ts": t, "available_at": t, "symbol": s,
                        "close_price": float(idx[(s, target)])})
    return pd.DataFrame(out)


DATES = ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24"]
OFFSETS = sorted(set(aa.FINE_GRID) | set(aa.TZ_CELLS))   # not GRID: that is the session grid above


def _measure(shift):
    bars = _tape(DATES)
    runs = _runs(DATES)
    dev = aa.deviation_table(_pairs(bars, runs, shift), bars, OFFSETS)
    pr = aa.per_run_offsets(dev)
    pr["date"] = pd.to_datetime(pr["run_ts"]).dt.date
    return pr, dev


def test_intraday_method_recovers_a_planted_zero_offset():
    pr, dev = _measure(0)
    p = pr[pr["powered"]]
    assert len(p) >= 0.8 * len(pr), "a trending tape must give the guard power"
    assert np.median(p["offset_min"]) == 0
    ci = aa.cluster_bootstrap_median(p, b=2000)
    assert aa.intraday_verdict(pr, dev, ci)["state"] == "PASS"


def test_intraday_method_recovers_a_planted_lag():
    """close_price taken 4 minutes BEFORE available_at must read as -4, and
    must NOT be called a pass -- more than one bar is a finding to report."""
    pr, dev = _measure(-4)
    p = pr[pr["powered"]]
    assert abs(np.median(p["offset_min"]) - (-4)) <= 1
    ci = aa.cluster_bootstrap_median(p, b=2000)
    v = aa.intraday_verdict(pr, dev, ci)
    assert v["state"] == "LAG_MEASURED", v
    assert "NOT APPLIED" in v["note"]


def test_a_one_hour_clock_error_is_a_timezone_stop_not_a_lag():
    """The failure this whole anchor exists for: an ET-vs-CT hour."""
    pr, dev = _measure(60)
    ci = aa.cluster_bootstrap_median(pr[pr["powered"]], b=2000) if pr["powered"].any() \
        else (float("nan"), float("nan"))
    v = aa.intraday_verdict(pr, dev, ci)
    assert v["state"] in ("STOP_TIMEZONE", "NO_DATA"), v
    assert v["state"] != "PASS"


def test_a_flat_tape_has_no_power_and_cannot_pass():
    """Spec 4.4: on a quiet session h=0 'wins' meaninglessly. It must be
    dropped as no-power, never counted as a pass."""
    bars = _tape(DATES)
    bars["close"] = 50.0
    runs = _runs(DATES)
    dev = aa.deviation_table(_pairs(bars, runs, 0), bars, OFFSETS)
    pr = aa.per_run_offsets(dev)
    pr["date"] = pd.to_datetime(pr["run_ts"]).dt.date
    assert not pr["powered"].any()
    v = aa.intraday_verdict(pr, dev, (float("nan"), float("nan")))
    assert v["state"] == "NO_DATA"


def test_fewer_than_three_powered_dates_is_no_data():
    pr, dev = _measure(0)
    keep = sorted(pr["date"].unique())[:2]
    pr2 = pr[pr["date"].isin(keep)]
    assert aa.intraday_verdict(pr2, dev, (0.0, 0.0))["state"] == "NO_DATA"


def test_stale_bars_beyond_tolerance_are_not_used():
    """A thin name's bar 30 minutes old is an old price, not the price now."""
    bars = pd.DataFrame({"ts": [pd.Timestamp("2026-08-19 10:00")], "symbol": ["A"],
                         "close": [10.0]})
    pairs = pd.DataFrame({"run_ts": [pd.Timestamp("2026-08-19 10:30")],
                          "available_at": [pd.Timestamp("2026-08-19 10:30")],
                          "symbol": ["A"], "close_price": [10.0]})
    dev = aa.deviation_table(pairs, bars, [0])
    assert dev.empty or dev["n"].sum() == 0


def test_mixed_timestamp_resolutions_join():
    """DuckDB gives datetime64[us]; parquet bars come back datetime64[ms].
    The first real run died on exactly this; the synthetic tape was all ns."""
    bars = pd.DataFrame({"ts": pd.to_datetime(["2026-08-19 10:00"]).astype("datetime64[ms]"),
                         "symbol": ["A"], "close": [10.0]})
    pairs = pd.DataFrame({"run_ts": [pd.Timestamp("2026-08-19 10:00:30")],
                          "available_at": pd.to_datetime(["2026-08-19 10:00:30"]).astype("datetime64[us]"),
                          "symbol": ["A"], "close_price": [10.0]})
    pairs = pd.concat([pairs] * aa.MIN_SYMBOLS_PER_CELL, ignore_index=True)
    dev = aa.deviation_table(pairs, bars, [0])
    assert float(dev["mard"].iloc[0]) == 0.0


def test_session_window_excludes_post_close_runs():
    assert aa._in_session(pd.Timestamp("2026-08-19 10:00"))
    assert not aa._in_session(pd.Timestamp("2026-08-19 15:15"))   # PRECLOSE, after 15:00
    assert not aa._in_session(pd.Timestamp("2026-08-19 08:35"))   # 5 min after the open


def test_cluster_bootstrap_resamples_dates_not_runs():
    pr = pd.DataFrame({"date": ["a"] * 50 + ["b"] * 1, "offset_min": [0] * 50 + [9]})
    lo, hi = aa.cluster_bootstrap_median(pr, b=2000)
    # resampling DATES gives a real chance of drawing only date b; resampling
    # runs would bury it under date a's 50 rows
    assert hi == 9.0 and lo == 0.0


# ---------------- the provider: 8-day chunks, ProviderUnavailable ----------------

def _fake_yf(monkeypatch, responder):
    calls = []
    mod = _types.ModuleType("yfinance")

    def download(tickers, start, end, **kw):
        calls.append((list(tickers), start, end, kw))
        return responder(tickers, start, end)
    mod.download = download
    monkeypatch.setitem(_sys.modules, "yfinance", mod)
    return calls


def _frame(tickers, start):
    idx = pd.date_range(pd.Timestamp(start) + pd.Timedelta(hours=14, minutes=30),
                        periods=3, freq="1min", tz="UTC")
    cols = pd.MultiIndex.from_product([list(tickers),
                                       ["Open", "High", "Low", "Close", "Volume"]])
    return pd.DataFrame(1.0, index=idx, columns=cols)


def test_every_1min_request_spans_at_most_8_days(monkeypatch, tmp_path):
    from zoltar_ranks.sources.prices import YFinanceProvider
    calls = _fake_yf(monkeypatch, lambda t, s, e: _frame(t, s))
    from datetime import date
    YFinanceProvider(tmp_path, adjusted=False).fetch_intraday(
        ["AAA"], date(2026, 8, 19), date(2026, 9, 10), "1min")
    spans = [(e - s).days for _, s, e, _ in calls]
    assert spans and max(spans) <= 8, spans
    assert calls[0][1] == date(2026, 8, 19) and calls[-1][2] == date(2026, 9, 11)
    for (_, _, e0, _), (_, s1, _, _) in zip(calls, calls[1:]):
        assert e0 == s1, "windows must be contiguous -- a gap is silently lost data"
    assert all(kw["auto_adjust"] is False and kw["prepost"] is False for *_, kw in calls)


def test_an_empty_window_is_recorded_as_provider_unavailable(monkeypatch, tmp_path):
    from datetime import date
    from zoltar_ranks.sources.prices import YFinanceProvider

    def responder(t, s, e):
        return pd.DataFrame() if s == date(2026, 8, 27) else _frame(t, s)
    _fake_yf(monkeypatch, responder)
    bars, cov = YFinanceProvider(tmp_path, adjusted=False).fetch_intraday(
        ["AAA"], date(2026, 8, 19), date(2026, 9, 10), "1min")
    assert len(cov.unavailable) == 1
    u = cov.unavailable[0]
    assert u["start"] == "2026-08-27" and u["end_exclusive"] == "2026-09-04"
    assert "empty" in u["reason"], "an empty frame is ProviderUnavailable, never 'no data'"


def test_all_windows_empty_raises_rather_than_returning_nothing(monkeypatch, tmp_path):
    from datetime import date
    from zoltar_ranks.sources.prices import ProviderUnavailable, YFinanceProvider
    _fake_yf(monkeypatch, lambda t, s, e: pd.DataFrame())
    with pytest.raises(ProviderUnavailable):
        YFinanceProvider(tmp_path, adjusted=False).fetch_intraday(
            ["AAA"], date(2026, 8, 19), date(2026, 8, 20), "1min")


def test_dotted_symbols_are_requested_with_a_dash_and_mapped_back(monkeypatch, tmp_path):
    from datetime import date
    from zoltar_ranks.sources.prices import YFinanceProvider
    calls = _fake_yf(monkeypatch, lambda t, s, e: _frame(t, s))
    bars, _ = YFinanceProvider(tmp_path, adjusted=False).fetch_intraday(
        ["BRK.B"], date(2026, 8, 19), date(2026, 8, 20), "1min")
    assert calls[0][0] == ["BRK-B"]
    assert set(bars["symbol"]) == {"BRK.B"}


def test_intraday_timestamps_are_converted_to_chicago_wall_clock(monkeypatch, tmp_path):
    """Yahoo returns UTC (measured). 14:30 UTC in August is 09:30 CT."""
    from datetime import date
    from zoltar_ranks.sources.prices import YFinanceProvider
    _fake_yf(monkeypatch, lambda t, s, e: _frame(t, s))
    bars, _ = YFinanceProvider(tmp_path, adjusted=False).fetch_intraday(
        ["AAA"], date(2026, 8, 19), date(2026, 8, 19), "1min")
    assert bars["ts"].dt.tz is None
    assert bars["ts"].min() == pd.Timestamp("2026-08-19 09:30")


# ---------------------------- acceptance: the result ----------------------------

def test_intraday_anchor_result_exists_with_offset_and_interval():
    """Order c-0002.1 acceptance: a measured offset AND a confidence interval."""
    assert RESULT.exists(), f"{RESULT} missing -- run `python -m zoltar_ranks.analysis.alignment_anchor --intraday`"
    d = _json.loads(RESULT.read_text(encoding="utf-8"))
    o = d["offset_minutes"]
    assert o["estimate"] is not None and np.isfinite(o["estimate"])
    lo, hi = o["ci95"]
    assert np.isfinite(lo) and np.isfinite(hi) and lo <= o["estimate"] <= hi
    assert d["verdict"]["state"] in ("PASS", "LAG_MEASURED", "STOP_TIMEZONE",
                                     "STOP_AMBIGUOUS", "NO_DATA")
    assert d["sample"]["anchor_time"].startswith("available_at")
    assert d["coverage"]["symbols_with_bars"] > 0
    assert "provider_unavailable" in d["coverage"], "unavailable windows must be recorded, even if none"
