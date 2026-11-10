"""Pure OHLCV reduction over caller-defined groups; no calendar/provider logic.

See docs/aggregation.md for the input, timestamp, missing-data, and completeness
contracts. Sorting costs O(n log n); validation and reduction cost O(n + g), with
O(n + g) auxiliary space, for n source rows and g explicit groups.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Integral
from typing import Literal

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_complex_dtype, is_numeric_dtype


OHLCV = ("Open", "High", "Low", "Close", "Volume")


@dataclass(frozen=True)
class CandleGroup:
    """An explicit output label, source membership, and expected row count.

    Timestamps must be pandas Timestamps in the source index's timezone (or all
    naive). Members may be unordered; Open/Close always follow source chronology.
    An empty member tuple is allowed, but never creates a synthetic OHLCV row.
    """

    timestamp: pd.Timestamp
    members: tuple[pd.Timestamp, ...]
    expected_count: int


@dataclass(frozen=True)
class AggregationResult:
    """New output frames; metadata includes retained, dropped, and empty groups."""

    candles: pd.DataFrame
    metadata: pd.DataFrame


def _check_timestamp(value: pd.Timestamp, timezone: object) -> None:
    if not isinstance(value, pd.Timestamp) or pd.isna(value):
        raise ValueError("Group timestamps and members must be non-NaT pandas Timestamps")
    if str(value.tz) != str(timezone):
        raise ValueError("Group timestamps and members must use the source index timezone")


def _validated_rows(candles: pd.DataFrame) -> pd.DataFrame:
    """Validate only; do not normalize, fill, deduplicate, or mutate source data."""
    if not isinstance(candles, pd.DataFrame):
        raise ValueError("candles must be a pandas DataFrame")
    if not isinstance(candles.index, pd.DatetimeIndex):
        raise ValueError("candles must have a DatetimeIndex")
    if candles.index.hasnans or not candles.index.is_unique:
        raise ValueError("Source timestamps must be unique and non-NaT")
    if not candles.columns.is_unique or not set(OHLCV).issubset(candles.columns):
        raise ValueError("Source must contain unique Open, High, Low, Close, Volume columns")
    for name in OHLCV:
        dtype = candles[name].dtype
        if len(candles) and (
            not is_numeric_dtype(dtype) or is_bool_dtype(dtype) or is_complex_dtype(dtype)
        ):
            raise ValueError(f"{name} must contain real numeric values, not strings or booleans")
    try:
        rows = candles.loc[:, list(OHLCV)].astype("float64").copy(deep=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("OHLCV values must be representable as finite float64") from exc
    if not np.isfinite(rows.to_numpy()).all():
        raise ValueError("Missing or non-finite OHLCV values are not allowed")
    if (
        (rows["High"] < rows[["Open", "Close", "Low"]].max(axis=1)).any()
        or (rows["Low"] > rows[["Open", "Close", "High"]].min(axis=1)).any()
        or (rows["Volume"] < 0).any()
    ):
        raise ValueError("Invalid OHLC range or negative Volume")
    return rows


def aggregate_candles(
    candles: pd.DataFrame,
    groups: Sequence[CandleGroup],
    *,
    incomplete: Literal["retain", "drop"] = "retain",
) -> AggregationResult:
    """Reduce an exact partition of source rows without changing any input.

    Every source timestamp must occur in exactly one group's members. Groups
    must have strictly increasing explicit output timestamps and positive integer
    expected_count >= member count. Completeness means observed == expected; it
    does not claim that a live candle has closed or a market calendar is valid.

    Rows are sorted chronologically on a private copy. Output uses the supplied
    group labels (index name ``Timestamp``) and source timezone. Values are float64.
    Strictly reject missing values, duplicates, unknown/unassigned memberships,
    inconsistent timezones, invalid OHLC, and overflow; never silently skip rows.
    """
    if incomplete not in ("retain", "drop"):
        raise ValueError("incomplete must be 'retain' or 'drop'")
    rows = _validated_rows(candles)
    groups = tuple(groups)
    source_timestamps = set(rows.index)
    owners: dict[pd.Timestamp, int] = {}
    labels: list[pd.Timestamp] = []
    for group_id, group in enumerate(groups):
        if not isinstance(group, CandleGroup):
            raise ValueError("groups must contain CandleGroup objects")
        _check_timestamp(group.timestamp, rows.index.tz)
        if labels and group.timestamp <= labels[-1]:
            raise ValueError("Group output timestamps must be unique and strictly increasing")
        labels.append(group.timestamp)
        if (
            isinstance(group.expected_count, bool)
            or not isinstance(group.expected_count, Integral)
            or group.expected_count <= 0
            or group.expected_count > np.iinfo(np.int64).max
        ):
            raise ValueError("expected_count must be a positive int64 integer")
        if not isinstance(group.members, tuple):
            raise ValueError("members must be a tuple of pandas Timestamps")
        if len(group.members) > group.expected_count:
            raise ValueError("A group cannot contain more rows than expected_count")
        for timestamp in group.members:
            _check_timestamp(timestamp, rows.index.tz)
            if timestamp not in source_timestamps:
                raise ValueError(f"Unknown source timestamp: {timestamp}")
            if timestamp in owners:
                raise ValueError(f"Repeated group membership: {timestamp}")
            owners[timestamp] = group_id
    if len(owners) != len(rows):
        raise ValueError("Every source row must belong to exactly one explicit group")

    # Sorting is separate from the linear reduction; group membership order never
    # decides Open/Close. No inference of session/calendar boundaries occurs here.
    rows = rows.sort_index(kind="stable")
    positions: list[list[int]] = [[] for _ in groups]
    for position, timestamp in enumerate(rows.index):
        positions[owners[timestamp]].append(position)

    reduced: list[list[float]] = []
    output_labels: list[pd.Timestamp] = []
    metadata: list[tuple[int, int, bool, bool]] = []
    for group, group_positions in zip(groups, positions):
        count = len(group_positions)
        complete = count == group.expected_count
        retained = count > 0 and (complete or incomplete == "retain")
        metadata.append((count, group.expected_count, complete, retained))
        if not retained:
            continue
        values = rows.iloc[group_positions].to_numpy()
        try:
            volume = fsum(values[:, 4])
        except OverflowError as exc:
            raise ValueError("Aggregated Volume overflows float64") from exc
        if not isfinite(volume):
            raise ValueError("Aggregated Volume must be finite")
        reduced.append([
            values[0, 0], values[:, 1].max(), values[:, 2].min(),
            values[-1, 3], volume,
        ])
        output_labels.append(group.timestamp)

    def index(timestamps: list[pd.Timestamp]) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(timestamps, tz=rows.index.tz, name="Timestamp")

    output = pd.DataFrame(reduced, index=index(output_labels), columns=OHLCV, dtype="float64")
    info = pd.DataFrame(
        metadata, index=index(labels),
        columns=["source_count", "expected_count", "is_complete", "retained"],
    ).astype({
        "source_count": "int64", "expected_count": "int64",
        "is_complete": "bool", "retained": "bool",
    })
    return AggregationResult(output, info)
