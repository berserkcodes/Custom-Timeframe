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

from chronochart.aggregation import AggregationResult


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


def export_bundle(path: str | Path, bundle: ExportBundle, *, overwrite: bool = False) -> None:
    atomic_json(Path(path), {
        "schema": EXPORT_SCHEMA, "candles": frame_to_json(bundle.result.candles),
        "metadata": frame_to_json(bundle.result.metadata),
        "source_metadata": frame_to_json(bundle.source_metadata), "provenance": encode(bundle.provenance),
    }, overwrite=overwrite)


def load_export(path: str | Path) -> ExportBundle:
    value = read_json(Path(path))
    if set(value) != {"schema", "candles", "metadata", "source_metadata", "provenance"} or value["schema"] != EXPORT_SCHEMA:
        raise ValueError("Unsupported/corrupt export schema")
    return ExportBundle(
        AggregationResult(frame_from_json(value["candles"]), frame_from_json(value["metadata"])),
        frame_from_json(value["source_metadata"]), decode(value["provenance"]),
    )
