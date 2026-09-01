"""Phase 2 tests.

The `test_contract_*` tests run today and enforce the interface. The four
`test_reconcile_*` tests are the acceptance criteria for Phase 2 (PLAN §2c) and
are skipped until a provider is implemented. **Un-skip them by implementing the
provider, never by deleting the skip.**
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from zoltar_ranks.config import Config
from zoltar_ranks.sources.prices import (
    ACTION_COLUMNS, DAILY_COLUMNS, INTRADAY_COLUMNS, PROVIDERS, Coverage,
    PriceProvider, get_provider, validate,
)


# --------------------------- interface contract ---------------------------

def test_coverage_math_pure():
    cov = Coverage(requested={"A", "B", "C"}, served={"A", "B"},
                   provider="alpaca", interval="1min")
    assert cov.missing == {"C"}
    assert cov.rate == pytest.approx(2 / 3)
    frame = cov.to_frame()
    assert set(frame.columns) == {"symbol", "served", "provider", "interval"}
    assert frame.served.sum() == 2


def test_validate_rejects_missing_columns_pure():
    with pytest.raises(ValueError, match="missing required columns"):
        validate(pd.DataFrame({"symbol": ["A"]}), DAILY_COLUMNS, "daily")


def test_validate_requires_explicit_adjusted_flag_pure():
    """Mixing adjusted and raw prices is how a split becomes a -50% 'return'."""
    df = pd.DataFrame({c: [None] for c in DAILY_COLUMNS})
    df["symbol"] = ["aapl"]
    df["trade_date"] = [date(2026, 1, 2)]
    df["adjusted"] = [None]
    with pytest.raises(ValueError, match="adjusted"):
        validate(df, DAILY_COLUMNS, "daily")


def test_validate_normalizes_symbols_and_tz_pure():
    df = pd.DataFrame({
        "ts": pd.to_datetime(["2026-09-01 09:30"]).tz_localize("UTC"),
        "symbol": [" aapl "], "interval": ["1min"],
        "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0],
        "volume": [10.0], "provider": ["x"],
    })
    out = validate(df, INTRADAY_COLUMNS, "intraday")
    assert out.symbol.iloc[0] == "AAPL"
    assert out.ts.dt.tz is None, "archive timestamps are tz-naive America/Chicago"


def test_all_providers_registered_and_abstract_pure():
    assert set(PROVIDERS) == {"robin_stocks", "alpaca", "yfinance"}
    for cls in PROVIDERS.values():
        assert issubclass(cls, PriceProvider)
        for method in ("fetch_daily", "fetch_intraday", "fetch_actions"):
            assert hasattr(cls, method)


def test_unknown_provider_raises_pure(tmp_path):
    with pytest.raises(KeyError):
        get_provider("bloomberg", tmp_path)


def test_config_price_provider_is_known_pure():
    cfg = Config.load()
    assert cfg.price_provider in PROVIDERS


# ------------------- Phase 2 acceptance criteria (PLAN §2c) -------------------

pytestmark_reason = "Phase 2: implement a PriceProvider, then remove this skip"


@pytest.mark.skip(reason=pytestmark_reason)
@pytest.mark.network
def test_reconcile_price_agreement():
    """`ranks.close_price` from a morning run must match the prior session's
    provider close within 0.5% for >=99% of symbols, after split adjustment.

    A failure here means either the provider is serving different prices than
    the ones the models were built on, or Close_Price is unadjusted (FINDINGS
    F7). Resolve which BEFORE computing any return.
    """
    raise NotImplementedError


@pytest.mark.skip(reason=pytestmark_reason)
@pytest.mark.network
def test_reconcile_intraday_coverage():
    """For each intraday run_ts, >=95% of ranked symbols have a bar within 5
    minutes. Below that, the timing study is measuring a biased subuniverse."""
    raise NotImplementedError


@pytest.mark.skip(reason=pytestmark_reason)
@pytest.mark.network
def test_reconcile_no_phantom_returns():
    """No adjusted 1-day return exceeds +/-60% without a matching row in
    corporate_actions."""
    raise NotImplementedError


@pytest.mark.skip(reason=pytestmark_reason)
@pytest.mark.network
def test_reconcile_trading_calendar():
    """No bars on market holidays; every trading day from 2025-10-01 to today
    is present."""
    raise NotImplementedError
