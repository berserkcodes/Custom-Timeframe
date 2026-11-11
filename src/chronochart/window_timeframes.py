"""Explicit calendar/45-minute windows, independent of provider and reduction.

All target/source intervals are [start, end); output labels are target starts in
UTC. Equity calendar boundaries use America/New_York; crypto uses UTC. Calendar
weeks start Monday, anchored 2000-01-03; two-month periods start Jan/Mar/May/Jul/
Sep/Nov, anchored 2000-01-01. Equity intraday windows restart at each XNYS open;
crypto intraday/source grids use 2000-01-01T00:00Z on a 24/7 calendar.

No bar is split or reconstructed. Unsupported resolution, straddling, off-grid,
duplicate, future-unopened, and out-of-request source bars are rejected. Request
bounds must also not cut a source bar. Missing closed requested bars raise unless
retain/drop is explicitly chosen. All policies default to retain except missing.
Short final equity-session windows are valid shorter windows, not coverage gaps;
short_session='drop' omits them while preserving metadata.

Calendar-window maturity is its exclusive calendar end, even if the last equity
session closed earlier. Intraday maturity is the window/session close. Input
daily equity labels denote session dates at UTC midnight, not midnight instants;
their actual coverage is exchange open..close. Crypto daily bars cover UTC days.

Generation scans expected calendar slots, never observed row positions, so gaps
and overlapping queries do not re-anchor boundaries. Inputs are never mutated.
Whole-minute source resolutions only; requests cover 2000-01-03..2100-01-01 UTC
with full calendar windows no earlier than 2000-01-01 local. Requests are capped
at 20,000 targets and 200,000 expected source slots and fail without partial output
if those bounds are exceeded. Use smaller overlapping requests for larger ranges.

With E expected slots, W windows, and n observed rows, grouping scans cost expected
O(E + W + n) time/space plus exchange schedule lookup/construction (cached O(S)
storage for S scheduled sessions). Core sorting is separate: O(n log n), followed
by O(n + W) reduction. Expected grids include absent bars and full edge windows.
"""

from dataclasses import dataclass
from importlib.metadata import version
from typing import Literal

import pandas as pd

from chronochart.aggregation import CandleGroup
from chronochart.timeframes import EdgePolicy, MissingPolicy, _xnys_schedule


MODES = ("3 calendar weeks", "2 calendar months", "45-minute intraday")
MAX_SOURCE_SLOTS = 200_000
MAX_TARGET_WINDOWS = 20_000
UTC_ANCHOR = pd.Timestamp("2000-01-01", tz="UTC")


@dataclass(frozen=True)
class WindowTimeframe:
    """Named modes only; never aliases for trading-session count timeframes."""

    mode: Literal["3 calendar weeks", "2 calendar months", "45-minute intraday"]
    market: Literal["equity", "crypto"]

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be explicitly named: {', '.join(MODES)}")
        if self.market not in ("equity", "crypto"):
            raise ValueError("market must be 'equity' (XNYS) or 'crypto' (UTC 24/7)")

    @property
    def timezone(self) -> str:
        return "America/New_York" if self.market == "equity" else "UTC"


@dataclass(frozen=True)
class SourceResolution:
    """Declared source semantics, not inferred from observed timestamp gaps.

    Daily equity bars use exchange-date labels; intraday bars use actual UTC left
    edges, with fixed whole-minute intervals from each session open (equity) or
    UTC_ANCHOR (crypto). The final equity source interval is clipped to close.
    """

    kind: Literal["daily", "intraday"]
    interval: pd.Timedelta | None = None

    def __post_init__(self) -> None:
        if self.kind == "daily" and self.interval is None:
            return
        if self.kind != "intraday" or not isinstance(self.interval, pd.Timedelta):
            raise ValueError("Use SourceResolution.daily() or SourceResolution.intraday('1min')")
        if (
            pd.isna(self.interval) or not pd.Timedelta(minutes=1) <= self.interval < pd.Timedelta(days=1)
            or self.interval % pd.Timedelta(minutes=1) != pd.Timedelta(0)
        ):
            raise ValueError("Intraday source interval must be whole minutes between 1 and 1439")

    @classmethod
    def daily(cls) -> "SourceResolution":
        return cls("daily")

    @classmethod
    def intraday(cls, interval: str | pd.Timedelta) -> "SourceResolution":
        if not isinstance(interval, (str, pd.Timedelta)):
            raise ValueError("Source interval must be a duration string or pandas Timedelta")
        try:
            return cls("intraday", pd.Timedelta(interval))
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid whole-minute source interval") from exc


