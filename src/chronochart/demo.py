"""Deterministic, hand-checkable aggregation fixture; no downloaded data."""

from typing import Literal

import pandas as pd

from chronochart.aggregation import OHLCV, AggregationResult, CandleGroup, aggregate_candles


def aggregate_fixture(*, incomplete: Literal["retain", "drop"] = "retain") -> AggregationResult:
    """Demonstrate explicit groups of three observed source candles."""
    timestamps = pd.to_datetime([
        "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08",
    ], utc=True)
    candles = pd.DataFrame([
        [10, 13, 9, 12, 100],
        [12, 15, 11, 14, 200],
        [14, 16, 13, 15, 300],
        [15, 18, 14, 17, 400],
        [17, 19, 16, 18, 500],
    ], index=timestamps, columns=OHLCV)
    groups = (
        CandleGroup(timestamps[0], tuple(timestamps[:3]), 3),
        CandleGroup(timestamps[3], tuple(timestamps[3:]), 3),
    )
    return aggregate_candles(candles, groups, incomplete=incomplete)


def aggregate_session_fixture(timeframe: str = "10 trading sessions") -> AggregationResult:
    """Omit Jan 10 deliberately; Jan 17 is a holiday, not missing coverage."""
    from chronochart.pipeline import aggregate_trading_sessions
    from chronochart.timeframes import TradingSessionTimeframe

    dates = pd.to_datetime([
        "2000-01-03", "2000-01-04", "2000-01-05", "2000-01-06", "2000-01-07",
        "2000-01-10", "2000-01-11", "2000-01-12", "2000-01-13", "2000-01-14",
        "2000-01-18", "2000-01-19", "2000-01-20", "2000-01-21", "2000-01-24",
        "2000-01-25", "2000-01-26", "2000-01-27", "2000-01-28", "2000-01-31",
    ], utc=True)
    candles = pd.DataFrame(
        [[i, i + 1, i - 1, i + 0.5, 10] for i in range(1, 21)],
        index=dates, columns=OHLCV,
    ).drop(pd.Timestamp("2000-01-10", tz="UTC"))
    return aggregate_trading_sessions(
        candles, TradingSessionTimeframe.parse(timeframe),
        start="2000-01-03", end="2000-01-31", as_of=pd.Timestamp("2000-03-01", tz="UTC"),
        missing="retain",
    )
