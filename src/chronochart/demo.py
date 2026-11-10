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
