"""No-network Binance contract tests; transport, throttling, and clock are explicit."""

from copy import deepcopy
from contextlib import redirect_stdout
from dataclasses import replace
import io
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal
import requests

from chronochart.binance import BinanceOptions, BinanceProvider, INTERVALS, URL
from chronochart.providers import (
    HistoricalDataProvider, HistoricalRequest, ProviderAccessError, ProviderError,
    ProviderHTTPError, ProviderRateLimitError, ProviderResponseError,
)


BASE = pd.Timestamp("2025-01-01", tz="UTC")
MINUTE = pd.Timedelta(minutes=1)


def kline(position=0, *, start=None, end=None, values=None):
    start = start if start is not None else BASE + position * MINUTE
    end = end if end is not None else start + MINUTE
    values = values or ["10", "12", "9", "11", "100"]
    return [start.value // 1_000_000, *values, end.value // 1_000_000 - 1, "1100", 5, "50", "550", "0"]


class Response:
    def __init__(self, data=None, status=200, headers=None, invalid_json=False):
        self.status_code = status
        self.headers = headers or {}
        self.data = data if data is not None else []
        self.invalid_json = invalid_json

    def json(self):
        if self.invalid_json:
            raise ValueError("bad JSON")
        return self.data


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, deepcopy(kwargs)))
        if not self.responses:
            raise AssertionError("Unexpected HTTP call; test never allows real network")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


