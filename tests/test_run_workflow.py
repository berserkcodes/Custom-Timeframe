"""Offline end-to-end run/export examples and argument validation."""

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
from pandas.testing import assert_frame_equal

from chronochart.application import JobSpec, execute_job, resolve_job
from chronochart.cli import main
from chronochart.storage import EXPORT_SCHEMA, export_bundle, load_export, read_json


class RunWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def spec(self, **kwargs):
        options = dict(provider="fixture-equity", symbol="AAPL", start="2024-01-01", end="2024-05-01",
                       as_of="2024-06-01T00:00:00Z", source_interval="1d", target_mode="trading-sessions",
                       target_amount=10, target_unit="sessions", output=str(self.root / "out.json"))
        options.update(kwargs)
        return JobSpec(**options)

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(args))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def args(self, spec):
        args = ["run"]
        for name in ("provider", "symbol", "start", "end", "as_of", "source_interval", "target_mode", "target_amount", "target_unit", "output"):
            args.extend(["--" + name.replace("_", "-"), str(getattr(spec, name))])
        return args

    def test_five_requested_cli_examples_export_and_reload_offline(self):
        examples = [
            self.spec(output=str(self.root / "aapl10.json")),
            self.spec(target_amount=15, output=str(self.root / "aapl15.json")),
            self.spec(target_mode="calendar", target_amount=3, target_unit="weeks", output=str(self.root / "aapl3weeks.json")),
            self.spec(target_mode="calendar", target_amount=2, target_unit="months", output=str(self.root / "aapl2months.json")),
            self.spec(provider="fixture-crypto", symbol="BTCUSDT", start="2024-02-29", end="2024-03-01",
                      as_of="2024-03-02T00:00:00Z", source_interval="15m", target_mode="intraday",
                      target_amount=45, target_unit="minutes", output=str(self.root / "crypto45.json")),
        ]
        with patch("requests.sessions.Session.request", side_effect=AssertionError("offline means no HTTP")), \
             patch("yfinance.Ticker", side_effect=AssertionError("offline means no live equity")):
            for spec in examples:
                with self.subTest(target=(spec.target_mode, spec.target_amount)):
                    code, out, err = self.cli(*self.args(spec))
                    self.assertEqual(code, 0, err)
                    self.assertIn("SYNTHETIC OFFLINE FIXTURE", out)
                    loaded = load_export(spec.output)
                    self.assertFalse(loaded.result.candles.empty)
                    self.assertTrue(loaded.provenance["synthetic"])
                    self.assertTrue({"is_complete", "retained", "expected_count"}.issubset(loaded.result.metadata))
                    self.assertTrue({"bar_end", "is_closed"}.issubset(loaded.source_metadata))
                    self.assertEqual(read_json(Path(spec.output))["schema"], EXPORT_SCHEMA)
        self.assertEqual(len(load_export(examples[-1].output).result.candles), 32)

    def test_roundtrip_preserves_dtypes_tuples_attrs_and_all_metadata(self):
        spec = self.spec(partial_policy="drop")
        bundle = execute_job(spec)
        loaded = load_export(spec.output)
        assert_frame_equal(bundle.result.candles, loaded.result.candles, check_freq=False)
        assert_frame_equal(bundle.result.metadata, loaded.result.metadata, check_freq=False)
        assert_frame_equal(bundle.source_metadata, loaded.source_metadata, check_freq=False)
        self.assertEqual(bundle.provenance, loaded.provenance)
        self.assertGreater(len(loaded.result.metadata), len(loaded.result.candles))
        self.assertIsInstance(loaded.result.metadata.iloc[0]["missing_sessions"], tuple)
        self.assertEqual(loaded.result.metadata["is_complete"].dtype, "bool")

    def test_output_never_overwrites_without_opt_in(self):
        spec = self.spec()
        execute_job(spec)
        before = Path(spec.output).read_bytes()
        with self.assertRaises(FileExistsError):
            execute_job(spec)
        self.assertEqual(Path(spec.output).read_bytes(), before)
        execute_job(replace(spec, overwrite=True))
        self.assertTrue(load_export(spec.output).provenance["synthetic"])
        self.assertEqual(list(self.root.glob(".chronochart-*")), [])

    def test_invalid_arguments_fail_before_fetch_or_export(self):
        invalid = [
            dict(target_amount=0), dict(target_mode="calendar", target_unit="weeks", target_amount=1),
            dict(target_unit="weeks"), dict(calendar="24/7"), dict(timezone="UTC"),
            dict(anchor="2024-01-01"),  # NYSE holiday cannot be a session anchor
            dict(end="2023-01-01"), dict(as_of="2024-06-01"), dict(symbol="aapl"),
            dict(source_interval="1M"), dict(cache_refresh=True), dict(cache_ttl=0), dict(cache_dir=""),
            dict(output=str(self.root / "not-a-csv.csv")),
            dict(provider="yfinance", source_interval="15m"),
            dict(provider="binance", symbol="BTCUSDT", source_interval="1d", target_mode="intraday", target_amount=45, target_unit="minutes"),
        ]
        with patch("requests.sessions.Session.request", side_effect=AssertionError("must validate before HTTP")), \
             patch("yfinance.Ticker", side_effect=AssertionError("must validate before equity HTTP")):
            for changes in invalid:
                with self.subTest(changes=changes), self.assertRaises((ValueError, OSError)):
                    execute_job(self.spec(**changes))
        self.assertFalse((self.root / "out.json").exists())

    def test_cli_invalid_mode_details_and_mixed_legacy_flags(self):
        code, _, err = self.cli(*self.args(self.spec(target_amount=0)))
        self.assertEqual(code, 2)
        self.assertIn("positive", err)
        code, _, _ = self.cli("--demo-aggregation", *self.args(self.spec()))
        self.assertEqual(code, 2)
        code, out, _ = self.cli("run", "--help")
        self.assertEqual(code, 0)
        for option in ("--provider", "--anchor", "--cache-ttl", "--output", "--partial-policy"):
            self.assertIn(option, out)

    def test_custom_session_anchor_stable_across_queries(self):
        spec = self.spec(anchor="2024-01-02")
        first = execute_job(spec)
        second = execute_job(replace(spec, start="2024-01-05", output=str(self.root / "short.json")))
        self.assertEqual(first.result.metadata.attrs["anchor"], pd.Timestamp("2024-01-02", tz="UTC"))
        for label in second.result.metadata.index:
            self.assertIn(label, first.result.metadata.index)
        complete = second.result.metadata.index[second.result.metadata["is_complete"]]
        assert_frame_equal(first.result.candles.loc[complete], second.result.candles.loc[complete])

    def test_requests_before_custom_session_anchor_keep_signed_bucket_phase(self):
        result = execute_job(self.spec(anchor="2024-01-16"))
        self.assertEqual(result.result.metadata.index[0], pd.Timestamp("2023-12-29", tz="UTC"))
        self.assertIn(pd.Timestamp("2024-01-16", tz="UTC"), result.result.metadata.index)

    def test_calendar_anchor_alignment_and_validation(self):
        spec = self.spec(target_mode="calendar", target_amount=3, target_unit="weeks", anchor="2024-01-01")
        result = execute_job(spec)
        self.assertEqual(result.result.candles.index[0], pd.Timestamp("2024-01-01T05:00:00Z"))
        with self.assertRaisesRegex(ValueError, "Monday"):
            resolve_job(replace(spec, anchor="2024-01-02", output=str(self.root / "bad.json")))
        months = execute_job(replace(spec, target_amount=2, target_unit="months", anchor="2000-02-01", output=str(self.root / "months.json")))
        self.assertIn(pd.Timestamp("2024-02-01T05:00:00Z"), months.result.metadata.index)
        with self.assertRaisesRegex(ValueError, "first"):
            resolve_job(replace(spec, target_amount=2, target_unit="months", anchor="2000-02-02", output=str(self.root / "bad.json")))

    def test_crypto_custom_anchor_and_detail_rejection(self):
        spec = self.spec(provider="fixture-crypto", symbol="BTCUSDT", source_interval="15m", target_mode="intraday",
                         target_amount=45, target_unit="minutes", start="2024-02-29T00:15:00Z",
                         end="2024-02-29T01:45:00Z", anchor="2000-01-01T00:15:00Z")
        result = execute_job(spec)
        self.assertEqual(list(result.result.candles.index), [pd.Timestamp("2024-02-29T00:15:00Z"), pd.Timestamp("2024-02-29T01:00:00Z")])
        with self.assertRaisesRegex(ValueError, "straddles"):
            resolve_job(replace(spec, anchor="2000-01-01T00:05:00Z", output=str(self.root / "bad.json")))
        with self.assertRaisesRegex(ValueError, "anchor"):
            resolve_job(replace(spec, anchor="1000-01-01T00:00:00Z", output=str(self.root / "bad.json")))

    def test_export_reload_rejects_unknown_schema_and_truthy_flag_strings(self):
        bundle = execute_job(self.spec())
        path = self.root / "bad.json"
        value = read_json(self.root / "out.json")
        value["schema"] = "future-version"
        import json
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_export(path)
        value = read_json(self.root / "out.json")
        position = value["metadata"]["columns"].index("is_complete")
        value["metadata"]["rows"][0][position] = "False"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dtype"):
            load_export(path)
        with self.assertRaises(FileExistsError):
            export_bundle(self.root / "out.json", bundle)

    def test_holiday_empty_export_is_typed_and_reloadable(self):
        result = execute_job(self.spec(
            source_interval="15m", target_mode="intraday", target_amount=45, target_unit="minutes",
            start="2025-07-04", end="2025-07-05", as_of="2025-07-07T00:00:00Z", anchor="session-open",
        ))
        loaded = load_export(self.root / "out.json")
        self.assertTrue(loaded.result.candles.empty)
        assert_frame_equal(result.result.metadata, loaded.result.metadata)

    def test_live_yfinance_cli_workflow_is_mocked_not_a_live_check(self):
        from chronochart.local_providers import FixtureProvider
        from chronochart.providers import HistoricalRequest
        from unittest.mock import Mock

        spec = self.spec(provider="yfinance")
        source = FixtureProvider("equity").fetch_history(HistoricalRequest(
            "AAPL", "1d", pd.Timestamp(spec.start, tz="UTC"), pd.Timestamp(spec.end, tz="UTC"), pd.Timestamp(spec.as_of),
        )).candles
        raw = source.copy()
        raw.index = raw.index.tz_localize(None).tz_localize("America/New_York")
        ticker = Mock()
        ticker.history.return_value = raw
        with patch("yfinance.Ticker", return_value=ticker):
            code, out, err = self.cli(*self.args(spec))
        self.assertEqual(code, 0, err)
        self.assertIn("provider data", out)
        self.assertFalse(load_export(spec.output).provenance["synthetic"])

    def test_binance_run_fetch_normalize_aggregate_export_with_mock_transport(self):
        from chronochart.binance import BinanceProvider

        start = pd.Timestamp("2024-02-29", tz="UTC")
        index = pd.date_range(start, periods=6, freq="15min")
        rows = [[stamp.value // 1_000_000, "10", "12", "9", "11", "100",
                 (stamp + pd.Timedelta(minutes=15)).value // 1_000_000 - 1,
                 "0", 1, "0", "0", "0"] for stamp in index]

        class Response:
            status_code = 200
            headers = {}

            def json(self):
                return rows

        class Transport:
            def get(self, *args, **kwargs):
                return Response()

        provider = BinanceProvider(transport=Transport())
        spec = self.spec(provider="binance", symbol="BTCUSDT", source_interval="15m", target_mode="intraday",
                         target_amount=45, target_unit="minutes", start=start.isoformat(),
                         end=(start + pd.Timedelta(minutes=90)).isoformat(), as_of="2024-03-02T00:00:00Z")
        with patch("chronochart.binance.BinanceProvider", return_value=provider):
            code, _, err = self.cli(*self.args(spec))
        self.assertEqual(code, 0, err)
        data = load_export(spec.output)
        self.assertEqual(data.result.candles["Volume"].tolist(), [300, 300])
        self.assertEqual(data.provenance["provider_identity"]["name"], "binance")
