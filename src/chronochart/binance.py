"""Public Binance Spot historical klines; no keys, signatures, or trading endpoints.

Endpoint: https://api.binance.com/api/v3/klines, timeZone=0, milliseconds.
Select OPEN timestamps in [start,end): Binance's inclusive endTime is end-1ms.
Pagination advances by max returned open+1ms (including calendar-month intervals),
continues past short pages until empty/end, and fails on no progress/conflicting
duplicates. Interval closes are validated and converted to exclusive UTC bar_end.

Default forming policy excludes bars whose scheduled end exceeds explicit as_of;
include exposes is_closed=False, raise fails. as_of is NOT point-in-time replay:
the endpoint may supply later revisions/final prices for a past forming candle.
Full bars are returned, never clipped to request end. crosses_request_end flags
selection edges; downstream aggregation must reject bars it cannot safely fit.

Global Binance availability depends on jurisdiction and service policy. HTTP
403/451 stop immediately. No alternate domain, regional fallback, redirect,
proxy, TLS bypass, or credential mechanism is provided. Access via an authorized
independent provider where lawful; do not evade restrictions. Live use is opt-in.

Official contract: https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
Klines weight=2, maximum page=1000. Weight headers are telemetry, not a guarantee
against shared-IP limits. Pace pages; honor numeric Retry-After seconds on429,
or fail without retrying early if it exceeds a bound. HTTP418 always stops.
Retry only timeouts/connections and500/502/503/504 with bounded backoff. Malformed
responses/API validation errors are never retried. No partial history on failure.

Expected O(r + n log n) work for r received rows/n unique rows, with O(n + p)
memory for page size p, plus bounded HTTP attempts/waits. Conflicting duplicates
fail early; identical duplicates do not grow the retained history. Symbol syntax
is uppercase ASCII alphanumeric; actual listing validity is checked by the API.
"""

from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real
import re
import time
from typing import Callable

import pandas as pd
import requests

from chronochart.aggregation import OHLCV
from chronochart.normalization import normalize_ohlcv
from chronochart.providers import (
    HistoricalRequest, MarketDataResult, ProviderAccessError, ProviderError,
    ProviderHTTPError, ProviderRateLimitError, ProviderResponseError,
)


URL = "https://api.binance.com/api/v3/klines"
_MAX_TIME_MS = pd.Timestamp("2101-01-01", tz="UTC").value // 1_000_000
INTERVALS = (
    "1s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w", "1M",
)
_DURATIONS = {
    "1s": pd.Timedelta(seconds=1),
    **{f"{n}m": pd.Timedelta(minutes=n) for n in (1, 3, 5, 15, 30)},
    **{f"{n}h": pd.Timedelta(hours=n) for n in (1, 2, 4, 6, 8, 12)},
    "1d": pd.Timedelta(days=1), "3d": pd.Timedelta(days=3), "1w": pd.Timedelta(weeks=1),
}


