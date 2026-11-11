"""Safe versioned JSON frames and atomic local writes; no pickle/code execution.

Exports are single-file bundles containing candles, ALL target metadata (including
dropped windows), source metadata, and provenance. UTC timestamps, typed flags,
tuples, timedeltas, and attrs round-trip. Index frequency is not a stored contract.
Local JSON files are limited to64MiB; oversized/corrupt data fails, never truncates.
"""

from dataclasses import dataclass
import json
from math import isfinite
import os
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from chronochart.aggregation import OHLCV, AggregationResult
from chronochart.normalization import normalize_ohlcv
from chronochart.providers import HistoricalRequest


EXPORT_SCHEMA = "chronochart-export-v1"
MAX_BYTES = 64 * 1024 * 1024
_DTYPES = {"float64", "int64", "bool", "object", "datetime64[ns, UTC]"}


def encode(value):
    if isinstance(value, pd.Timestamp):
        if pd.isna(value) or value.tz is None:
            raise ValueError("Stored timestamps must be aware/non-NaT")
        return {"$timestamp": value.tz_convert("UTC").isoformat()}
    if isinstance(value, pd.Timedelta):
        if pd.isna(value):
            raise ValueError("Cannot store NaT")
        return {"$timedelta_ns": value.value}
    if isinstance(value, np.generic):
        return encode(value.item())
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Stored dictionary keys must be strings")
        return {key: encode(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise ValueError(f"Unsupported/non-finite stored value: {type(value).__name__}")


def decode(value):
    if isinstance(value, dict):
        if set(value) == {"$timestamp"}:
            result = pd.Timestamp(value["$timestamp"])
            if pd.isna(result) or result.tz is None:
                raise ValueError("Invalid stored timestamp")
            return result.tz_convert("UTC")
        if set(value) == {"$timedelta_ns"}:
            number = value["$timedelta_ns"]
            if type(number) is not int:
                raise ValueError("Invalid stored duration")
            return pd.Timedelta(number, unit="ns")
        if set(value) == {"$tuple"}:
            if not isinstance(value["$tuple"], list):
                raise ValueError("Invalid stored tuple")
            return tuple(decode(item) for item in value["$tuple"])
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("Non-finite JSON value")
    return value


def frame_to_json(frame: pd.DataFrame) -> dict:
    if not isinstance(frame.index, pd.DatetimeIndex) or str(frame.index.tz) != "UTC":
        raise ValueError("Stored frames require UTC DatetimeIndex")
    if frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing or not frame.columns.is_unique:
        raise ValueError("Stored frame labels must be unique/non-NaT")
    if not all(isinstance(column, str) for column in frame.columns):
        raise ValueError("Stored columns must be strings")
    dtypes = [str(dtype) for dtype in frame.dtypes]
    if not set(dtypes).issubset(_DTYPES):
        raise ValueError("Unsupported stored frame dtype")
    return {
        "index": [item.isoformat() for item in frame.index], "columns": list(frame.columns),
        "dtypes": dtypes, "rows": [[encode(item) for item in row] for row in frame.itertuples(index=False, name=None)],
        "attrs": encode(frame.attrs),
    }


def frame_from_json(value: dict) -> pd.DataFrame:
    if not isinstance(value, dict) or set(value) != {"index", "columns", "dtypes", "rows", "attrs"}:
        raise ValueError("Invalid frame envelope")
    index, columns, dtypes, rows = (value[key] for key in ("index", "columns", "dtypes", "rows"))
    if not all(isinstance(item, list) for item in (index, columns, dtypes, rows)):
        raise ValueError("Invalid frame arrays")
    if len(columns) != len(dtypes) or len(index) != len(rows) or len(set(columns)) != len(columns):
        raise ValueError("Invalid frame shape")
    if not all(isinstance(column, str) for column in columns) or not set(dtypes).issubset(_DTYPES):
        raise ValueError("Invalid frame columns/dtypes")
    timestamps = [pd.Timestamp(item) for item in index]
    if any(pd.isna(item) or item.tz is None or str(item.tz) != "UTC" for item in timestamps):
        raise ValueError("Stored frame index must use UTC-aware instants")
    labels = pd.DatetimeIndex(timestamps, tz="UTC", name="Timestamp").as_unit("ns")
    if not labels.is_unique or not labels.is_monotonic_increasing:
        raise ValueError("Stored frame index must be sorted and unique")
    decoded = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(columns):
            raise ValueError("Invalid stored row")
        items = [decode(item) for item in row]
        for item, dtype in zip(items, dtypes):
            valid = (
                (dtype == "bool" and type(item) is bool)
                or (dtype == "int64" and type(item) is int)
                or (dtype == "float64" and type(item) in (int, float) and isfinite(item))
                or (dtype == "datetime64[ns, UTC]" and isinstance(item, pd.Timestamp) and str(item.tz) == "UTC")
                or dtype == "object"
            )
            if not valid:
                raise ValueError("Stored cell does not match its declared dtype")
        decoded.append(items)
    result = pd.DataFrame(decoded, index=labels, columns=columns).astype(dict(zip(columns, dtypes)))
    attrs = decode(value["attrs"])
    if not isinstance(attrs, dict):
        raise ValueError("Stored attrs must be a dictionary")
    result.attrs.update(attrs)
    return result


def json_bytes(value: dict) -> bytes:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        raise ValueError("Local JSON exceeds64MiB; use a smaller request")
    return data


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Local JSON exceeds64MiB")
    def reject_constant(item):
        raise ValueError(f"Non-finite JSON constant: {item}")

    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
    except RecursionError as exc:
        raise ValueError("Local JSON is too deeply nested") from exc
    if not isinstance(value, dict):
        raise ValueError("Local JSON must contain an object")
    return value


def atomic_json(path: Path, value: dict, *, overwrite: bool) -> None:
    data = json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".chronochart-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Same-filesystem hard link installs atomically WITHOUT clobbering a
            # concurrently created destination. Supported by local NTFS/POSIX.
            os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class ExportBundle:
    result: AggregationResult
    source_metadata: pd.DataFrame
    provenance: dict


def validate_export(bundle: ExportBundle) -> None:
    """Enforce the frontend-facing v1 contract, not merely JSON cell types.

    Validate financial ranges, flag/count/maturity consistency, exact retained
    candle labels, and source accounting. This does not authenticate a provider,
    prove prices are historically correct, or reconstruct source membership.
    """
    if not isinstance(bundle, ExportBundle) or not isinstance(bundle.provenance, dict):
        raise ValueError("Export requires an ExportBundle with dictionary provenance")
    if not {"request", "synthetic"}.issubset(bundle.provenance):
        raise ValueError("Export provenance requires the request and explicit synthetic flag")
    candles, info, source = bundle.result.candles, bundle.result.metadata, bundle.source_metadata
    for frame in (candles, info, source):
        if not isinstance(frame, pd.DataFrame) or not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError("Export tables require timestamp-indexed DataFrames")
        if str(frame.index.tz) != "UTC" or frame.index.hasnans or not frame.index.is_unique or not frame.index.is_monotonic_increasing or not frame.columns.is_unique:
            raise ValueError("Export tables require unique sorted UTC labels/columns")
    if tuple(candles.columns) != OHLCV or not all(str(dtype) == "float64" for dtype in candles.dtypes):
        raise ValueError("Export candles require canonical float64 OHLCV columns")
    normalize_ohlcv(candles)  # Validate without changing caller data/provenance.
    if not {"source_count", "expected_count", "is_complete", "retained"}.issubset(info.columns):
        raise ValueError("Export lacks required target metadata")
    for column in ("source_count", "expected_count"):
        if str(info[column].dtype) != "int64":
            raise ValueError("Export counts must be int64")
    if (info["source_count"] < 0).any() or (info["expected_count"] <= 0).any() or (info["source_count"] > info["expected_count"]).any():
        raise ValueError("Invalid export target counts")
    for column in ("is_complete", "retained", "count_complete", "window_closed", "coverage_complete",
                   "leading_partial", "trailing_partial", "short_window"):
        if column in info and str(info[column].dtype) != "bool":
            raise ValueError("Export flags must be boolean")
    for column in ("requested_count", "closed_requested_count"):
        if column in info and (str(info[column].dtype) != "int64" or (info[column] < 0).any() or (info[column] > info["expected_count"]).any()):
            raise ValueError("Invalid export requested/closed counts")
    if "requested_count" in info and (info["source_count"] > info["requested_count"]).any():
        raise ValueError("Export source count exceeds requested coverage")
    if {"requested_count", "closed_requested_count"}.issubset(info.columns) and (info["closed_requested_count"] > info["requested_count"]).any():
        raise ValueError("Export closed count exceeds requested coverage")
    missing_column = next((name for name in ("missing_sessions", "missing_bars") if name in info), None)
    pending_column = next((name for name in ("pending_sessions", "pending_bars") if name in info), None)
    for column in (missing_column, pending_column):
        if column is not None:
            for values in info[column]:
                if not isinstance(values, tuple) or any(not isinstance(item, pd.Timestamp) or pd.isna(item) or str(item.tz) != "UTC" for item in values):
                    raise ValueError("Export missing/pending labels require UTC timestamp tuples")
                if tuple(sorted(set(values))) != values:
                    raise ValueError("Export missing/pending labels must be unique and sorted")
    if "coverage_complete" in info:
        if missing_column is None or not (info["coverage_complete"] == info[missing_column].map(lambda values: not values)).all():
            raise ValueError("Export coverage flag contradicts missing labels")
    if missing_column and pending_column and {"requested_count", "closed_requested_count"}.issubset(info.columns):
        for _, row in info.iterrows():
            missing, pending = row[missing_column], row[pending_column]
            closed = int(row["closed_requested_count"])
            if set(missing).intersection(pending) or len(missing) > closed or closed + len(pending) != row["requested_count"]:
                raise ValueError("Export missing/pending accounting is inconsistent")
            if not closed - len(missing) <= row["source_count"] <= closed - len(missing) + len(pending):
                raise ValueError("Export source count contradicts missing/pending coverage")
    counts_complete = info["source_count"] == info["expected_count"]
    if "count_complete" in info and not (info["count_complete"] == counts_complete).all():
        raise ValueError("Export count_complete contradicts target counts")
    complete = counts_complete & info["window_closed"] if "window_closed" in info else counts_complete
    if not (info["is_complete"] == complete).all():
        raise ValueError("Export completeness contradicts target counts/maturity")
    retained = info.index[info["retained"]]
    if not candles.index.as_unit("ns").equals(retained.as_unit("ns")) or (info.loc[info["retained"], "source_count"] == 0).any():
        raise ValueError("Export candle labels contradict retained metadata")
    if sum(int(count) for count in info["source_count"]) != len(source):
        raise ValueError("Export source accounting does not match source metadata")
    if not {"bar_end", "is_closed", "crosses_request_end"}.issubset(source.columns):
        raise ValueError("Export lacks source maturity metadata")
    if not isinstance(source["bar_end"].dtype, pd.DatetimeTZDtype) or str(source["bar_end"].dt.tz) != "UTC":
        raise ValueError("Export source ends must be UTC instants")
    if source["bar_end"].isna().any() or (source["bar_end"] <= source.index).any():
        raise ValueError("Invalid export source ends")
    if any(str(source[column].dtype) != "bool" for column in ("is_closed", "crosses_request_end")):
        raise ValueError("Export source flags must be boolean")
    if "request" in bundle.provenance:
        try:
            request = HistoricalRequest(**bundle.provenance["request"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid export request provenance") from exc
        if ((source.index < request.start) | (source.index >= request.end)).any():
            raise ValueError("Export source labels contradict request bounds")
        if not (source["is_closed"] == (source["bar_end"] <= request.as_of)).all():
            raise ValueError("Export source maturity contradicts as_of")
        if not (source["crosses_request_end"] == (source["bar_end"] > request.end)).all():
            raise ValueError("Export source extents contradict request end")
        if request.forming != "include" and not source["is_closed"].all():
            raise ValueError("Export source contradicts forming policy")
        for column in (missing_column, pending_column):
            if column and any(not request.start <= item < request.end for values in info[column] for item in values):
                raise ValueError("Export missing/pending labels contradict request bounds")
        for column in ("window_start", "window_end", "window_close"):
            if column in info and (not isinstance(info[column].dtype, pd.DatetimeTZDtype) or str(info[column].dt.tz) != "UTC" or info[column].isna().any()):
                raise ValueError("Export window bounds must be UTC timestamps")
        maturity = "window_close" if "window_close" in info else "window_end" if "window_end" in info else None
        if maturity and "window_closed" in info and not (info["window_closed"] == (info[maturity] <= request.as_of)).all():
            raise ValueError("Export window maturity contradicts as_of")
    if "synthetic" in bundle.provenance and type(bundle.provenance["synthetic"]) is not bool:
        raise ValueError("Export synthetic provenance must be boolean")


def export_bundle(path: str | Path, bundle: ExportBundle, *, overwrite: bool = False) -> None:
    validate_export(bundle)
    atomic_json(Path(path), {
        "schema": EXPORT_SCHEMA, "candles": frame_to_json(bundle.result.candles),
        "metadata": frame_to_json(bundle.result.metadata),
        "source_metadata": frame_to_json(bundle.source_metadata), "provenance": encode(bundle.provenance),
    }, overwrite=overwrite)


def load_export(path: str | Path) -> ExportBundle:
    value = read_json(Path(path))
    if set(value) != {"schema", "candles", "metadata", "source_metadata", "provenance"} or value["schema"] != EXPORT_SCHEMA:
        raise ValueError("Unsupported/corrupt export schema")
    bundle = ExportBundle(
        AggregationResult(frame_from_json(value["candles"]), frame_from_json(value["metadata"])),
        frame_from_json(value["source_metadata"]), decode(value["provenance"]),
    )
    validate_export(bundle)
    return bundle
