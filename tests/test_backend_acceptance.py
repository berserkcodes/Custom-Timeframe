"""Independent cross-layer acceptance checks for the frontend-facing export contract."""

from pathlib import Path
import tempfile
import unittest

import pandas as pd

from chronochart.aggregation import OHLCV
from chronochart.application import JobSpec, execute_job
from chronochart.providers import MarketDataResult
from chronochart.storage import atomic_json, export_bundle, load_export, read_json


class BackendAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "hand.json"

    def bundle(self):
        index = pd.date_range("2024-02-29", periods=6, freq="15min", tz="UTC").as_unit("ns")
        candles = pd.DataFrame([
            [10, 13, 9, 12, 100], [12, 15, 11, 14, 200], [14, 16, 13, 15, 300],
            [15, 18, 14, 17, 400], [17, 19, 16, 18, 500], [18, 22, 17, 21, 600],
        ], index=index, columns=OHLCV, dtype="float64")
        candles.attrs.update(synthetic=True, provider="hand-calculated-acceptance")
        info = pd.DataFrame(index=index)
        info["bar_end"] = pd.Series(index + pd.Timedelta(minutes=15), index=index, dtype="datetime64[ns, UTC]")
        info["is_closed"], info["crosses_request_end"] = True, False

        class Provider:
            def fetch_history(self, request):
                return MarketDataResult(candles.copy(deep=True), info.copy(deep=True))

        spec = JobSpec(
            "fixture-crypto", "BTCUSDT", "2024-02-29T00:00:00Z", "2024-02-29T01:30:00Z",
            "2024-03-02T00:00:00Z", "15m", "intraday", 45, "minutes", str(self.output),
        )
        return execute_job(spec, provider_override=(Provider(), {"name": "hand-acceptance", "revision": 1}))

    def test_independent_hand_calculation_through_normalize_aggregate_export_reload(self):
        self.bundle()
        result = load_export(self.output)
        self.assertEqual(result.result.candles.to_numpy().tolist(), [
            [10, 16, 9, 15, 600], [15, 22, 14, 21, 1500],
        ])
        self.assertTrue(result.result.metadata["is_complete"].all())
        self.assertEqual(int(result.result.metadata["source_count"].sum()), len(result.source_metadata))
        self.assertTrue(result.provenance["synthetic"])

    def test_reader_rejects_financially_invalid_ohlcv_even_when_json_types_are_valid(self):
        self.bundle()
        original = read_json(self.output)
        for column, value in (("Volume", -1), ("High", 1), ("Low", 1000)):
            payload = read_json(self.output)
            position = payload["candles"]["columns"].index(column)
            payload["candles"]["rows"][0][position] = value
            path = self.root / f"bad-{column}.json"
            atomic_json(path, payload, overwrite=False)
            with self.subTest(column=column), self.assertRaises(ValueError):
                load_export(path)
        self.assertEqual(read_json(self.output), original)

    def test_reader_rejects_retention_completeness_and_source_maturity_contradictions(self):
        self.bundle()
        for frame, column, value in (
            ("metadata", "retained", False), ("metadata", "is_complete", False),
            ("metadata", "source_count", 4), ("source_metadata", "is_closed", False),
        ):
            payload = read_json(self.output)
            position = payload[frame]["columns"].index(column)
            payload[frame]["rows"][0][position] = value
            path = self.root / f"bad-{frame}-{column}.json"
            atomic_json(path, payload, overwrite=False)
            with self.subTest(frame=frame, column=column), self.assertRaises(ValueError):
                load_export(path)

    def test_reader_rejects_non_dictionary_provenance(self):
        self.bundle()
        payload = read_json(self.output)
        payload["provenance"] = ["not", "a", "contract"]
        path = self.root / "bad-provenance.json"
        atomic_json(path, payload, overwrite=False)
        with self.assertRaises(ValueError):
            load_export(path)

    def test_writer_rejects_semantically_invalid_bundle_without_creating_output(self):
        bundle = self.bundle()
        bundle.result.metadata.iloc[0, bundle.result.metadata.columns.get_loc("retained")] = False
        destination = self.root / "invalid.json"
        with self.assertRaises(ValueError):
            export_bundle(destination, bundle)
        self.assertFalse(destination.exists())

    def test_reader_requires_explicit_request_and_synthetic_provenance(self):
        self.bundle()
        for field in ("request", "synthetic"):
            payload = read_json(self.output)
            del payload["provenance"][field]
            path = self.root / f"missing-{field}.json"
            atomic_json(path, payload, overwrite=False)
            with self.subTest(field=field), self.assertRaises(ValueError):
                load_export(path)

    def test_optional_flags_missing_lists_and_maturity_must_obey_their_contract(self):
        self.bundle()
        for variant in ("flag-type", "missing-list", "maturity"):
            payload = read_json(self.output)
            table = payload["metadata"]
            if variant == "flag-type":
                position = table["columns"].index("coverage_complete")
                table["dtypes"][position] = "object"
                table["rows"][0][position] = "False"
            elif variant == "missing-list":
                position = table["columns"].index("missing_bars")
                table["rows"][0][position] = {"$tuple": [{"$timestamp": "2024-02-29T00:00:00+00:00"}]}
            else:
                table["rows"][0][table["columns"].index("window_closed")] = False
                table["rows"][0][table["columns"].index("is_complete")] = False
            path = self.root / f"bad-{variant}.json"
            atomic_json(path, payload, overwrite=False)
            with self.subTest(variant=variant), self.assertRaises(ValueError):
                load_export(path)


if __name__ == "__main__":
    unittest.main()
