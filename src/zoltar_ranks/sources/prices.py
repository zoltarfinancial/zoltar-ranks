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
from datetime import date, timedelta
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


class ProviderUnavailable(RuntimeError):
    """The provider could not answer -- distinct from "there is nothing to report".

    Raised rather than returning a short frame. Silence and emptiness look the
    same to every downstream join, and for corporate actions the difference is a
    phantom -50% return.
    """


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



def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _unstack_yf(raw: pd.DataFrame, symbols: list[str]) -> list[pd.DataFrame]:
    """Flatten yfinance's output into DAILY_COLUMNS-shaped frames.

    yfinance returns flat columns for one ticker and a (ticker, field)
    MultiIndex for several, so both shapes are handled here rather than at every
    call site. Symbols it could not serve are simply absent -- callers that care
    about coverage must compare against what they requested.
    """
    if raw is None or raw.empty:
        return []
    frames = []
    multi = isinstance(raw.columns, pd.MultiIndex)
    for sym in symbols:
        try:
            sub = raw[sym] if multi else raw
        except KeyError:
            continue
        sub = sub.dropna(how="all")
        if sub.empty:
            continue
        idx = pd.to_datetime(sub.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert(MARKET_TZ).tz_localize(None)
        frames.append(pd.DataFrame({
            "trade_date": idx.date,
            "symbol": sym,
            "open": sub["Open"].to_numpy(),
            "high": sub["High"].to_numpy(),
            "low": sub["Low"].to_numpy(),
            "close": sub["Close"].to_numpy(),
            "volume": sub["Volume"].to_numpy(),
        }))
    return frames


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

    #: yfinance batches well but rate-limits hard above ~100 tickers per call.
    CHUNK = 100

    def fetch_daily(self, symbols, start, end):
        """Split/dividend-adjusted daily OHLCV. `adjusted` is True by construction.

        `auto_adjust=True` is not optional here: mixing adjusted and raw prices
        is exactly how the SFTBY halving in FINDINGS F7 becomes a phantom -50%
        return. The flag travels with every row so a later join cannot lose it.
        """
        import yfinance as yf

        wanted = sorted(set(symbols))
        out = []
        for chunk in _chunks(wanted, self.CHUNK):
            raw = yf.download(chunk, start=start, end=end + timedelta(days=1),
                              auto_adjust=True, actions=False, group_by="ticker",
                              progress=False, threads=True)
            out.extend(_unstack_yf(raw, chunk))

        # yfinance reports transport failures by returning an empty frame, so an
        # outage and a delisted ticker look identical. They are not: one means
        # "no data exists", the other means "we do not know". Serving zero rows
        # for every symbol is never a legitimate answer to a live request.
        if not out:
            raise ProviderUnavailable(
                f"yfinance returned no daily bars for any of {len(wanted)} symbols "
                f"({start}..{end}). Treating this as an outage, not as an empty "
                f"market: returning an empty frame here would silently shorten "
                f"every downstream return series.")
        df = pd.concat(out, ignore_index=True)
        served = set(df["symbol"].unique())
        if missing := sorted(set(wanted) - served):
            # Partial misses are legitimate (delistings), but must never be silent.
            log.warning("yfinance served %d/%d symbols for %s..%s; missing: %s",
                        len(served), len(wanted), start, end, missing[:20])
        df["adjusted"] = True
        df["provider"] = self.name
        return df

    def fetch_intraday(self, symbols, start, end, interval):
        raise NotImplementedError(
            "PLAN §2a/§2a-bis. yfinance serves 1-minute bars for roughly the last "
            "30 days only -- fine as a cross-check, not as the timing study's "
            "backbone. Blocked on the INTRADAY_COLUMNS `session` decision.")

    def fetch_actions(self, symbols, start, end):
        """Splits and dividends -- the table that makes returns trustworthy.

        Long form, one row per event: `kind='split'` carries `ratio` (new/old)
        and a null `amount`; `kind='dividend'` carries `amount` and a null
        `ratio`. That matches `corporate_actions` in schema.sql exactly.
        """
        import yfinance as yf

        rows, failed = [], []
        wanted = sorted(set(symbols))
        for sym in wanted:
            try:
                acts = yf.Ticker(sym).actions
            except Exception as exc:                      # noqa: BLE001 - provider is flaky
                # NEVER swallow this into an empty frame. "no corporate actions"
                # and "the provider could not answer" look identical downstream,
                # and the difference is a phantom -50% return (FINDINGS F7).
                log.warning("yfinance actions failed for %s: %s", sym, exc)
                failed.append(sym)
                continue
            if acts is None or acts.empty:
                continue
            idx = pd.to_datetime(acts.index)
            if getattr(idx, "tz", None) is not None:
                idx = idx.tz_convert(MARKET_TZ).tz_localize(None)
            for when, row in zip(idx, acts.itertuples(index=False)):
                d = when.date()
                if not (start <= d <= end):
                    continue
                split = float(getattr(row, "Stock_Splits", 0) or 0)
                div = float(getattr(row, "Dividends", 0) or 0)
                if split:
                    rows.append((d, sym, "split", split, None, self.name))
                if div:
                    rows.append((d, sym, "dividend", None, div, self.name))
        if failed:
            raise ProviderUnavailable(
                f"yfinance could not serve corporate actions for {len(failed)} of "
                f"{len(wanted)} symbols (e.g. {failed[:5]}). Refusing to return a "
                f"partial frame: an absent split is indistinguishable from no "
                f"split, and that is exactly how a split becomes a -50% return.")
        return pd.DataFrame(rows, columns=ACTION_COLUMNS)


PROVIDERS: dict[str, type[PriceProvider]] = {
    RobinStocksProvider.name: RobinStocksProvider,
    AlpacaProvider.name: AlpacaProvider,
    YFinanceProvider.name: YFinanceProvider,
}


def get_provider(name: str, cache_dir: Path) -> PriceProvider:
    if name not in PROVIDERS:
        raise KeyError(f"unknown price provider {name!r}; have {sorted(PROVIDERS)}")
    return PROVIDERS[name](cache_dir)
