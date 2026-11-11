"""Provider-independent canonical normalization; all tests are offline."""

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from chronochart.aggregation import OHLCV
from chronochart.normalization import normalize_ohlcv


class NormalizationTests(unittest.TestCase):
    def frame(self):
        return pd.DataFrame(
            [["10", "12", "9", "11", "100"], ["11", "14", "10", "13", "200"]],
            index=pd.date_range("2025-01-01", periods=2, tz="America/New_York"), columns=OHLCV,
        )

    def test_numeric_conversion_utc_sort_and_purity(self):
        raw = self.frame().iloc[::-1]
        before = raw.copy(deep=True)
        result = normalize_ohlcv(raw)
        self.assertEqual(str(result.index.tz), "UTC")
        self.assertEqual(str(result.index.dtype), "datetime64[ns, UTC]")
        self.assertTrue(result.index.is_monotonic_increasing)
        self.assertTrue((result.dtypes == "float64").all())
        result.iloc[0, 0] = 999
        assert_frame_equal(raw, before)

    def test_identical_duplicates_collapse_but_conflicts_raise(self):
        raw = self.frame()
        result = normalize_ohlcv(pd.concat([raw, raw.iloc[:1]]))
        self.assertEqual(len(result), 2)
        self.assertEqual(result.attrs["duplicates_removed"], 1)
        changed = raw.iloc[:1].copy()
        changed.iloc[0, 3] = "11.5"
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            normalize_ohlcv(pd.concat([raw, changed]))

    def test_missing_nonfinite_boolean_and_bad_ranges(self):
        for value in (np.nan, pd.NA, "inf", True, 1j, "bad", "-1"):
            raw = self.frame().astype("object")
            raw.iloc[0, 4] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_ohlcv(raw)
        raw = self.frame()
        raw.iloc[0, 1] = "8"
        with self.assertRaises(ValueError):
            normalize_ohlcv(raw)

    def test_empty_schema_preserves_types(self):
        result = normalize_ohlcv(self.frame().iloc[:0])
        self.assertTrue(result.empty)
        self.assertTrue((result.dtypes == "float64").all())
        self.assertEqual(str(result.index.tz), "UTC")

    def test_invalid_schema_or_naive_index_is_not_guessed(self):
        for raw in (self.frame().tz_localize(None), self.frame().drop(columns="Open"), self.frame().reset_index(drop=True)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_ohlcv(raw)


if __name__ == "__main__":
    unittest.main()
