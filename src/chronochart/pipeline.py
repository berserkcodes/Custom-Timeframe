"""Compose calendar grouping and pure reduction without mixing their semantics."""

import pandas as pd

from chronochart.aggregation import AggregationResult, aggregate_candles
from chronochart.timeframes import (
    EdgePolicy, MissingPolicy, TradingSessionTimeframe, build_trading_session_groups,
)


def aggregate_trading_sessions(
    candles: pd.DataFrame,
    timeframe: TradingSessionTimeframe,
    *,
    start: str | pd.Timestamp,
    end: str | pd.Timestamp,
    as_of: pd.Timestamp,
    leading: EdgePolicy = "retain",
    trailing: EdgePolicy = "retain",
    unfinished: EdgePolicy = "retain",
    missing: MissingPolicy = "raise",
) -> AggregationResult:
    """Validate/reduce all source rows, then apply explicit window policies.

    ``is_complete`` here additionally requires the calendar window to be closed;
    ``count_complete`` preserves the reducer's purely count-based definition.
    Omitted groups remain in metadata, and never bypass OHLCV validation.
    """
    if not isinstance(candles, pd.DataFrame):
        raise ValueError("candles must be a pandas DataFrame")
    plan = build_trading_session_groups(
        candles.index, timeframe, start=start, end=end, as_of=as_of,
        leading=leading, trailing=trailing, unfinished=unfinished, missing=missing,
    )
    reduced = aggregate_candles(candles, plan.groups, incomplete="retain")
    labels = plan.metadata.index[plan.metadata["retained"]]
    output = reduced.candles.loc[reduced.candles.index.isin(labels)].copy(deep=True)
    return AggregationResult(output, plan.metadata.copy(deep=True))
