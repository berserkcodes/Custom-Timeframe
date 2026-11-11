"""Provider-independent canonical OHLCV normalization, never aggregation.

Explicitly converts numeric strings to float64 and aware timestamps to UTC.
Missing/non-finite values, booleans/complex values, invalid OHLC, negative volume,
and conflicting duplicate timestamps raise. Identical duplicate rows collapse.
No imputation, reconstruction, naive-timezone guessing, or input mutation.
"""

from copy import deepcopy

import numpy as np
import pandas as pd

from chronochart.aggregation import OHLCV
from chronochart.providers import HistoricalRequest, MarketDataResult, ProviderResponseError


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


def validate_market_data(data: MarketDataResult, request: HistoricalRequest) -> MarketDataResult:
    """Normalize/validate a provider or cache result against its exact request.

    Validation does not erase provider provenance (e.g. duplicates already removed
    by pagination). Typed maturity flags must agree with actual source ends and
    the request; truthy strings are never interpreted as booleans.
    """
    if not isinstance(data, MarketDataResult) or not isinstance(data.metadata, pd.DataFrame):
        raise ProviderResponseError("Provider/cache must return MarketDataResult frames")
    candles = normalize_ohlcv(data.candles)
    candles.attrs.update(deepcopy(data.candles.attrs))
    info = data.metadata.copy(deep=True)
    if not isinstance(info.index, pd.DatetimeIndex) or str(info.index.tz) != "UTC" or not info.index.is_unique or not info.columns.is_unique:
        raise ProviderResponseError("Source metadata needs unique UTC timestamps")
    info.index = info.index.as_unit("ns").rename("Timestamp")
    info = info.sort_index(kind="stable")
    if not candles.index.equals(info.index) or not {"bar_end", "is_closed", "crosses_request_end"}.issubset(info.columns):
        raise ProviderResponseError("Source candles and maturity metadata must align")
    if not isinstance(info["bar_end"].dtype, pd.DatetimeTZDtype) or str(info["bar_end"].dt.tz) != "UTC":
        raise ProviderResponseError("Source bar ends must use UTC")
    info["bar_end"] = info["bar_end"].astype("datetime64[ns, UTC]")
    if info["bar_end"].isna().any() or (info["bar_end"] <= candles.index).any():
        raise ProviderResponseError("Invalid source interval ends")
    for name in ("is_closed", "crosses_request_end"):
        if str(info[name].dtype) != "bool":
            raise ProviderResponseError("Source flags must be boolean, not truthy strings")
    if not (info["is_closed"] == (info["bar_end"] <= request.as_of)).all():
        raise ProviderResponseError("Source maturity flags contradict as_of")
    if not (info["crosses_request_end"] == (info["bar_end"] > request.end)).all():
        raise ProviderResponseError("Source end-crossing flags contradict request")
    if ((candles.index < request.start) | (candles.index >= request.end)).any():
        raise ProviderResponseError("Source labels outside requested range")
    if request.forming != "include" and not info["is_closed"].all():
        raise ProviderResponseError("Provider/cache violated forming-candle policy")
    return MarketDataResult(candles, info)
