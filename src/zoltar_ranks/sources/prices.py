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
from dataclasses import dataclass, field
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
    #: Request windows the provider answered with NOTHING. Each is recorded as
    #: ProviderUnavailable with its window -- never read as "there was no data".
    #: Optional with a default, so the column contract is unchanged.
    unavailable: list = field(default_factory=list)

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
        # `returns_adjusted` is part of the key, not decoration. It is settable
        # per instance (the alignment anchor needs RAW prices; returns need
        # adjusted ones), and without it a raw request is served the adjusted
        # bars a previous call cached under the same provider name -- silently,
        # and in exactly the direction that hides the answer to FINDINGS F7.
        key = hashlib.sha1(
            f"{self.name}|adj={self.returns_adjusted}|{kind}|{interval}|{start}"
            f"|{end}|{','.join(sorted(symbols))}".encode()).hexdigest()[:16]
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


def _unstack_yf_intraday(raw, tickers, back, interval, provider) -> list[pd.DataFrame]:
    """yfinance intraday output -> INTRADAY_COLUMNS frames, one per symbol.

    Handles both the flat (single ticker) and (ticker, field) MultiIndex shapes.
    The index is tz-aware (UTC, measured) and is converted to tz-naive
    America/Chicago -- the archive's wall clock -- right here.
    """
    if raw is None or getattr(raw, "empty", True):
        return []
    frames = []
    multi = isinstance(raw.columns, pd.MultiIndex)
    for tk in tickers:
        try:
            sub = raw[tk] if multi else raw
        except KeyError:
            continue
        sub = sub.dropna(how="all")
        if sub.empty or "Close" not in sub:
            continue
        idx = pd.to_datetime(sub.index)
        if getattr(idx, "tz", None) is None:
            # A naive index would be an unknown clock. Refuse rather than guess --
            # guessing the source tz is the exact error the anchor measures.
            raise ValueError(f"yfinance returned a tz-naive intraday index for {tk}")
        idx = idx.tz_convert(MARKET_TZ).tz_localize(None)
        frames.append(pd.DataFrame({
            "ts": idx, "symbol": back.get(tk, tk), "interval": interval,
            "open": sub["Open"].to_numpy(), "high": sub["High"].to_numpy(),
            "low": sub["Low"].to_numpy(), "close": sub["Close"].to_numpy(),
            "volume": sub["Volume"].to_numpy(), "provider": provider,
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

    def __init__(self, cache_dir: Path, adjusted: bool = True):
        """`adjusted=False` serves RAW prices, for the alignment anchor.

        Adjusted history is restated backwards for every dividend, so a 2026-03
        close pulled today differs from what the model saw by the sum of
        subsequent dividends. Comparing `ranks.close_price` against that would
        fail for reasons having nothing to do with the clock, and would make
        "unadjusted" and "misaligned" indistinguishable. The `adjusted` flag
        already travels with every row; this just lets it be false.
        """
        super().__init__(cache_dir)
        self.returns_adjusted = adjusted

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
                              auto_adjust=self.returns_adjusted, actions=False,
                              group_by="ticker", progress=False, threads=True)
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
        df["adjusted"] = self.returns_adjusted
        df["provider"] = self.name
        return df

    #: Yahoo refuses more than 8 calendar days of 1-minute data per request.
    #: MEASURED 2026-09-10: an exact 8-day window is served; a 9-day window comes
    #: back EMPTY -- not an error, an empty frame -- which is precisely the
    #: silence-that-reads-as-absence this project audits for. So the chunk size is
    #: load-bearing, not a tuning knob.
    MAX_INTRADAY_DAYS = {"1min": 8, "5min": 60, "1hour": 730}
    YF_INTERVAL = {"1min": "1m", "5min": "5m", "1hour": "60m"}

    def fetch_intraday(self, symbols, start, end, interval):
        """RAW intraday OHLCV, chunked by `MAX_INTRADAY_DAYS`, `ts` = bar OPEN.

        `start`..`end` are inclusive calendar dates. Every (window, symbol-batch)
        request that returns an empty frame for ALL of its symbols is recorded in
        `Coverage.unavailable` with its window -- a ProviderUnavailable record,
        never an inferred absence. A batch that served some symbols and not
        others records the misses in `Coverage.missing`. Only if EVERY request
        comes back empty does this raise.

        Bars are RAW (auto_adjust=False), regular session only (prepost=False).
        The alignment anchor compares them against `ranks.close_price`, which
        FINDINGS F8 measured to be unadjusted. Yahoo returns this index in UTC;
        it is converted to tz-naive America/Chicago here and nowhere else.

        Symbols with a '.' (BRK.B, BF.B) are requested with '-', which is
        Yahoo's spelling, and mapped back -- the daily anchor lost both to this.
        """
        import yfinance as yf

        if interval not in self.YF_INTERVAL:
            raise ValueError(f"unsupported interval {interval!r}")
        yfi, span = self.YF_INTERVAL[interval], self.MAX_INTRADAY_DAYS[interval]
        wanted = sorted(set(symbols))
        yf_of = {s: s.replace(".", "-") for s in wanted}
        back = {v: k for k, v in yf_of.items()}

        frames, unavailable, windows = [], [], []
        w0 = start
        while w0 <= end:
            w1 = min(w0 + timedelta(days=span), end + timedelta(days=1))  # exclusive
            windows.append((w0, w1))
            w0 = w1

        for w0, w1 in windows:
            for chunk in _chunks(wanted, self.CHUNK):
                tick = [yf_of[s] for s in chunk]
                err = None
                try:
                    raw = yf.download(tick, start=w0, end=w1, interval=yfi,
                                      auto_adjust=False, prepost=False,
                                      group_by="ticker", progress=False, threads=True)
                except Exception as exc:                      # noqa: BLE001
                    raw, err = None, f"{type(exc).__name__}: {exc}"
                got = _unstack_yf_intraday(raw, tick, back, interval, self.name)
                if not got:
                    unavailable.append({
                        "start": str(w0), "end_exclusive": str(w1),
                        "symbols": len(chunk), "first": chunk[0], "last": chunk[-1],
                        "reason": err or ("provider returned an empty 1-minute "
                                          "frame for every symbol in this request"),
                    })
                    continue
                frames.extend(got)

        if not frames:
            raise ProviderUnavailable(
                f"yfinance returned no {interval} bars for any of {len(wanted)} "
                f"symbols in any of {len(windows)} window(s) {start}..{end}. "
                f"Treating this as an outage, not as an empty market.")
        bars = pd.concat(frames, ignore_index=True)
        cov = Coverage(requested=set(wanted), served=set(bars["symbol"].unique()),
                       provider=self.name, interval=interval,
                       unavailable=unavailable)
        return bars, cov

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
