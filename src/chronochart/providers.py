"""Provider-independent historical-data interface and explicit UTC request model.

No authentication/trading interface is exposed. Provider-specific validation,
transport, and aggregation remain separate. as_of classifies candle maturity;
it does not reconstruct historical point-in-time snapshots of forming candles.
"""

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

import pandas as pd


class ProviderError(RuntimeError):
    """A provider failed; no partial history is returned as a successful result."""


class ProviderResponseError(ProviderError):
    """Malformed, conflicting, or non-progressing provider response."""


class ProviderAccessError(ProviderError):
    """Access is restricted/unavailable; do not bypass regional restrictions."""


class ProviderRateLimitError(ProviderError):
    """Throttled/banned, or Retry-After exceeds the configured retry budget."""

    def __init__(self, message: str, *, status: int, retry_after: float | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)


class ProviderHTTPError(ProviderError):
    """A non-retryable HTTP/API failure, including unknown server-side symbols."""

    def __init__(self, message: str, *, status: int, code: int | None = None):
        self.status = status
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class HistoricalRequest:
    """Select provider-declared labels in [start,end), independent of pagination.

    Binance labels are actual bar OPEN instants. Equity daily adapters must declare
    their session-date label semantics explicitly; UTC labels alone do not imply
    an exchange calendar or equal source resolution across providers.

    A final selected bar can extend past end; metadata must expose that fact and
    downstream grouping must not split it. Times are explicit aware instants at
    millisecond precision; forming is exclude(default), include, or raise.
    """

    symbol: str
    interval: str
    start: pd.Timestamp
    end: pd.Timestamp
    as_of: pd.Timestamp
    forming: Literal["exclude", "include", "raise"] = "exclude"

    def __post_init__(self) -> None:
        for name in ("symbol", "interval"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a nonempty string")
        for name in ("start", "end", "as_of"):
            value = getattr(self, name)
            if not isinstance(value, pd.Timestamp) or pd.isna(value) or value.tz is None:
                raise ValueError(f"{name} must be an explicit aware pandas Timestamp")
            value = value.tz_convert("UTC")
            if not pd.Timestamp("1970-01-01", tz="UTC") <= value <= pd.Timestamp("2100-01-01", tz="UTC"):
                raise ValueError(f"{name} must be within 1970..2100 UTC")
            if value.value % 1_000_000:
                raise ValueError(f"{name} must have millisecond precision")
            object.__setattr__(self, name, value)
        if self.start >= self.end:
            raise ValueError("start must be before end")
        if self.forming not in ("exclude", "include", "raise"):
            raise ValueError("forming must be 'exclude', 'include', or 'raise'")


@dataclass(frozen=True)
class MarketDataResult:
    """Canonical OHLCV plus aligned bar_end/is_closed/crosses_request_end metadata.

    Index is unique, sorted, UTC Timestamp; bar_end is exclusive. Metadata attrs
    identify provider, symbol, interval, request, and exclusions; absence of bars
    is never filled. Correctness/finalization of returned prices is provider-owned.
    """

    candles: pd.DataFrame
    metadata: pd.DataFrame


@runtime_checkable
class HistoricalDataProvider(Protocol):
    """Common contract for real providers and deterministic offline fixtures."""

    def fetch_history(self, request: HistoricalRequest) -> MarketDataResult:
        ...