@dataclass(frozen=True)
class BinanceOptions:
    """Finite per-operation limits; retry sleep budget applies across all pages.

    Timeout is (connect, read) seconds, not an overall wall-clock deadline. There
    are max_retries+1 attempts/page, max_pages pages, max_rows unique source rows.
    page_pause is independent successful-page pacing; retry waits share one budget.
    """

    page_size: int = 1000
    max_pages: int = 1000
    max_rows: int = 1_000_000
    max_retries: int = 3
    connect_timeout: float = 3.05
    read_timeout: float = 10.0
    backoff: float = 0.5
    max_backoff: float = 30.0
    retry_wait_budget: float = 60.0
    page_pause: float = 0.25

    def __post_init__(self) -> None:
        for name, maximum in (("page_size", 1000), ("max_pages", 10_000), ("max_rows", 1_000_000), ("max_retries", 5)):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Integral) or not (0 if name == "max_retries" else 1) <= value <= maximum:
                raise ValueError(f"Invalid bounded {name}")
        for name in ("connect_timeout", "read_timeout", "backoff", "max_backoff", "retry_wait_budget", "page_pause"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or not 0 <= value <= 120:
                raise ValueError(f"{name} must be finite and within0..120 seconds")
        if self.connect_timeout == 0 or self.read_timeout == 0 or self.backoff == 0 or self.max_backoff == 0:
            raise ValueError("Timeouts and backoff must be positive")
        if self.backoff > self.max_backoff:
            raise ValueError("backoff must not exceed max_backoff")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_TIME_MS:
        raise ProviderResponseError(f"Invalid Binance {name} milliseconds")
    return value


def _decode(row: object, interval: str) -> tuple[pd.Timestamp, pd.Timestamp, tuple[object, ...]]:
    if not isinstance(row, list) or len(row) != 12:
        raise ProviderResponseError("Binance kline must contain exactly12fields")
    opened = pd.Timestamp(_integer(row[0], "open time"), unit="ms", tz="UTC")
    closed = pd.Timestamp(_integer(row[6], "close time") + 1, unit="ms", tz="UTC")
    if interval == "1M":
        aligned = opened.day == 1 and opened == opened.normalize()
        expected = opened + pd.DateOffset(months=1)
    elif interval == "1w":
        aligned = opened.dayofweek == 0 and opened == opened.normalize()
        expected = opened + _DURATIONS[interval]
    elif interval == "3d":
        # Do not invent an undocumented absolute multi-day anchor.
        aligned = opened == opened.normalize()
        expected = opened + _DURATIONS[interval]
    else:
        duration = _DURATIONS[interval]
        aligned = opened.value % duration.value == 0
        expected = opened + duration
    if not aligned or closed != expected:
        raise ProviderResponseError("Binance interval/open/close semantics are inconsistent")
    if isinstance(row[8], bool) or not isinstance(row[8], int) or row[8] < 0:
        raise ProviderResponseError("Invalid Binance trade count")
    for value in (*row[1:6], row[7], row[9], row[10]):
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ProviderResponseError("Invalid Binance numeric field")
        try:
            number = float(value)
        except (ValueError, OverflowError) as exc:
            raise ProviderResponseError("Invalid Binance numeric field") from exc
        if not isfinite(number) or number < 0:
            raise ProviderResponseError("Binance numeric fields must be finite and nonnegative")
    values = tuple(float(value) for value in row[1:6])
    opened_price, high, low, closed_price, _ = values
    if high < max(opened_price, low, closed_price) or low > min(opened_price, high, closed_price):
        raise ProviderResponseError("Invalid Binance OHLC range")
    return opened, closed, values


class BinanceProvider:
    """Implements HistoricalDataProvider; inject transport/sleep only for tests.

    Owned sessions ignore environment proxy/netrc settings, never follow redirects,
    retain normal TLS verification, and can be used as context managers. Injected
    transports remain caller-owned. No market-data gap is synthesized or hidden.
    """

    def __init__(self, options: BinanceOptions | None = None, *, transport=None, sleep: Callable[[float], None] = time.sleep):
        self.options = BinanceOptions() if options is None else options
        if not isinstance(self.options, BinanceOptions) or not callable(sleep):
            raise ValueError("Provide BinanceOptions and a callable sleeper")
        self._owned = transport is None
        self._http = requests.Session() if self._owned else transport
        if self._owned:
            self._http.trust_env = False
        self._sleep = sleep

    def close(self) -> None:
        if self._owned:
            self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _wait(self, delay: float, budget: list[float], *, status: int) -> None:
        if delay > self.options.max_backoff or budget[0] + delay > self.options.retry_wait_budget:
            if status == 429:
                raise ProviderRateLimitError("Retry-After exceeds retry bounds; no early retry", status=status, retry_after=delay)
            raise ProviderError("Retry wait budget exceeded")
        self._sleep(delay)
        budget[0] += delay

    def _page(self, params: dict, budget: list[float], weights: dict) -> list:
        for attempt in range(self.options.max_retries + 1):
            try:
                response = self._http.get(
                    URL, params=dict(params),
                    timeout=(self.options.connect_timeout, self.options.read_timeout),
                    allow_redirects=False,
                )
            except requests.exceptions.SSLError as exc:
                raise ProviderError("Binance TLS verification failed; no bypass attempted") from exc
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == self.options.max_retries:
                    raise ProviderError("Binance transport retry limit exceeded") from exc
                self._wait(min(self.options.backoff * 2**attempt, self.options.max_backoff), budget, status=0)
                continue
            except requests.RequestException as exc:
                raise ProviderError("Non-retryable Binance request failure") from exc
            status = response.status_code
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            for key, value in headers.items():
                if key.startswith("x-mbx-used-weight") and len(value) <= 18 and value.isascii() and value.isdigit():
                    weights[key] = int(value)
            if status in (403, 451):
                raise ProviderAccessError(f"Binance HTTP{status}: unavailable/restricted; no regional bypass")
            if status in (418, 429):
                retry_after = None
                if "retry-after" in headers:
                    try:
                        retry_after = float(headers["retry-after"])
                    except ValueError:
                        pass
                    if retry_after is None or not isfinite(retry_after) or retry_after < 0:
                        raise ProviderRateLimitError("Invalid Retry-After; stop rather than retry early", status=status)
                if status == 418 or attempt == self.options.max_retries:
                    raise ProviderRateLimitError("Binance banned/throttled; stop at retry limit", status=status, retry_after=retry_after)
                delay = max(min(self.options.backoff * 2**attempt, self.options.max_backoff), retry_after or 0)
                # Never clip a server-mandated delay to a shorter wait.
                self._wait(delay, budget, status=status)
                continue
            if status in (500, 502, 503, 504):
                if attempt == self.options.max_retries:
                    raise ProviderHTTPError("Binance server retry limit exceeded", status=status)
                self._wait(min(self.options.backoff * 2**attempt, self.options.max_backoff), budget, status=status)
                continue
            if 300 <= status < 400:
                raise ProviderAccessError("Binance redirect refused; no endpoint switching")
            if status != 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                code = payload.get("code") if isinstance(payload, dict) else None
                code = code if isinstance(code, int) and not isinstance(code, bool) else None
                raise ProviderHTTPError(f"Binance HTTP/API error (status={status}, code={code})", status=status, code=code)
            try:
                payload = response.json()
            except (ValueError, requests.exceptions.JSONDecodeError) as exc:
                raise ProviderResponseError(f"Binance HTTP{status}: invalid JSON") from exc
            if isinstance(payload, dict):
                code = payload.get("code") if isinstance(payload, dict) else None
                code = code if isinstance(code, int) and not isinstance(code, bool) else None
                raise ProviderHTTPError(f"Binance HTTP/API error (status={status}, code={code})", status=status, code=code)
            if not isinstance(payload, list) or len(payload) > self.options.page_size:
                raise ProviderResponseError("Binance payload must be a bounded list of klines")
            return payload
        raise ProviderError("Binance retry loop exhausted")

    def fetch_history(self, request: HistoricalRequest) -> MarketDataResult:
        """Fetch/normalize full source bars; no credentials, aggregation, or gap fill."""
        if not isinstance(request, HistoricalRequest):
            raise ValueError("request must be a HistoricalRequest")
        if re.fullmatch(r"[A-Z0-9]{2,30}", request.symbol) is None:
            raise ValueError("Binance symbol must be2..30 uppercase ASCII letters/digits, e.g. BTCUSDT")
        if request.interval not in INTERVALS:
            raise ValueError(f"Unsupported case-sensitive Binance interval: {request.interval}")
        start_ms, end_ms = request.start.value // 1_000_000, request.end.value // 1_000_000
        cursor = start_ms
        budget, weights = [0.0], {}
        decoded: dict[pd.Timestamp, tuple] = {}
        duplicate_count = 0
        completed = False
        pages = 0
        for page_id in range(self.options.max_pages):
            if page_id and self.options.page_pause:
                self._sleep(self.options.page_pause)
            page = self._page({
                "symbol": request.symbol, "interval": request.interval,
                "startTime": cursor, "endTime": end_ms - 1,
                "timeZone": "0", "limit": self.options.page_size,
            }, budget, weights)
            pages += 1
            if not page:
                completed = True
                break
            current = [_decode(row, request.interval) for row in page]
            for opened, closed, values in current:
                if not request.start <= opened < request.end:
                    raise ProviderResponseError("Binance returned a bar outside requested open-time boundaries")
                if opened > request.as_of:
                    raise ProviderResponseError("Binance returned a future-unopened bar at as_of")
                if opened.value // 1_000_000 < cursor and opened not in decoded:
                    raise ProviderResponseError("Binance returned an unknown stale page row")
                if opened in decoded:
                    if decoded[opened] != (closed, values):
                        raise ProviderResponseError("Conflicting duplicate Binance candle")
                    duplicate_count += 1
                else:
                    decoded[opened] = (closed, values)
                if len(decoded) > self.options.max_rows:
                    raise ProviderError("Binance max_rows exceeded; no partial result returned")
            next_cursor = max(item[0].value // 1_000_000 for item in current) + 1
            if next_cursor <= cursor:
                raise ProviderResponseError("Binance pagination made no forward progress")
            if max(item[1] for item in current) >= request.end:
                completed = True
                break
            cursor = next_cursor
        if not completed:
            raise ProviderError("Binance max_pages exceeded; no partial result returned")
        index = pd.DatetimeIndex(list(decoded), tz="UTC", name="Timestamp")
        raw = pd.DataFrame([item[1] for item in decoded.values()], index=index, columns=OHLCV)
        try:
            candles = normalize_ohlcv(raw)
        except ValueError as exc:
            raise ProviderResponseError(str(exc)) from exc
        info = pd.DataFrame(index=candles.index)
        info["bar_end"] = pd.Series([decoded[opened][0] for opened in candles.index], index=candles.index, dtype="datetime64[ns, UTC]")
        info["is_closed"] = (info["bar_end"] <= request.as_of).astype("bool")
        info["crosses_request_end"] = (info["bar_end"] > request.end).astype("bool")
        forming_count = int((~info["is_closed"]).sum())
        if forming_count and request.forming == "raise":
            raise ProviderResponseError("Currently forming candle encountered under forming='raise'")
        if request.forming == "exclude":
            candles = candles.loc[info["is_closed"]].copy(deep=True)
            info = info.loc[info["is_closed"]].copy(deep=True)
        context = dict(
            provider="binance-spot-public", symbol=request.symbol, interval=request.interval,
            request_start=request.start, request_end=request.end, as_of=request.as_of,
            selection="open in [start,end)", close="exclusive", timezone="UTC", volume_unit="base_asset",
            forming_policy=request.forming, forming_encountered=forming_count,
            forming_excluded=forming_count if request.forming == "exclude" else 0,
            duplicates_removed=duplicate_count, pages=pages,
            used_weight=dict(weights), retry_wait_seconds=budget[0],
        )
        candles.attrs.update(context)
        info.attrs.update(context)
        return MarketDataResult(candles, info)
