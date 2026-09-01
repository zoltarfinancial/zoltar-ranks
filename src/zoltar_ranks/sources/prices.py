"""Phase 2: market data. THE INTERFACE IS BUILT; THE PROVIDERS ARE NOT.

This file exists so the three providers cannot drift apart. Implement the three
`fetch_*` methods on each provider; do not change the returned column contract,
because the reconciliation tests and every downstream join depend on it.

Design notes that are not obvious:

* **`adjusted` is explicit, never inferred.** FINDINGS F7 shows at least one
  upstream symbol whose Close_Price halved between snapshots. Mixing adjusted
  and raw prices silently produces phantom -50% returns, so the flag travels
  with every row and `prices_daily`'s primary key includes `provider`.
* **Coverage is recorded, not assumed.** Alpaca has gaps for some Robinhood
  tickers. A provider that returns a short frame must also report which symbols
  it could not serve, otherwise the intraday study quietly becomes a
  survivorship study. `fetch_intraday` therefore returns `(bars, coverage)`.
* **Everything is cached to disk.** Re-running an analysis must not re-hit the
  provider. The cache key includes the provider, so switching providers cannot
  serve you the other one's bars.
* **Timestamps are tz-naive America/Chicago wall clock**, matching the archive.
  Convert at the provider boundary, here, and nowhere else.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# --- column contracts. Do not change without updating tests/test_prices.py ---

DAILY_COLUMNS = ["trade_date", "symbol", "open", "high", "low", "close",
                 "volume", "adjusted", "provider"]
INTRADAY_COLUMNS = ["ts", "symbol", "interval", "open", "high", "low", "close",
                    "volume", "provider"]
ACTION_COLUMNS = ["ex_date", "symbol", "kind", "ratio", "amount", "provider"]

VALID_INTERVALS = {"1min", "5min", "1hour"}
MARKET_TZ = "America/Chicago"   # the archive's wall clock; NOT US/Eastern


@dataclass
class Coverage:
    """What a provider could and could not serve. Never discard this."""
    requested: set[str]
    served: set[str]
    provider: str
    interval: str

    @property
    def missing(self) -> set[str]:
        return self.requested - self.served

    @property
    def rate(self) -> float:
        return len(self.served) / len(self.requested) if self.requested else 1.0

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "symbol": sorted(self.requested),
            "served": [s in self.served for s in sorted(self.requested)],
            "provider": self.provider,
            "interval": self.interval,
        })


class PriceProvider(ABC):
    """Implement one subclass per data source. Return the exact columns above."""

    name: str = "base"
    #: True if this source returns split/dividend-adjusted prices.
    returns_adjusted: bool = False

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir) / self.name
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- implement these three ----

    @abstractmethod
    def fetch_daily(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """Daily OHLCV. Must return DAILY_COLUMNS."""

    @abstractmethod
    def fetch_intraday(self, symbols: list[str], start: date, end: date,
                       interval: str) -> tuple[pd.DataFrame, Coverage]:
        """Intraday bars, `ts` = bar OPEN time. Must return INTRADAY_COLUMNS."""

    @abstractmethod
    def fetch_actions(self, symbols: list[str], start: date, end: date) -> pd.DataFrame:
        """Splits and dividends. Must return ACTION_COLUMNS."""

    # ---- provided for you; do not override ----

    def _cache_path(self, kind: str, symbols: list[str], start: date,
                    end: date, interval: str = "") -> Path:
        key = hashlib.sha1(
            f"{self.name}|{kind}|{interval}|{start}|{end}|{','.join(sorted(symbols))}"
            .encode()).hexdigest()[:16]
        return self.cache_dir / f"{kind}_{start}_{end}_{interval or 'd'}_{key}.parquet"

    def daily(self, symbols: list[str], start: date, end: date,
              refresh: bool = False) -> pd.DataFrame:
        path = self._cache_path("daily", symbols, start, end)
        if path.exists() and not refresh:
            return pd.read_parquet(path)
        df = validate(self.fetch_daily(symbols, start, end), DAILY_COLUMNS, "daily")
        df.to_parquet(path, index=False)
        return df

    def intraday(self, symbols: list[str], start: date, end: date, interval: str,
                 refresh: bool = False) -> tuple[pd.DataFrame, Coverage]:
        if interval not in VALID_INTERVALS:
            raise ValueError(f"interval must be one of {sorted(VALID_INTERVALS)}")
        path = self._cache_path("intraday", symbols, start, end, interval)
        cov_path = path.with_suffix(".coverage.parquet")
        if path.exists() and cov_path.exists() and not refresh:
            cov_df = pd.read_parquet(cov_path)
            cov = Coverage(requested=set(cov_df.symbol),
                           served=set(cov_df.loc[cov_df.served, "symbol"]),
                           provider=self.name, interval=interval)
            return pd.read_parquet(path), cov
        bars, cov = self.fetch_intraday(symbols, start, end, interval)
        bars = validate(bars, INTRADAY_COLUMNS, "intraday")
        bars.to_parquet(path, index=False)
        cov.to_frame().to_parquet(cov_path, index=False)
        if cov.rate < 0.95:
            log.warning("%s served only %.1f%% of symbols at %s; missing: %s",
                        self.name, 100 * cov.rate, interval,
                        sorted(cov.missing)[:20])
        return bars, cov

    def actions(self, symbols: list[str], start: date, end: date,
                refresh: bool = False) -> pd.DataFrame:
        path = self._cache_path("actions", symbols, start, end)
        if path.exists() and not refresh:
            return pd.read_parquet(path)
        df = validate(self.fetch_actions(symbols, start, end), ACTION_COLUMNS, "actions")
        df.to_parquet(path, index=False)
        return df


def validate(df: pd.DataFrame, columns: list[str], kind: str) -> pd.DataFrame:
    """Fail loudly at the provider boundary rather than deep in a join."""
    missing = set(columns) - set(df.columns)
    if missing:
        raise ValueError(f"{kind} frame missing required columns: {sorted(missing)}")
    out = df[columns].copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    if kind == "intraday":
        ts = pd.to_datetime(out["ts"])
        if getattr(ts.dt, "tz", None) is not None:
            ts = ts.dt.tz_convert(MARKET_TZ).dt.tz_localize(None)
        out["ts"] = ts
    if kind == "daily":
        out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
        if out["adjusted"].isna().any():
            raise ValueError("`adjusted` must be explicitly True or False on every row")
    return out


# --------------------------------------------------------------------------
# Providers. Each raises NotImplementedError until built -- see docs/PLAN.md §2a.
# Order of work: RobinStocks first (it is the source the ranks are built from,
# so it is the one that can reconcile against ranks.close_price), then Alpaca
# for minute bars, then yfinance as an independent cross-check.
# --------------------------------------------------------------------------

class RobinStocksProvider(PriceProvider):
    name = "robin_stocks"
    returns_adjusted = False   # VERIFY THIS before trusting any return series

    def fetch_daily(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a. Use robin_stocks.stocks.get_stock_historicals.")

    def fetch_intraday(self, symbols, start, end, interval):
        raise NotImplementedError("PLAN §2a. Note Robinhood's limited intraday lookback.")

    def fetch_actions(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a. Robinhood exposes splits sparsely; "
                                  "cross-check against yfinance and record disagreements.")


class AlpacaProvider(PriceProvider):
    name = "alpaca"
    returns_adjusted = True

    def fetch_daily(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a")

    def fetch_intraday(self, symbols, start, end, interval):
        raise NotImplementedError(
            "PLAN §2a. Alpaca has gaps for some Robinhood-listed tickers -- the "
            "Coverage object is mandatory, not optional.")

    def fetch_actions(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a")


class YFinanceProvider(PriceProvider):
    name = "yfinance"
    returns_adjusted = True

    def fetch_daily(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a. auto_adjust=True; set adjusted=True.")

    def fetch_intraday(self, symbols, start, end, interval):
        raise NotImplementedError(
            "PLAN §2a. yfinance serves 1-minute bars for roughly the last 30 days "
            "only -- fine as a cross-check, not as the timing study's backbone.")

    def fetch_actions(self, symbols, start, end):
        raise NotImplementedError("PLAN §2a. Ticker.splits and Ticker.dividends.")


PROVIDERS: dict[str, type[PriceProvider]] = {
    RobinStocksProvider.name: RobinStocksProvider,
    AlpacaProvider.name: AlpacaProvider,
    YFinanceProvider.name: YFinanceProvider,
}


def get_provider(name: str, cache_dir: Path) -> PriceProvider:
    if name not in PROVIDERS:
        raise KeyError(f"unknown price provider {name!r}; have {sorted(PROVIDERS)}")
    return PROVIDERS[name](cache_dir)