@dataclass(frozen=True)
class WindowGroupPlan:
    """Exact source partition plus full-window metadata, including dropped windows."""

    groups: tuple[CandleGroup, ...]
    metadata: pd.DataFrame


class MissingBarsError(ValueError):
    """Closed requested source-grid slots are absent, not holidays or pending bars."""

    def __init__(self, bars: tuple[pd.Timestamp, ...]) -> None:
        self.bars = bars
        super().__init__(f"Missing {len(bars)} closed requested source bar(s): {bars[:3]}")


@dataclass(frozen=True)
class _Window:
    start: pd.Timestamp
    end: pd.Timestamp
    short: bool = False


@dataclass(frozen=True)
class _Slot:
    label: pd.Timestamp
    start: pd.Timestamp
    end: pd.Timestamp


def _instant(value: pd.Timestamp, name: str) -> pd.Timestamp:
    if not isinstance(value, pd.Timestamp) or pd.isna(value) or value.tz is None:
        raise ValueError(f"{name} must be an explicit timezone-aware pandas Timestamp")
    return value.tz_convert("UTC")


def _calendar_floor(instant: pd.Timestamp, timeframe: WindowTimeframe) -> pd.Timestamp:
    local = instant.tz_convert(timeframe.timezone)
    date = local.tz_localize(None).normalize()
    if timeframe.mode == "3 calendar weeks":
        anchor = pd.Timestamp("2000-01-03")  # Monday, local wall-calendar time
        days = (date - anchor).days
        floor = anchor + pd.Timedelta(days=(days // 21) * 21)
    else:
        month = ((date.year - 2000) * 12 + date.month - 1) // 2 * 2
        floor = pd.Timestamp(year=2000 + month // 12, month=month % 12 + 1, day=1)
    return floor.tz_localize(timeframe.timezone).tz_convert("UTC")


def _next_calendar(start: pd.Timestamp, timeframe: WindowTimeframe) -> pd.Timestamp:
    local = start.tz_convert(timeframe.timezone).tz_localize(None)
    offset = pd.DateOffset(weeks=3) if timeframe.mode == "3 calendar weeks" else pd.DateOffset(months=2)
    return (local + offset).tz_localize(timeframe.timezone).tz_convert("UTC")


def _equity_sessions(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    schedule = _xnys_schedule()
    # Date lookup is independent of observed bars and includes local session dates.
    first = start.tz_convert("America/New_York").normalize().tz_localize(None).tz_localize("UTC")
    last = end.tz_convert("America/New_York").normalize().tz_localize(None).tz_localize("UTC")
    dates = schedule.loc[first:last]
    return dates.loc[(dates["close"] > start) & (dates["open"] < end)]


def _windows(start: pd.Timestamp, end: pd.Timestamp, timeframe: WindowTimeframe) -> list[_Window]:
    windows: list[_Window] = []

    def add(window: _Window) -> None:
        if len(windows) >= MAX_TARGET_WINDOWS:
            raise ValueError("Request exceeds 20,000 target windows; use smaller requests")
        windows.append(window)

    if timeframe.mode != "45-minute intraday":
        left = _calendar_floor(start, timeframe)
        while left < end:
            right = _next_calendar(left, timeframe)
            add(_Window(left, right))
            left = right
    elif timeframe.market == "crypto":
        duration = pd.Timedelta(minutes=45)
        left = UTC_ANCHOR + ((start - UTC_ANCHOR) // duration) * duration
        while left < end:
            add(_Window(left, left + duration))
            left += duration
    else:
        duration = pd.Timedelta(minutes=45)
        for _, session in _equity_sessions(start, end).iterrows():
            left, close = session["open"], session["close"]
            while left < close:
                right = min(left + duration, close)
                if right > start and left < end:
                    add(_Window(left, right, right - left < duration))
                left = right
    if windows and (
        windows[0].start.tz_convert(timeframe.timezone).date() < pd.Timestamp("2000-01-01").date()
        or windows[-1].end > pd.Timestamp("2101-01-01", tz="UTC")
    ):
        raise ValueError("Full target window lies outside the supported calendar horizon")
    return windows


def _slots(start: pd.Timestamp, end: pd.Timestamp, timeframe: WindowTimeframe, source: SourceResolution) -> list[_Slot]:
    slots: list[_Slot] = []

    def add(label: pd.Timestamp, left: pd.Timestamp, right: pd.Timestamp) -> None:
        if len(slots) >= MAX_SOURCE_SLOTS:
            raise ValueError("Request exceeds 200,000 expected source slots; use smaller requests")
        slots.append(_Slot(label, left, right))

    if timeframe.market == "equity":
        for date, session in _equity_sessions(start, end).iterrows():
            left, close = session["open"], session["close"]
            if source.kind == "daily":
                add(date, left, close)
            else:
                while left < close:
                    right = min(left + source.interval, close)
                    add(left, left, right)
                    left = right
    else:
        duration = pd.Timedelta(days=1) if source.kind == "daily" else source.interval
        left = UTC_ANCHOR + ((start - UTC_ANCHOR) // duration) * duration
        while left < end:
            add(left, left, left + duration)
            left += duration
    return slots


def _frame(records: list[dict], labels: list[pd.Timestamp], context: dict) -> pd.DataFrame:
    dtypes = {
        "window_start": "datetime64[ns, UTC]", "window_end": "datetime64[ns, UTC]",
        "source_count": "int64", "expected_count": "int64", "requested_count": "int64",
        "closed_requested_count": "int64", "missing_bars": "object", "pending_bars": "object",
        "leading_partial": "bool", "trailing_partial": "bool", "short_window": "bool",
        "window_closed": "bool", "coverage_complete": "bool", "count_complete": "bool",
        "is_complete": "bool", "retained": "bool",
    }
    result = pd.DataFrame(
        records, index=pd.DatetimeIndex(labels, tz="UTC", name="Timestamp"), columns=list(dtypes),
    ).astype(dtypes)
    result.attrs.update(context)
    return result


def build_window_groups(
    source_index: pd.DatetimeIndex,
    timeframe: WindowTimeframe,
    source: SourceResolution,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
    as_of: pd.Timestamp,
    leading: EdgePolicy = "retain",
    trailing: EdgePolicy = "retain",
    unfinished: EdgePolicy = "retain",
    missing: MissingPolicy = "raise",
    short_session: EdgePolicy = "retain",
) -> WindowGroupPlan:
    """Build fixed-boundary windows and their explicit expected source-grid slots.

    Source labels are UTC; start/end are aware instants, end exclusive. All full
    source slots must fit one target, even when absent from the input. Finer data
    must be supplied by the caller to resolve straddling; no daily-to-intraday
    reconstruction or proportional splitting of OHLCV is ever performed.
    """
    if not isinstance(timeframe, WindowTimeframe) or not isinstance(source, SourceResolution):
        raise ValueError("Provide WindowTimeframe and SourceResolution objects")
    for name, policy in (("leading", leading), ("trailing", trailing), ("unfinished", unfinished), ("short_session", short_session)):
        if policy not in ("retain", "drop"):
            raise ValueError(f"{name} must be 'retain' or 'drop'")
    if missing not in ("raise", "retain", "drop"):
        raise ValueError("missing must be 'raise', 'retain', or 'drop'")
    start, end, as_of = _instant(start, "start"), _instant(end, "end"), _instant(as_of, "as_of")
    if not pd.Timestamp("2000-01-03", tz="UTC") <= start < end <= pd.Timestamp("2100-01-01", tz="UTC"):
        raise ValueError("Request must satisfy 2000-01-03 <= start < end <= 2100-01-01 UTC")
    if not isinstance(source_index, pd.DatetimeIndex) or str(source_index.tz) != "UTC":
        raise ValueError("source_index must be a UTC DatetimeIndex")
    if source_index.hasnans or not source_index.is_unique:
        raise ValueError("Source labels must be unique and non-NaT")
    if source.kind == "daily" and not source_index.equals(source_index.normalize()):
        raise ValueError("Daily source bars require UTC-midnight date labels")
    if timeframe.mode == "45-minute intraday" and (
        source.kind == "daily" or source.interval > pd.Timedelta(minutes=45)
    ):
        raise ValueError("Intraday target requires sufficiently fine intraday source data, not daily bars")

    context = {
        "mode": timeframe.mode, "market": timeframe.market, "timezone": timeframe.timezone,
        "exchange": "XNYS" if timeframe.market == "equity" else "UTC-24/7",
        "calendar_version": version("exchange-calendars") if timeframe.market == "equity" else None,
        "week_anchor": f"2000-01-03 00:00 {timeframe.timezone} (Monday)",
        "month_anchor": f"2000-01-01 00:00 {timeframe.timezone} (Jan/Mar/May/Jul/Sep/Nov)",
        "crypto_intraday_anchor": UTC_ANCHOR, "closure": "left", "label": "left-UTC",
        "source_kind": source.kind, "source_interval": source.interval,
        "request_start": start, "request_end": end, "as_of": as_of,
        "policies": dict(leading=leading, trailing=trailing, unfinished=unfinished, missing=missing, short_session=short_session),
    }
    windows = _windows(start, end, timeframe)
    if not windows:
        if len(source_index):
            raise ValueError("No exchange windows exist for the supplied source bars")
        return WindowGroupPlan((), _frame([], [], context))
    slots = _slots(windows[0].start, windows[-1].end, timeframe, source)
    buckets: list[list[_Slot]] = [[] for _ in windows]
    # Two chronological scans, not one full source-grid scan per target window.
    window_id = 0
    for slot in slots:
        while window_id < len(windows) and slot.start >= windows[window_id].end:
            window_id += 1
        if window_id >= len(windows) or slot.end <= windows[window_id].start:
            continue
        window = windows[window_id]
        if slot.start < window.start or slot.end > window.end:
            raise ValueError("Source bar straddles a target boundary; supply finer-resolution bars")
        buckets[window_id].append(slot)

    observed = set(source_index)
    assigned: set[pd.Timestamp] = set()
    groups: list[CandleGroup] = []
    records: list[dict] = []
    labels: list[pd.Timestamp] = []
    all_missing: list[pd.Timestamp] = []
    for window, full_slots in zip(windows, buckets):
        requested: list[_Slot] = []
        for slot in full_slots:
            if slot.end <= start or slot.start >= end:
                continue
            if slot.start < start or slot.end > end:
                raise ValueError("Request boundary cuts a source bar; align the request or supply finer data")
            requested.append(slot)
        members = tuple(slot.label for slot in requested if slot.label in observed)
        if any(slot.start > as_of for slot in requested if slot.label in observed):
            raise ValueError("Source contains a bar that has not opened at as_of")
        assigned.update(members)
        closed = [slot for slot in requested if slot.end <= as_of]
        missing_bars = tuple(slot.label for slot in closed if slot.label not in observed)
        pending = tuple(slot.label for slot in requested if slot.end > as_of)
        all_missing.extend(missing_bars)
        lead, trail = window.start < start, window.end > end
        window_closed = window.end <= as_of
        complete = len(members) == len(full_slots) and bool(full_slots)
        retained = bool(members) and not (
            (lead and leading == "drop") or (trail and trailing == "drop")
            or (not window_closed and unfinished == "drop")
            or (bool(missing_bars) and missing == "drop")
            or (window.short and short_session == "drop")
        )
        if not full_slots:
            raise ValueError("Target window contains no expected source slots")
        groups.append(CandleGroup(window.start, members, len(full_slots)))
        labels.append(window.start)
        records.append({
            "window_start": window.start, "window_end": window.end,
            "source_count": len(members), "expected_count": len(full_slots),
            "requested_count": len(requested), "closed_requested_count": len(closed),
            "missing_bars": missing_bars, "pending_bars": pending,
            "leading_partial": lead, "trailing_partial": trail, "short_window": window.short,
            "window_closed": window_closed, "coverage_complete": not missing_bars,
            "count_complete": complete, "is_complete": complete and window_closed, "retained": retained,
        })
    if assigned != observed:
        raise ValueError("Source contains off-grid, non-session, or out-of-request bars")
    if all_missing and missing == "raise":
        raise MissingBarsError(tuple(all_missing))
    return WindowGroupPlan(tuple(groups), _frame(records, labels, context))
