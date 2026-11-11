"""Optional raw-source filesystem cache; explicit TTL, refresh, versions, isolation.

Keys hash canonical provider identity + full source request + schema version.
Target timeframes are deliberately NOT part of a raw-source cache key. Expired,
refresh-requested, or explicitly refetched corrupt entries are invalidated before
the loader runs: failed refresh NEVER falls back to old data, even on a later call.
Per-key exclusive lock files fail busy rather than serving data during a refresh;
crash-left locks require explicit operator cleanup after ensuring no writer lives.
No pickle, stale-while-error, background refresh, or global cache is used.
"""

from dataclasses import dataclass
import hashlib
from math import isfinite
import os
from pathlib import Path
import time
from typing import Callable

from chronochart.providers import HistoricalRequest, MarketDataResult, ProviderResponseError
from chronochart.normalization import validate_market_data
from chronochart.storage import atomic_json, encode, frame_from_json, frame_to_json, json_bytes, read_json


CACHE_SCHEMA_VERSION = 1


class CacheError(RuntimeError):
    pass


class CacheCorruptError(CacheError):
    pass


class CacheBusyError(CacheError):
    pass


@dataclass(frozen=True)
class CacheOutcome:
    data: MarketDataResult
    status: str
    key: str
    created_at: float
    expires_at: float


class FileCache:
    def __init__(self, directory: str | Path, *, ttl: float = 3600, corrupt: str = "raise", clock: Callable[[], float] = time.time, schema_version: int = CACHE_SCHEMA_VERSION):
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not isfinite(ttl) or not 0 < ttl <= 30 * 86400:
            raise ValueError("Cache TTL must be positive/finite and at most30days")
        if corrupt not in ("raise", "refetch") or type(schema_version) is not int or schema_version < 1:
            raise ValueError("Invalid corrupt-cache policy/schema version")
        self.directory, self.ttl, self.corrupt = Path(directory), float(ttl), corrupt
        self.clock, self.schema_version = clock, schema_version

    def identity(self, provider: dict, request: HistoricalRequest) -> tuple[str, dict]:
        identity = {
            "schema_version": self.schema_version, "provider": encode(provider),
            "request": encode(dict(symbol=request.symbol, interval=request.interval, start=request.start,
                                   end=request.end, as_of=request.as_of, forming=request.forming)),
        }
        return hashlib.sha256(json_bytes(identity)).hexdigest(), identity

    def fetch(self, provider: dict, request: HistoricalRequest, loader: Callable[[], MarketDataResult], *, refresh: bool = False) -> CacheOutcome:
        if type(refresh) is not bool:
            raise ValueError("refresh must be boolean")
        key, identity = self.identity(provider, request)
        self.directory.mkdir(parents=True, exist_ok=True)
        path, lock = self.directory / f"{key}.json", self.directory / f"{key}.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise CacheBusyError("Cache key is busy; no stale read while another operation owns it") from exc
        os.close(descriptor)
        try:
            now = float(self.clock())
            if not isfinite(now):
                raise ValueError("Cache clock must be finite")
            status = "refresh" if refresh else "miss"
            if path.exists() and not refresh:
                try:
                    value = read_json(path)
                    if set(value) != {"identity", "created_at", "expires_at", "payload", "sha256"} or value["identity"] != identity:
                        raise ValueError("Cache identity/schema mismatch")
                    created, expires = value["created_at"], value["expires_at"]
                    if any(type(item) not in (int, float) or not isfinite(item) for item in (created, expires)) or not created <= now or expires <= created:
                        raise ValueError("Invalid cache timestamps/clock rollback")
                    payload = value["payload"]
                    if hashlib.sha256(json_bytes(payload)).hexdigest() != value["sha256"] or set(payload) != {"candles", "metadata"}:
                        raise ValueError("Cache checksum/payload mismatch")
                    data = MarketDataResult(frame_from_json(payload["candles"]), frame_from_json(payload["metadata"]))
                    if not data.candles.index.equals(data.metadata.index):
                        raise ValueError("Cache candle/metadata indices differ")
                    data = validate_market_data(data, request)
                    deadline = min(expires, created + self.ttl)
                    if now < deadline:
                        return CacheOutcome(data, "hit", key, created, deadline)
                    status = "expired"
                except (ValueError, TypeError, KeyError, OverflowError, RecursionError, ProviderResponseError) as exc:
                    if self.corrupt == "raise":
                        raise CacheCorruptError("Corrupt/incompatible cache; use explicit refresh or corrupt=refetch") from exc
                    status = "corrupt-refetch"
            # Delete only this managed digest entry, never a broad path. No old
            # entry remains usable if loader/network/serialization fails afterward.
            path.unlink(missing_ok=True)
            data = validate_market_data(loader(), request)
            created = float(self.clock())
            if not isfinite(created):
                raise ValueError("Cache clock must be finite")
            payload = {"candles": frame_to_json(data.candles), "metadata": frame_to_json(data.metadata)}
            atomic_json(path, {
                "identity": identity, "created_at": created, "expires_at": created + self.ttl,
                "payload": payload, "sha256": hashlib.sha256(json_bytes(payload)).hexdigest(),
            }, overwrite=True)
            return CacheOutcome(data, status, key, created, created + self.ttl)
        finally:
            lock.unlink(missing_ok=True)
