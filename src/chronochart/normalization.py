"""Provider-independent canonical OHLCV normalization, never aggregation.

Explicitly converts numeric strings to float64 and aware timestamps to UTC.
Missing/non-finite values, booleans/complex values, invalid OHLC, negative volume,
and conflicting duplicate timestamps raise. Identical duplicate rows collapse.
No imputation, reconstruction, naive-timezone guessing, or input mutation.
"""

import numpy as np
import pandas as pd

from chronochart.aggregation import OHLCV


def normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Return sorted unique UTC OHLCV; O(n log n) sort, O(n) validation/dedup.

    Float64 precision, including integer exactness limits, is intentional and
    consistent with the reduction core. Additional columns are not propagated.
    """
    if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("OHLCV input must be a DataFrame with DatetimeIndex")
    if frame.index.tz is None or frame.index.hasnans:
        raise ValueError("OHLCV timestamps must be aware and non-NaT")
    if not frame.columns.is_unique or not set(OHLCV).issubset(frame.columns):
        raise ValueError("OHLCV requires unique Open, High, Low, Close, Volume columns")
    result = frame.loc[:, list(OHLCV)].copy(deep=True)
    result.index = result.index.tz_convert("UTC").as_unit("ns").rename("Timestamp")
    for column in OHLCV:
        if any(isinstance(value, (bool, np.bool_, complex, np.complexfloating)) for value in result[column]):
            raise ValueError("OHLCV values must be real numeric values, not bool/complex")
        try:
            result[column] = pd.to_numeric(result[column], errors="raise").astype("float64")
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("OHLCV values must be numeric") from exc
    if not np.isfinite(result.to_numpy()).all():
        raise ValueError("Missing/non-finite OHLCV values are not allowed")
    if (
        (result["High"] < result[["Open", "Low", "Close"]].max(axis=1)).any()
        or (result["Low"] > result[["Open", "High", "Close"]].min(axis=1)).any()
        or (result["Volume"] < 0).any()
    ):
        raise ValueError("Invalid OHLC range or negative Volume")
    duplicate_mask = result.index.duplicated(keep=False)
    if duplicate_mask.any():
        counts = result.loc[duplicate_mask].groupby(level=0, sort=False).nunique(dropna=False)
        if (counts > 1).any().any():
            raise ValueError("Conflicting duplicate OHLCV timestamp")
    duplicate_count = int(result.index.duplicated().sum())
    result = result.loc[~result.index.duplicated(keep="first")].sort_index(kind="stable").copy(deep=True)
    result.attrs.update(schema="OHLCV-v1", duplicates_removed=duplicate_count)
    return result
