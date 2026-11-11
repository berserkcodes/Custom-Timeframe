"""Fetch -> normalize -> cache raw source -> aggregate -> export, outside the CLI.

All run date ranges are [start,end). Equity1d accepts exchange-date strings;
provider bounds are UTC date labels and calendar boundaries are NY-local dates.
Intraday timestamps must be aware, or ISO dates interpreted in the selected market
timezone. Source resolution is declared, never inferred. Only supported market
calendar/timezone pairs are accepted, never silently ignored. Anchors may change
phase but cannot violate Monday/month-first/session-open rules.

Defaults: retain query-edge/unfinished windows, raise on missing closed bars,
retain valid shorter session windows, exclude forming source candles. Exports are
one atomic versioned JSON bundle with ALL completeness/source/provenance metadata.
Cache stores only raw normalized data; target changes reuse it without reusing
old aggregated results. Live providers are explicit; failures never use fixtures.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import time

import pandas as pd

from chronochart.aggregation import AggregationResult
from chronochart.cache import FileCache
from chronochart.local_providers import FixtureProvider, YFinanceProvider, _validate_symbol, provider_identity, source_resolution
from chronochart.normalization import validate_market_data
from chronochart.pipeline import aggregate_time_windows, aggregate_trading_sessions
from chronochart.providers import HistoricalRequest, MarketDataResult
from chronochart.storage import ExportBundle, export_bundle
from chronochart.timeframes import ANCHOR, TradingSessionTimeframe, build_trading_session_groups
from chronochart.window_timeframes import WindowTimeframe, build_window_groups


@dataclass(frozen=True)
class JobSpec:
    provider: str
    symbol: str
    start: str
    end: str
    as_of: str
    source_interval: str
    target_mode: str
    target_amount: int
    target_unit: str
    output: str
    calendar: str | None = None
    timezone: str | None = None
    anchor: str | None = None
    partial_policy: str = "retain"
    missing: str = "raise"
    forming: str = "exclude"
    short_session: str = "retain"
    cache_dir: str | None = None
    cache_ttl: float = 3600
    cache_refresh: bool = False
    cache_corrupt: str = "raise"
    overwrite: bool = False


@dataclass(frozen=True)
class ResolvedJob:
    spec: JobSpec
    market: str
    timezone: str
    calendar: str
    request: HistoricalRequest
    start: pd.Timestamp
    end: pd.Timestamp
    anchor: pd.Timestamp | None
    target: TradingSessionTimeframe | WindowTimeframe


def _date_only(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None


def _time(value: str, zone: str, *, dates=True) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value, tz=zone) if dates and _date_only(value) else pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid explicit date/timestamp") from exc
    if pd.isna(stamp) or stamp.tz is None:
        raise ValueError("Use ISO dates or timezone-aware timestamps; as_of must be aware")
    return stamp.tz_convert("UTC")


def resolve_job(spec: JobSpec) -> ResolvedJob:
    if spec.provider not in ("fixture-equity", "fixture-crypto", "yfinance", "binance"):
        raise ValueError("Unsupported run provider")
    market = "equity" if spec.provider in ("fixture-equity", "yfinance") else "crypto"
    zone, calendar = ("America/New_York", "XNYS") if market == "equity" else ("UTC", "24/7")
    if spec.timezone not in (None, zone) or spec.calendar not in (None, calendar):
        raise ValueError(f"{market} currently requires calendar={calendar}, timezone={zone}")
    _validate_symbol(spec.symbol, market)
    source = source_resolution(spec.source_interval)
    if spec.provider == "yfinance" and spec.source_interval != "1d":
        raise ValueError("Live yfinance adapter currently supports1d only; use fixtures for equity intraday tests")
    if type(spec.target_amount) is not int or spec.target_amount <= 0:
        raise ValueError("target_amount must be a positive integer")
    if spec.target_mode == "trading-sessions":
        if market != "equity" or spec.target_unit != "sessions" or spec.source_interval != "1d":
            raise ValueError("Trading-session targets require equity1d source and unit=sessions")
        target = TradingSessionTimeframe(spec.target_amount)
    else:
        modes = {("calendar", 3, "weeks"): "3 calendar weeks", ("calendar", 2, "months"): "2 calendar months",
                 ("intraday", 45, "minutes"): "45-minute intraday"}
        name = modes.get((spec.target_mode, spec.target_amount, spec.target_unit))
        if name is None:
            raise ValueError("Supported calendar/intraday targets:3weeks,2months,45minutes; name the mode explicitly")
        target = WindowTimeframe(name, market)
    start, end = _time(spec.start, zone), _time(spec.end, zone)
    as_of = _time(spec.as_of, "UTC", dates=False)
    if start >= end:
        raise ValueError("Run start must be before exclusive end")
    if source.kind == "daily" and market == "equity":
        if not _date_only(spec.start) or not _date_only(spec.end):
            raise ValueError("Equity daily ranges require YYYY-MM-DD date labels")
        request_start, request_end = pd.Timestamp(spec.start, tz="UTC"), pd.Timestamp(spec.end, tz="UTC")
    else:
        request_start, request_end = start, end
    request = HistoricalRequest(spec.symbol, spec.source_interval, request_start, request_end, as_of, spec.forming)
    if isinstance(target, TradingSessionTimeframe):
        anchor = _time(spec.anchor, "UTC") if spec.anchor is not None else ANCHOR
    elif market == "equity" and target.mode == "45-minute intraday":
        if spec.anchor not in (None, "session-open"):
            raise ValueError("Equity intraday requires --anchor session-open")
        anchor = None
    else:
        anchor = _time(spec.anchor, zone) if spec.anchor is not None else None
    if spec.partial_policy not in ("retain", "drop") or spec.missing not in ("raise", "retain", "drop") or spec.short_session not in ("retain", "drop"):
        raise ValueError("Invalid partial/missing/short-session policy")
    if not spec.cache_dir and (spec.cache_refresh or spec.cache_ttl != 3600 or spec.cache_corrupt != "raise"):
        raise ValueError("Cache options require --cache-dir")
    if spec.cache_dir is not None and (not isinstance(spec.cache_dir, str) or not spec.cache_dir.strip()):
        raise ValueError("Cache directory must be a nonempty path")
    FileCache(spec.cache_dir or ".", ttl=spec.cache_ttl, corrupt=spec.cache_corrupt)  # validation only; no IO
    if not isinstance(spec.output, str) or not spec.output.strip():
        raise ValueError("Output file is required")
    path = Path(spec.output)
    if path.suffix.lower() != ".json":
        raise ValueError("Run exports are versioned JSON bundles; output must end in .json")
    if path.is_dir() or (path.parent.exists() and not path.parent.is_dir()):
        raise ValueError("Output must be a file path under a directory")
    if path.exists() and not spec.overwrite:
        raise FileExistsError("Output exists; use --overwrite explicitly")
    job = ResolvedJob(spec, market, zone, calendar, request, start, end, anchor, target)
    # Validate dates, source detail, anchors, and full-grid resource bounds BEFORE
    # a live fetch or a cache hit. Empty source is deliberate, missing=retain.
    _plan_empty(job)
    return job


def _session_options(job: ResolvedJob) -> dict:
    return dict(start=job.request.start.date().isoformat(),
                end=(job.request.end - pd.Timedelta(days=1)).date().isoformat(),
                as_of=job.request.as_of, anchor=job.anchor)


def _plan_empty(job: ResolvedJob) -> None:
    empty = pd.DatetimeIndex([], tz="UTC")
    if isinstance(job.target, TradingSessionTimeframe):
        build_trading_session_groups(empty, job.target, **_session_options(job), missing="retain")
    else:
        build_window_groups(empty, job.target, source_resolution(job.spec.source_interval),
                            start=job.start, end=job.end, as_of=job.request.as_of, anchor=job.anchor, missing="retain")


def validate_source(data: MarketDataResult, request: HistoricalRequest) -> MarketDataResult:
    return validate_market_data(data, request)


def execute_job(spec: JobSpec, *, provider_override=None, clock=time.time) -> ExportBundle:
    job = resolve_job(spec)
    identity = provider_identity(spec.provider)
    if provider_override is not None:
        provider, identity = provider_override

    def fetch():
        owned = provider_override is None
        if not owned:
            adapter = provider
        elif spec.provider.startswith("fixture-"):
            adapter = FixtureProvider(job.market)
        elif spec.provider == "yfinance":
            adapter = YFinanceProvider()
        else:
            from chronochart.binance import BinanceProvider
            adapter = BinanceProvider()
        try:
            return validate_source(adapter.fetch_history(job.request), job.request)
        finally:
            if owned and hasattr(adapter, "close"):
                adapter.close()

    cache_context = {"status": "disabled"}
    if spec.cache_dir:
        cached = FileCache(spec.cache_dir, ttl=spec.cache_ttl, corrupt=spec.cache_corrupt, clock=clock).fetch(
            identity, job.request, fetch, refresh=spec.cache_refresh,
        )
        data = validate_source(cached.data, job.request)
        cache_context = dict(status=cached.status, key=cached.key, created_at=cached.created_at, expires_at=cached.expires_at)
    else:
        data = fetch()
    policies = dict(leading=spec.partial_policy, trailing=spec.partial_policy, unfinished=spec.partial_policy, missing=spec.missing)
    if isinstance(job.target, TradingSessionTimeframe):
        result = aggregate_trading_sessions(data.candles, job.target, **_session_options(job), **policies)
    else:
        result = aggregate_time_windows(
            data.candles, job.target, source_resolution(spec.source_interval),
            start=job.start, end=job.end, as_of=job.request.as_of, anchor=job.anchor,
            short_session=spec.short_session, **policies,
        )
    for frame in (result.candles, result.metadata):
        frame.index = frame.index.as_unit("ns").rename("Timestamp")
    bundle = ExportBundle(result, data.metadata, dict(
        job=asdict(spec), provider_identity=identity, source=dict(data.candles.attrs),
        market=job.market, calendar=job.calendar, timezone=job.timezone, request=asdict(job.request),
        cache=cache_context, synthetic=bool(data.candles.attrs.get("synthetic", False)),
    ))
    export_bundle(spec.output, bundle, overwrite=spec.overwrite)
    return bundle