class BinanceTests(unittest.TestCase):
    def request(self, **kwargs):
        values = dict(symbol="BTCUSDT", interval="1m", start=BASE, end=BASE + 4 * MINUTE, as_of=BASE + 10 * MINUTE)
        values.update(kwargs)
        return HistoricalRequest(**values)

    def provider(self, transport, **options):
        settings = dict(page_pause=0)
        settings.update(options)
        self.waits = []
        return BinanceProvider(BinanceOptions(**settings), transport=transport, sleep=self.waits.append)

    def test_mocked_pagination_overlap_and_constant_exclusive_end(self):
        transport = Transport(Response([kline(0), kline(1)]), Response([kline(1), kline(2)]), Response([kline(3)]))
        result = self.provider(transport, page_size=2).fetch_history(self.request())
        self.assertEqual(len(result.candles), 4)
        self.assertEqual(result.metadata.attrs["duplicates_removed"], 1)
        self.assertEqual([call[1]["params"]["startTime"] for call in transport.calls], [
            BASE.value // 1_000_000, (BASE + MINUTE).value // 1_000_000 + 1,
            (BASE + 2 * MINUTE).value // 1_000_000 + 1,
        ])
        for url, kwargs in transport.calls:
            self.assertEqual(url, URL)
            self.assertEqual(kwargs["params"]["endTime"], (BASE + 4 * MINUTE).value // 1_000_000 - 1)
            self.assertEqual(kwargs["params"]["timeZone"], "0")
            self.assertEqual(kwargs["timeout"], (3.05, 10.0))
            self.assertFalse(kwargs["allow_redirects"])
            self.assertNotIn("headers", kwargs)
        self.assertEqual(str(result.candles.index.tz), "UTC")
        self.assertEqual(result.metadata["bar_end"].tolist(), list(pd.date_range(BASE + MINUTE, periods=4, freq="min")))

    def test_short_page_continues_until_empty_without_gap_filling(self):
        transport = Transport(Response([kline(0)]), Response([kline(2)]), Response([]))
        result = self.provider(transport, page_size=2).fetch_history(self.request())
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(list(result.candles.index), [BASE, BASE + 2 * MINUTE])

    def test_empty_history_has_typed_canonical_frames(self):
        result = self.provider(Transport(Response([]))).fetch_history(self.request())
        self.assertTrue(result.candles.empty)
        self.assertTrue((result.candles.dtypes == "float64").all())
        self.assertEqual(str(result.candles.index.tz), "UTC")
        self.assertEqual(str(result.metadata["bar_end"].dtype), "datetime64[ns, UTC]")
        self.assertEqual(result.metadata["is_closed"].dtype, "bool")

    def test_unsorted_rows_and_identical_duplicates_normalized_without_mutation(self):
        rows = [kline(2), kline(0), kline(1), kline(1)]
        before = deepcopy(rows)
        result = self.provider(Transport(Response(rows))).fetch_history(self.request(end=BASE + 3 * MINUTE))
        self.assertEqual(list(result.candles.index), list(pd.date_range(BASE, periods=3, freq="min")))
        self.assertEqual(result.metadata.attrs["duplicates_removed"], 1)
        self.assertEqual(rows, before)

    def test_conflicting_duplicates_fail_even_if_forming_would_be_excluded(self):
        changed = kline(0, values=["10", "12", "9", "11.5", "100"])
        for rows in ([kline(0), changed], [changed, kline(0)]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ProviderResponseError, "Conflicting duplicate"):
                self.provider(Transport(Response(rows))).fetch_history(self.request(as_of=BASE + pd.Timedelta(seconds=30)))

    def test_no_progress_repeated_page_fails_not_loops(self):
        transport = Transport(Response([kline(0)]), Response([kline(0)]))
        with self.assertRaisesRegex(ProviderResponseError, "forward progress"):
            self.provider(transport).fetch_history(self.request())
        self.assertEqual(len(transport.calls), 2)

    def test_unknown_stale_or_out_of_range_rows_fail(self):
        transport = Transport(Response([kline(2)]), Response([kline(1)]))
        with self.assertRaisesRegex(ProviderResponseError, "unknown stale"):
            self.provider(transport).fetch_history(self.request())
        for row in (kline(-1), kline(4)):
            with self.subTest(row=row), self.assertRaisesRegex(ProviderResponseError, "boundaries"):
                self.provider(Transport(Response([row]))).fetch_history(self.request())

    def test_current_candle_exclude_include_raise_and_exact_close(self):
        for policy, count in (("exclude", 0), ("include", 1)):
            with self.subTest(policy=policy):
                result = self.provider(Transport(Response([kline(0)]))).fetch_history(self.request(
                    end=BASE + MINUTE, as_of=BASE + MINUTE - pd.Timedelta(milliseconds=1), forming=policy,
                ))
                self.assertEqual(len(result.candles), count)
                self.assertEqual(result.metadata.attrs["forming_encountered"], 1)
                if count:
                    self.assertFalse(result.metadata.iloc[0]["is_closed"])
        with self.assertRaisesRegex(ProviderResponseError, "forming"):
            self.provider(Transport(Response([kline(0)]))).fetch_history(self.request(
                end=BASE + MINUTE, as_of=BASE + pd.Timedelta(seconds=30), forming="raise",
            ))
        result = self.provider(Transport(Response([kline(0)]))).fetch_history(self.request(end=BASE + MINUTE, as_of=BASE + MINUTE))
        self.assertTrue(result.metadata.iloc[0]["is_closed"])

    def test_request_selects_open_times_and_marks_end_crossing(self):
        result = self.provider(Transport(Response([kline(0)]))).fetch_history(self.request(end=BASE + pd.Timedelta(seconds=30)))
        self.assertTrue(result.metadata.iloc[0]["crosses_request_end"])
        self.assertEqual(result.metadata.iloc[0]["bar_end"], BASE + MINUTE)
        self.assertEqual(result.candles.iloc[0]["Volume"], 100)  # never prorated

    def test_future_unopened_candle_is_invalid(self):
        with self.assertRaisesRegex(ProviderResponseError, "future-unopened"):
            self.provider(Transport(Response([kline(1)]))).fetch_history(self.request(as_of=BASE))

    def test_monthly_leap_year_and_weekly_end_semantics(self):
        for interval, left, right in (
            ("1M", "2024-02-01", "2024-03-01"),
            ("1M", "2024-12-01", "2025-01-01"),
            ("1w", "2024-02-26", "2024-03-04"),
            ("3d", "2024-02-28", "2024-03-02"),
        ):
            left, right = pd.Timestamp(left, tz="UTC"), pd.Timestamp(right, tz="UTC")
            with self.subTest(interval=interval):
                result = self.provider(Transport(Response([kline(start=left, end=right)]))).fetch_history(self.request(
                    interval=interval, start=left, end=right, as_of=right,
                ))
                self.assertEqual(result.metadata.iloc[0]["bar_end"], right)

    def test_every_documented_fixed_interval(self):
        durations = {
            "1s": pd.Timedelta(seconds=1),
            **{f"{n}m": pd.Timedelta(minutes=n) for n in (1, 3, 5, 15, 30)},
            **{f"{n}h": pd.Timedelta(hours=n) for n in (1, 2, 4, 6, 8, 12)},
            "1d": pd.Timedelta(days=1),
        }
        for interval, delta in durations.items():
            with self.subTest(interval=interval):
                result = self.provider(Transport(Response([kline(start=BASE, end=BASE + delta)]))).fetch_history(self.request(
                    interval=interval, end=BASE + delta, as_of=BASE + delta,
                ))
                self.assertEqual(len(result.candles), 1)
        self.assertEqual(len(INTERVALS), len(durations) + 3)

    def test_429_honors_retry_after_and_weight_telemetry(self):
        transport = Transport(
            Response(status=429, headers={"Retry-After": "2"}),
            Response([kline(0)], headers={"X-MBX-USED-WEIGHT-1M": "50", "X-MBX-USED-WEIGHT-1D": "9" * 100}),
        )
        result = self.provider(transport).fetch_history(self.request(end=BASE + MINUTE))
        self.assertEqual(self.waits, [2])
        self.assertEqual(result.metadata.attrs["retry_wait_seconds"], 2)
        self.assertEqual(result.metadata.attrs["used_weight"]["x-mbx-used-weight-1m"], 50)
        self.assertNotIn("x-mbx-used-weight-1d", result.metadata.attrs["used_weight"])
        self.assertEqual(transport.calls[0][1]["params"], transport.calls[1][1]["params"])

    def test_excessive_or_invalid_retry_after_stops_without_early_retry(self):
        for value in ("31", "bad", "-1", "NaN", "inf", "Wed, 11 Nov 2026 00:00:00 GMT"):
            transport = Transport(Response(status=429, headers={"Retry-After": value}))
            with self.subTest(value=value), self.assertRaises(ProviderRateLimitError):
                self.provider(transport).fetch_history(self.request())
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(self.waits, [])

    def test_418_ban_never_retries(self):
        transport = Transport(Response(status=418, headers={"Retry-After": "120"}))
        with self.assertRaises(ProviderRateLimitError) as caught:
            self.provider(transport).fetch_history(self.request())
        self.assertEqual(caught.exception.status, 418)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.waits, [])

    def test_backoff_timeout_connection_and_server_errors(self):
        transport = Transport(
            requests.Timeout(), requests.ConnectionError(), Response(status=503), Response([kline(0)]),
        )
        self.provider(transport).fetch_history(self.request(end=BASE + MINUTE))
        self.assertEqual(self.waits, [0.5, 1, 2])
        self.assertEqual(len(transport.calls), 4)

    def test_retry_attempts_and_global_wait_budget_are_bounded(self):
        transport = Transport(Response(status=500), Response(status=500), Response(status=500))
        with self.assertRaises(ProviderHTTPError):
            self.provider(transport, max_retries=2).fetch_history(self.request())
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(self.waits, [0.5, 1])
        transport = Transport(Response(status=429, headers={"Retry-After": "1"}), Response([kline(0)]), Response(status=429, headers={"Retry-After": "1"}))
        with self.assertRaises(ProviderRateLimitError):
            self.provider(transport, retry_wait_budget=1).fetch_history(self.request())
        self.assertEqual(self.waits, [1])

    def test_bounded_backoff_and_exhausted_timeout_or_throttle(self):
        transport = Transport(Response(status=429), Response(status=429), Response(status=429), Response([kline(0)]))
        self.provider(transport, max_backoff=1).fetch_history(self.request(end=BASE + MINUTE))
        self.assertEqual(self.waits, [0.5, 1, 1])
        for failure in (requests.Timeout(), Response(status=429, headers={"Retry-After": "1"})):
            transport = Transport(failure, failure)
            with self.subTest(failure=type(failure)), self.assertRaises(ProviderError):
                self.provider(transport, max_retries=1).fetch_history(self.request())
            self.assertEqual(len(transport.calls), 2)

    def test_equity_and_binance_fixtures_share_reduction_not_calendar_assumptions(self):
        from chronochart.demo import aggregate_provider_fixtures

        with patch("requests.Session.get", side_effect=AssertionError("No live market HTTP allowed")):
            results = aggregate_provider_fixtures()
        equity, crypto = results["equity_fixture"], results["crypto_binance_mock"]
        # Existing calendar and fixed-UTC window anchors can use different pandas
        # storage units; compare actual instants and values at a common precision.
        assert_frame_equal(
            equity.candles.set_axis(equity.candles.index.as_unit("ns")),
            crypto.candles.set_axis(crypto.candles.index.as_unit("ns")),
        )
        self.assertEqual(equity.candles.iloc[0].tolist(), [1, 4, 0, 3.5, 300])
        self.assertTrue(equity.metadata.iloc[-1]["short_window"])
        self.assertTrue(equity.metadata.iloc[-1]["is_complete"])
        self.assertFalse(crypto.metadata.iloc[-1]["is_complete"])

    def test_page_pacing_and_resource_limits(self):
        transport = Transport(Response([kline(0)]), Response([kline(1)]))
        self.provider(transport, page_pause=0.25).fetch_history(self.request(end=BASE + 2 * MINUTE))
        self.assertEqual(self.waits, [0.25])
        with self.assertRaisesRegex(ProviderError, "max_pages"):
            self.provider(Transport(Response([kline(0)])), max_pages=1).fetch_history(self.request())
        with self.assertRaisesRegex(ProviderError, "max_rows"):
            self.provider(Transport(Response([kline(0), kline(1)])), max_rows=1).fetch_history(self.request())

    def test_restricted_regions_redirects_and_tls_fail_without_fallback(self):
        for response in (Response(status=403), Response(status=451), Response(status=302)):
            transport = Transport(response)
            with self.subTest(status=response.status_code), self.assertRaises(ProviderAccessError):
                self.provider(transport).fetch_history(self.request())
            self.assertEqual(len(transport.calls), 1)
        for failure in (requests.exceptions.SSLError(), requests.RequestException()):
            transport = Transport(failure)
            with self.subTest(error=type(failure)), self.assertRaises(ProviderError):
                self.provider(transport).fetch_history(self.request())
            self.assertEqual(self.waits, [])

    def test_http_and_api_errors_not_retried(self):
        for response in (
            Response({"code": -1121, "msg": "Invalid symbol"}, status=400),
            Response({"code": -1121, "msg": "Invalid symbol"}), Response(status=404, invalid_json=True),
        ):
            transport = Transport(response)
            with self.subTest(status=response.status_code), self.assertRaises(ProviderHTTPError):
                self.provider(transport).fetch_history(self.request())
            self.assertEqual(len(transport.calls), 1)

    def test_malformed_payloads_fail_without_retries(self):
        wrong_end = kline(0)
        wrong_end[6] -= 1
        bool_time = kline(0)
        bool_time[0] = True
        corrupt = [
            None, "html", [kline(0)[:6]], [wrong_end], [bool_time],
            [kline(0, values=["oops", "12", "9", "11", "100"])],
            [kline(0, values=["10", "12", "9", "11", "NaN"])],
            [kline(0, values=["10", "8", "9", "11", "100"])],
            [kline(0, values=[True, "12", "9", "11", "100"])],
            [kline(0, values=["10", "12", "9", "11", "-1"])],
        ]
        for payload in corrupt:
            transport = Transport(Response(payload))
            if payload is None:
                transport.responses[0].data = None
            with self.subTest(payload=payload), self.assertRaises(ProviderResponseError):
                self.provider(transport).fetch_history(self.request())
            self.assertEqual(len(transport.calls), 1)
        with self.assertRaises(ProviderResponseError):
            self.provider(Transport(Response(invalid_json=True))).fetch_history(self.request())
        with self.assertRaises(ProviderResponseError):
            self.provider(Transport(Response([kline(0), kline(1)])), page_size=1).fetch_history(self.request())

    def test_invalid_auxiliary_fields_and_calendar_alignment(self):
        for field, value in ((0, "1735689600000"), (6, True), (7, "bad"), (8, 1.5), (9, "-1"), (10, None)):
            row = kline(0)
            row[field] = value
            with self.subTest(field=field), self.assertRaises(ProviderResponseError):
                self.provider(Transport(Response([row]))).fetch_history(self.request())
        for interval, end in (("1w", BASE + pd.Timedelta(weeks=1)), ("1M", BASE + pd.DateOffset(months=1))):
            left = BASE + MINUTE
            with self.subTest(interval=interval), self.assertRaises(ProviderResponseError):
                self.provider(Transport(Response([kline(start=left, end=end)]))).fetch_history(self.request(interval=interval))

    def test_full_bar_may_extend_past_upper_request_horizon(self):
        left = pd.Timestamp("2099-12-30", tz="UTC")
        right = left + pd.Timedelta(days=3)
        limit = pd.Timestamp("2100-01-01", tz="UTC")
        result = self.provider(Transport(Response([kline(start=left, end=right)]))).fetch_history(self.request(
            interval="3d", start=left, end=limit, as_of=limit, forming="include",
        ))
        self.assertEqual(result.metadata.iloc[0]["bar_end"], right)
        self.assertTrue(result.metadata.iloc[0]["crosses_request_end"])

    def test_symbol_interval_and_options_validate_before_http(self):
        with self.assertRaises(ValueError):
            BinanceProvider(options=0)
        for symbol in ("btcusdt", "BTC/USDT", "A", "BTC_USDT", "A" * 31):
            transport = Transport()
            with self.subTest(symbol=symbol), self.assertRaises(ValueError):
                self.provider(transport).fetch_history(self.request(symbol=symbol))
            self.assertEqual(transport.calls, [])
        for interval in ("45m", "1H", "1mo", "daily"):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                self.provider(Transport()).fetch_history(self.request(interval=interval))
        for options in (dict(max_retries=6), dict(page_size=1001), dict(max_pages=0), dict(read_timeout=0), dict(backoff=31), dict(page_pause=float("inf")), dict(max_rows=True)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                BinanceOptions(**options)

    def test_common_request_boundaries_precision_and_immutability(self):
        for options in (
            dict(start=BASE.tz_localize(None)), dict(end=BASE), dict(as_of=pd.NaT),
            dict(start=BASE + pd.Timedelta(nanoseconds=1)), dict(forming="keep"), dict(symbol=""),
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.request(**options)
        request = self.request(start=BASE.tz_convert("America/New_York"), end=BASE + MINUTE)
        self.assertEqual(str(request.start.tz), "UTC")
        before = replace(request)
        self.provider(Transport(Response([kline(0)]))).fetch_history(request)
        self.assertEqual(request, before)

    def test_provider_protocol_and_owned_session_security_and_cleanup(self):
        transport = Transport(Response([kline(0)]))
        with patch("chronochart.binance.requests.Session", return_value=transport):
            with BinanceProvider() as provider:
                self.assertIsInstance(provider, HistoricalDataProvider)
                self.assertFalse(transport.trust_env)
                provider.fetch_history(self.request(end=BASE + MINUTE))
        self.assertTrue(transport.closed)
        injected = Transport()
        self.provider(injected).close()
        self.assertFalse(injected.closed)

    def test_optional_live_cli_path_uses_mocked_transport_only(self):
        from chronochart.cli import main

        transport = Transport(Response([kline(0)]))
        provider = self.provider(transport)
        output = io.StringIO()
        with patch("chronochart.binance.BinanceProvider", return_value=provider), redirect_stdout(output):
            code = main([
                "--live-binance-check", "--symbol", "BTCUSDT", "--interval", "1m",
                "--start", BASE.isoformat(), "--end", (BASE + MINUTE).isoformat(),
                "--as-of", (BASE + 2 * MINUTE).isoformat(),
            ])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 1 bars", output.getvalue())
        self.assertEqual(len(transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
