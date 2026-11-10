"""Anchor-based XNYS session grouping; no OHLCV arithmetic or provider access."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.metadata import version
from numbers import Integral
import re
from typing import Literal

import exchange_calendars as xcals
import pandas as pd

from chronochart.aggregation import CandleGroup


ANCHOR = pd.Timestamp("2000-01-03", tz="UTC")
LAST_REQUEST_DATE = pd.Timestamp("2099-12-31", tz="UTC")
EdgePolicy = Literal["retain", "drop"]
MissingPolicy = Literal["raise", "retain", "drop"]


@dataclass(frozen=True)
class TradingSessionTimeframe:
    """A positive session count, never a calendar-day/week duration."""

    sessions: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.sessions, bool)
            or not isinstance(self.sessions, Integral)
            or not 0 < self.sessions <= 2**63 - 1
        ):
            raise ValueError("sessions must be a positive int64 integer")
        object.__setattr__(self, "sessions", int(self.sessions))

    @classmethod
    def parse(cls, value: str) -> "TradingSessionTimeframe":
        """Accept explicit session units and the prototype 3-trading-week alias."""
        if value == "3 trading weeks":
            return cls(15)
        match = re.fullmatch(r"([1-9][0-9]*) trading sessions?", value) if isinstance(value, str) else None
        if match is None:
            raise ValueError("Use '<count> trading sessions' or the alias '3 trading weeks'")
        return cls(int(match.group(1)))


@dataclass(frozen=True)
class SessionGroupPlan:
    """All intersecting windows and policy metadata, including omitted windows.

    ``groups`` always partition every source row, even if metadata marks a window
    not retained. This allows the reducer to validate all input before filtering.
    """

    groups: tuple[CandleGroup, ...]
    metadata: pd.DataFrame


class MissingSessionsError(ValueError):
    """Closed, requested exchange sessions have no corresponding source row."""

    def __init__(self, sessions: tuple[pd.Timestamp, ...]) -> None:
        self.sessions = sessions
        super().__init__(
            f"Missing {len(sessions)} closed requested XNYS session(s): "
            + ", ".join(str(timestamp.date()) for timestamp in sessions[:5])
        )


@lru_cache(maxsize=1)
def _xnys_schedule() -> pd.DataFrame:
    """Cache a private pinned-library schedule with a fixed lookahead horizon."""
    calendar = xcals.get_calendar("XNYS", start="2000-01-01", end="2101-12-31")
    schedule = calendar.schedule.loc[:, ["open", "close"]].copy(deep=True)
    schedule.index = schedule.index.tz_localize("UTC")
    if schedule.index[0] != ANCHOR:
        raise RuntimeError("XNYS anchor changed; review calendar version and grouping semantics")
    return schedule


def _request_date(value: str | pd.Timestamp, name: str) -> pd.Timestamp:
    if not isinstance(value, (str, pd.Timestamp)) or (
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None
    ):
        raise ValueError(f"{name} must be an ISO date or midnight UTC/naive pandas Timestamp")
    try:
        timestamp = pd.Timestamp(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {name} date") from exc
    if pd.isna(timestamp) or timestamp != timestamp.normalize():
        raise ValueError(f"{name} must be a non-NaT midnight date")
    if timestamp.tz is None:
        timestamp = timestamp.tz_localize("UTC")
    if str(timestamp.tz) != "UTC":
        raise ValueError(f"{name} must use UTC date labels")
    if not ANCHOR <= timestamp <= LAST_REQUEST_DATE:
        raise ValueError(f"{name} must lie between 2000-01-03 and 2099-12-31 inclusive")
    return timestamp


def _metadata(records: list[dict], labels: list[pd.Timestamp]) -> pd.DataFrame:
    """Keep identical dtypes/columns for empty, dropped, and populated plans."""
    dtypes = {
        "window_start": "datetime64[ns, UTC]", "window_end": "datetime64[ns, UTC]",
        "window_close": "datetime64[ns, UTC]", "source_count": "int64",
        "expected_count": "int64", "requested_count": "int64",
        "closed_requested_count": "int64", "missing_sessions": "object",
        "pending_sessions": "object", "leading_partial": "bool", "trailing_partial": "bool",
        "window_closed": "bool", "coverage_complete": "bool", "count_complete": "bool",
        "is_complete": "bool", "retained": "bool",
    }
    index = pd.DatetimeIndex(labels, tz="UTC", name="Timestamp")
    result = pd.DataFrame(records, index=index, columns=list(dtypes)).astype(dtypes)
    result.attrs.update({
        "exchange": "XNYS", "calendar_version": version("exchange-calendars"),
        "anchor": ANCHOR,
    })
    return result


def build_trading_session_groups(
    source_index: pd.DatetimeIndex,
    timeframe: TradingSessionTimeframe,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    as_of: pd.Timestamp,
    leading: EdgePolicy = "retain",
    trailing: EdgePolicy = "retain",
    unfinished: EdgePolicy = "retain",
    missing: MissingPolicy = "raise",
) -> SessionGroupPlan:
    """Plan fixed-anchor XNYS windows for an inclusive request date range.

    Source labels must be unique UTC midnights denoting exchange session dates,
    not raw intraday/provider timestamps. ``as_of`` is an explicit aware instant;
    schedule closes (including early closes/DST) decide elapsed expectations.
    Missing closed requested sessions raise by default; holidays never do.
    See docs/trading_sessions.md for all policy and metadata definitions.
    """
    if not isinstance(timeframe, TradingSessionTimeframe):
        raise ValueError("timeframe must be a TradingSessionTimeframe")
    for name, policy in (("leading", leading), ("trailing", trailing), ("unfinished", unfinished)):
        if policy not in ("retain", "drop"):
            raise ValueError(f"{name} must be 'retain' or 'drop'")
    if missing not in ("raise", "retain", "drop"):
        raise ValueError("missing must be 'raise', 'retain', or 'drop'")
    start_date, end_date = _request_date(start, "start"), _request_date(end, "end")
    if start_date > end_date:
        raise ValueError("start must not be after end")
    if not isinstance(as_of, pd.Timestamp) or pd.isna(as_of) or as_of.tz is None:
        raise ValueError("as_of must be an explicit timezone-aware pandas Timestamp")
    as_of = as_of.tz_convert("UTC")
    if not isinstance(source_index, pd.DatetimeIndex):
        raise ValueError("source_index must be a DatetimeIndex")
    if source_index.hasnans or not source_index.is_unique:
        raise ValueError("Source session labels must be unique and non-NaT")
    if str(source_index.tz) != "UTC" or not source_index.equals(source_index.normalize()):
        raise ValueError("Source session labels must be UTC midnights")
    if ((source_index < start_date) | (source_index > end_date)).any():
        raise ValueError("Source sessions must lie inside the inclusive request range")

    schedule = _xnys_schedule()
    if not source_index.isin(schedule.index).all():
        raise ValueError("Source includes a non-XNYS session (weekend, holiday, or closure)")
    if (schedule.loc[source_index, "open"] > as_of).any():
        raise ValueError("Source includes a session that has not opened at as_of")
    first = int(schedule.index.searchsorted(start_date, side="left"))
    stop = int(schedule.index.searchsorted(end_date, side="right"))
    if first == stop:
        return SessionGroupPlan((), _metadata([], []))

    observed = set(source_index)
    groups: list[CandleGroup] = []
    records: list[dict] = []
    labels: list[pd.Timestamp] = []
    all_missing: list[pd.Timestamp] = []
    size = timeframe.sessions
    for group_id in range(first // size, (stop - 1) // size + 1):
        left, right = group_id * size, (group_id + 1) * size
        if right > len(schedule):
            raise ValueError("Full timeframe window exceeds the calendar horizon (2101-12-31)")
        window = schedule.iloc[left:right]
        requested = schedule.iloc[max(left, first):min(right, stop)]
        members = tuple(timestamp for timestamp in requested.index if timestamp in observed)
        closed = requested.index[requested["close"] <= as_of]
        missing_sessions = tuple(timestamp for timestamp in closed if timestamp not in observed)
        pending_sessions = tuple(requested.index[requested["close"] > as_of])
        all_missing.extend(missing_sessions)
        label = window.index[0]
        window_closed = bool(window.iloc[-1]["close"] <= as_of)
        leading_partial = bool(label < start_date)
        trailing_partial = bool(window.index[-1] > end_date)
        count_complete = len(members) == size
        retained = bool(members) and not (
            (leading_partial and leading == "drop")
            or (trailing_partial and trailing == "drop")
            or (not window_closed and unfinished == "drop")
            or (bool(missing_sessions) and missing == "drop")
        )
        groups.append(CandleGroup(label, members, size))
        labels.append(label)
        records.append({
            "window_start": label, "window_end": window.index[-1],
            "window_close": window.iloc[-1]["close"], "source_count": len(members),
            "expected_count": size, "requested_count": len(requested),
            "closed_requested_count": len(closed), "missing_sessions": missing_sessions,
            "pending_sessions": pending_sessions, "leading_partial": leading_partial,
            "trailing_partial": trailing_partial, "window_closed": window_closed,
            "coverage_complete": not missing_sessions, "count_complete": count_complete,
            "is_complete": count_complete and window_closed, "retained": retained,
        })
    if all_missing and missing == "raise":
        raise MissingSessionsError(tuple(all_missing))
    return SessionGroupPlan(tuple(groups), _metadata(records, labels))
