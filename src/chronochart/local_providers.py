"""Deterministic offline providers and an opt-in daily-equity yfinance adapter.

Fixture prices are SYNTHETIC, not AAPL/Binance historical quotes. Values depend on
symbol and timestamp, not query start. Daily equities use UTC exchange-date labels
with actual XNYS opens/closes; intraday labels are actual UTC left edges. yfinance
is daily-only, unadjusted (auto/back_adjust=False), single bounded-timeout library
call; it never silently switches to fixtures after failure. Library/network terms
and historical revisions still apply. No live checks are needed for offline jobs.
"""

from importlib.metadata import version
import re

import pandas as pd

from chronochart.aggregation import OHLCV
from chronochart.normalization import normalize_ohlcv
from chronochart.providers import HistoricalRequest, MarketDataResult, ProviderError, ProviderResponseError
from chronochart.timeframes import _xnys_schedule
from chronochart.window_timeframes import SourceResolution, WindowTimeframe, _slots


_MINUTES = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720}


def source_resolution(interval: str) -> SourceResolution:
    if interval == "1d":
        return SourceResolution.daily()
    if interval not in _MINUTES:
        raise ValueError("Run source interval must be1d or a supported whole-minute Binance interval")
    return SourceResolution.intraday(pd.Timedelta(minutes=_MINUTES[interval]))


def _validate_symbol(symbol: str, market: str) -> None:
    pattern = r"[A-Z0-9.^-]{1,32}" if market == "equity" else r"[A-Z0-9]{2,30}"
    if re.fullmatch(pattern, symbol) is None:
        raise ValueError("Invalid uppercase equity/crypto symbol")


def _result(candles, starts, ends, request, *, provider, synthetic=False) -> MarketDataResult:
    candles = normalize_ohlcv(candles)
    if any(not request.start <= label < request.end for label in candles.index):
        raise ProviderResponseError("Provider returned out-of-request source labels")
    if any(starts[label] > request.as_of for label in candles.index):
        raise ProviderResponseError("Provider returned a source bar not opened at as_of")
    info = pd.DataFrame(index=candles.index)
    info["bar_end"] = pd.Series([ends[label] for label in candles.index], index=candles.index, dtype="datetime64[ns, UTC]")
    info["is_closed"] = (info["bar_end"] <= request.as_of).astype("bool")
    info["crosses_request_end"] = (info["bar_end"] > request.end).astype("bool")
    # For daily equities request.end is a DATE LABEL, not actual session extent.
    if request.interval == "1d" and "equity" in provider:
        info["crosses_request_end"] = False
    forming = int((~info["is_closed"]).sum())
    if forming and request.forming == "raise":
        raise ProviderResponseError("Forming source candle encountered")
    if request.forming == "exclude":
        candles, info = candles.loc[info["is_closed"]].copy(), info.loc[info["is_closed"]].copy()
    context = dict(provider=provider, synthetic=synthetic, symbol=request.symbol, interval=request.interval,
                   request_start=request.start, request_end=request.end, as_of=request.as_of,
                   forming_policy=request.forming, forming_excluded=forming if request.forming == "exclude" else 0,
                   price_basis="synthetic" if synthetic else "unadjusted", schema="OHLCV-v1",
                   volume_unit="shares" if "equity" in provider else "base_asset")
    candles.attrs.update(context)
    info.attrs.update(context)
    return MarketDataResult(candles, info)


class FixtureProvider:
    """Offline, deterministic, actual-calendar fixture provider; no network code."""

    def __init__(self, market: str):
        if market not in ("equity", "crypto"):
            raise ValueError("Invalid fixture market")
        self.market = market

    def fetch_history(self, request: HistoricalRequest) -> MarketDataResult:
        _validate_symbol(request.symbol, self.market)
        resolution = source_resolution(request.interval)
        slots = _slots(request.start, request.end, WindowTimeframe("45-minute intraday", self.market), resolution)
        slots = [slot for slot in slots if request.start <= slot.label < request.end and slot.start <= request.as_of]
        salt = sum((i + 1) * ord(char) for i, char in enumerate(request.symbol))
        rows = []
        for slot in slots:
            ordinal = slot.label.value // 60_000_000_000
            price = 100 + ((ordinal + salt) % 10000) / 100
            rows.append([price, price + 2, price - 2, price + 0.5, 1000 + ordinal % 1000])
        frame = pd.DataFrame(rows, index=pd.DatetimeIndex([slot.label for slot in slots], tz="UTC"), columns=OHLCV)
        return _result(frame, {slot.label: slot.start for slot in slots}, {slot.label: slot.end for slot in slots},
                       request, provider=f"fixture-{self.market}-v1", synthetic=True)


class YFinanceProvider:
    """Daily-only live equity prototype; explicit unadjusted price policy."""

    def __init__(self, *, ticker_factory=None, timeout: float = 10):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ValueError("yfinance timeout must be finite/positive and <=120 seconds")
        self.ticker_factory, self.timeout = ticker_factory, timeout

    def fetch_history(self, request: HistoricalRequest) -> MarketDataResult:
        _validate_symbol(request.symbol, "equity")
        if request.interval != "1d" or request.start != request.start.normalize() or request.end != request.end.normalize():
            raise ValueError("yfinance run adapter supports1d with UTC-midnight date-label bounds only")
        factory = self.ticker_factory
        if factory is None:
            import yfinance as yf
            factory = yf.Ticker
        try:
            raw = factory(request.symbol).history(
                start=request.start.date().isoformat(), end=request.end.date().isoformat(), interval="1d",
                auto_adjust=False, back_adjust=False, actions=False, repair=False, keepna=True,
                prepost=False, timeout=self.timeout, raise_errors=True,
            )
        except Exception as exc:
            raise ProviderError("yfinance daily fetch failed; no fixture/stale fallback") from exc
        if not isinstance(raw, pd.DataFrame):
            raise ProviderResponseError("yfinance returned a non-frame response")
        if len(raw) and (not raw.columns.is_unique or not set(OHLCV).issubset(raw.columns)):
            raise ProviderResponseError("yfinance returned nonempty data without unique OHLCV columns")
        if raw.empty:
            frame = pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
        else:
            if not isinstance(raw.index, pd.DatetimeIndex) or raw.index.tz is None:
                raise ProviderResponseError("yfinance daily dates must be timezone-aware")
            local = raw.index.tz_convert("America/New_York")
            if not local.equals(local.normalize()):
                raise ProviderResponseError("yfinance daily dates must be New York midnights")
            frame = raw.copy(deep=True)
            frame.index = local.tz_localize(None).tz_localize("UTC")
        schedule = _xnys_schedule()
        if not frame.index.isin(schedule.index).all():
            raise ProviderResponseError("yfinance returned a non-XNYS session date")
        try:
            return _result(frame, schedule["open"].to_dict(), schedule["close"].to_dict(), request, provider="yfinance-equity-daily")
        except ValueError as exc:
            raise ProviderResponseError(str(exc)) from exc


def provider_identity(name: str) -> dict:
    identity = {"name": name, "normalization": "OHLCV-v1-ns", "adapter_version": 1,
                "price_basis": "synthetic" if name.startswith("fixture-") else "unadjusted"}
    if name in ("fixture-equity", "yfinance"):
        identity.update(calendar="XNYS", calendar_version=version("exchange-calendars"))
    if name == "yfinance":
        identity["library_version"] = version("yfinance")
    elif name == "binance":
        identity.update(endpoint="https://api.binance.com/api/v3/klines", timezone="UTC")
    elif name not in ("fixture-equity", "fixture-crypto"):
        raise ValueError("Unsupported provider")
    return identity
