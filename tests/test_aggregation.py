"""Hand-calculated, provider-independent aggregation contracts."""

from dataclasses import replace
import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from chronochart.aggregation import OHLCV, CandleGroup, aggregate_candles
from chronochart.demo import aggregate_fixture


class AggregationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.times = pd.date_range("2025-01-02", periods=3, tz="UTC")
        self.rows = pd.DataFrame([
            [10, 13, 9, 12, 100],
            [12, 15, 11, 14, 200],
            [14, 16, 13, 15, 300],
        ], index=self.times, columns=OHLCV)
        self.group = CandleGroup(self.times[0], tuple(self.times), 3)

    def test_hand_calculated_fixture_and_completeness(self) -> None:
        result = aggregate_fixture()
        self.assertEqual(result.candles.to_numpy().tolist(), [
            [10, 16, 9, 15, 600], [15, 19, 14, 18, 900],
        ])
        self.assertEqual(result.metadata.to_numpy().tolist(), [
            [3, 3, True, True], [2, 3, False, True],
        ])

    def test_sorting_rows_and_members_does_not_change_open_close(self) -> None:
        rows = self.rows.iloc[[2, 0, 1]]
        before = rows.copy(deep=True)
        group = replace(self.group, members=tuple(reversed(self.times)))
        result = aggregate_candles(rows, [group])
        self.assertEqual(result.candles.iloc[0].tolist(), [10, 16, 9, 15, 600])
        assert_frame_equal(rows, before)
        self.assertEqual(group.members, tuple(reversed(self.times)))

    def test_single_row_groups(self) -> None:
        groups = [CandleGroup(timestamp, (timestamp,), 1) for timestamp in self.times]
        result = aggregate_candles(self.rows, groups)
        expected = self.rows.astype("float64").rename_axis("Timestamp")
        assert_frame_equal(result.candles, expected, check_freq=False)
        self.assertTrue(result.metadata["is_complete"].all())

    def test_output_uses_explicit_labels_not_first_source_timestamp(self) -> None:
        label = pd.Timestamp("2025-01-01", tz="UTC")
        result = aggregate_candles(self.rows, [replace(self.group, timestamp=label)])
        self.assertEqual(result.candles.index[0], label)
        self.assertEqual(result.candles.index.name, "Timestamp")
        self.assertEqual(str(result.candles.index.tz), "UTC")

    def test_naive_timestamps_are_preserved(self) -> None:
        rows = self.rows.tz_localize(None)
        group = CandleGroup(rows.index[0], tuple(rows.index), 3)
        self.assertIsNone(aggregate_candles(rows, [group]).candles.index.tz)

    def test_empty_input_returns_typed_frames(self) -> None:
        result = aggregate_candles(self.rows.iloc[:0], [])
        self.assertTrue(result.candles.empty)
        self.assertTrue(result.metadata.empty)
        self.assertEqual(tuple(result.candles.columns), OHLCV)
        self.assertTrue((result.candles.dtypes == "float64").all())
        self.assertEqual(str(result.candles.index.tz), "UTC")
        self.assertEqual(result.metadata["source_count"].dtype, "int64")
        self.assertEqual(result.metadata["is_complete"].dtype, "bool")

    def test_empty_schema_does_not_require_numeric_column_dtypes(self) -> None:
        empty = pd.DataFrame(columns=OHLCV, index=pd.DatetimeIndex([], tz="UTC"))
        result = aggregate_candles(empty, [])
        self.assertTrue(result.candles.empty)
        self.assertTrue((result.candles.dtypes == "float64").all())

    def test_empty_groups_report_metadata_without_synthetic_candles(self) -> None:
        empty = replace(self.group, members=())
        for policy in ("retain", "drop"):
            with self.subTest(policy=policy):
                result = aggregate_candles(self.rows.iloc[:0], [empty], incomplete=policy)
                self.assertTrue(result.candles.empty)
                self.assertEqual(result.metadata.iloc[0].tolist(), [0, 3, False, False])

    def test_drop_partial_fixture_keeps_completeness_metadata(self) -> None:
        result = aggregate_fixture(incomplete="drop")
        self.assertEqual(len(result.candles), 1)
        self.assertEqual(result.metadata.iloc[1].tolist(), [2, 3, False, False])

    def test_all_partial_groups_can_be_dropped(self) -> None:
        result = aggregate_candles(
            self.rows, [replace(self.group, expected_count=4)], incomplete="drop",
        )
        self.assertTrue(result.candles.empty)
        self.assertEqual(str(result.candles.index.tz), "UTC")
        self.assertEqual(result.metadata.iloc[0].tolist(), [3, 4, False, False])

    def test_partial_policy_applies_to_nonfinal_groups(self) -> None:
        groups = [
            CandleGroup(self.times[0], (self.times[0],), 2),
            CandleGroup(self.times[1], tuple(self.times[1:]), 2),
        ]
        result = aggregate_candles(self.rows, groups, incomplete="drop")
        self.assertEqual(list(result.candles.index), [self.times[1]])
        self.assertEqual(result.metadata["is_complete"].tolist(), [False, True])

    def test_non_utc_timezone_is_preserved(self) -> None:
        rows = self.rows.tz_convert("America/New_York")
        group = CandleGroup(rows.index[0], tuple(rows.index), 3)
        result = aggregate_candles(rows, [group])
        self.assertEqual(str(result.candles.index.tz), "America/New_York")

    def test_missing_and_nonfinite_values_rejected_even_when_dropped(self) -> None:
        for value in (np.nan, np.inf, -np.inf):
            for column in OHLCV:
                with self.subTest(value=value, column=column):
                    rows = self.rows.astype("float64")
                    rows.loc[self.times[0], column] = value
                    before = rows.copy(deep=True)
                    with self.assertRaisesRegex(ValueError, "Missing or non-finite"):
                        aggregate_candles(
                            rows, [replace(self.group, expected_count=4)], incomplete="drop",
                        )
                    assert_frame_equal(rows, before)

    def test_nullable_missing_values_rejected(self) -> None:
        rows = self.rows.astype("Float64")
        rows.loc[self.times[0], "Volume"] = pd.NA
        with self.assertRaisesRegex(ValueError, "Missing or non-finite"):
            aggregate_candles(rows, [self.group])

    def test_nonnumeric_boolean_and_complex_values_rejected(self) -> None:
        for dtype in ("str", "bool", "complex128"):
            with self.subTest(dtype=dtype):
                rows = self.rows.astype({"Open": dtype})
                with self.assertRaisesRegex(ValueError, "real numeric"):
                    aggregate_candles(rows, [self.group])

    def test_bad_ohlc_and_negative_volume_rejected(self) -> None:
        for column, value in (("High", 5), ("Low", 20), ("Volume", -1)):
            with self.subTest(column=column):
                rows = self.rows.copy(deep=True)
                rows.loc[self.times[0], column] = value
                with self.assertRaisesRegex(ValueError, "Invalid OHLC"):
                    aggregate_candles(rows, [self.group])

    def test_zero_volume_and_negative_prices_are_allowed(self) -> None:
        rows = self.rows.copy(deep=True)
        rows.loc[:, ["Open", "High", "Low", "Close"]] -= 100
        rows["Volume"] = 0
        result = aggregate_candles(rows, [self.group])
        self.assertEqual(result.candles.iloc[0].tolist(), [-90, -84, -91, -85, 0])

    def test_volume_overflow_rejected(self) -> None:
        rows = self.rows.astype("float64")
        rows["Volume"] = 1e308
        with self.assertRaisesRegex(ValueError, "overflows"):
            aggregate_candles(rows, [self.group])

    def test_invalid_schema_and_index_rejected(self) -> None:
        invalid = [
            self.rows.reset_index(drop=True),
            self.rows.drop(columns="Open"),
            self.rows.rename(columns={"Volume": "Close"}),
            pd.concat([self.rows, self.rows.iloc[:1]]),
            self.rows.set_axis(pd.DatetimeIndex([self.times[0], self.times[1], pd.NaT])),
        ]
        for rows in invalid:
            with self.subTest(index=repr(rows.index), columns=list(rows.columns)):
                with self.assertRaises(ValueError):
                    aggregate_candles(rows, [self.group])

    def test_unknown_repeated_and_unassigned_members_rejected(self) -> None:
        invalid = [
            replace(self.group, members=(self.times[0], self.times[1])),
            replace(self.group, members=(self.times[0], self.times[0], self.times[2])),
            replace(self.group, members=(*self.times[:2], pd.Timestamp("2030-01-01", tz="UTC"))),
        ]
        for group in invalid:
            with self.subTest(group=group), self.assertRaises(ValueError):
                aggregate_candles(self.rows, [group])
        with self.assertRaisesRegex(ValueError, "Every source row"):
            aggregate_candles(self.rows, [])
        with self.assertRaisesRegex(ValueError, "Repeated group membership"):
            aggregate_candles(self.rows, [
                self.group, CandleGroup(self.times[-1], (self.times[-1],), 1),
            ])

    def test_bad_expected_counts_rejected(self) -> None:
        for count in (0, -1, True, 3.0, 2, 2**63):
            with self.subTest(count=count), self.assertRaises(ValueError):
                aggregate_candles(self.rows, [replace(self.group, expected_count=count)])

    def test_malformed_groups_and_members_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "CandleGroup"):
            aggregate_candles(self.rows, [object()])
        with self.assertRaisesRegex(ValueError, "members must be a tuple"):
            aggregate_candles(self.rows, [replace(self.group, members=None)])

    def test_invalid_policy_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "incomplete"):
            aggregate_candles(self.rows, [self.group], incomplete="ignore")

    def test_group_timestamp_types_nat_and_timezones_rejected(self) -> None:
        for timestamp in ("2025-01-01", pd.NaT, pd.Timestamp("2025-01-01")):
            for field in ("timestamp", "members"):
                group = replace(self.group, **{
                    field: timestamp if field == "timestamp" else (timestamp, *self.times[1:]),
                })
                with self.subTest(timestamp=timestamp, field=field), self.assertRaises(ValueError):
                    aggregate_candles(self.rows, [group])

    def test_group_labels_must_be_unique_and_sorted(self) -> None:
        first = CandleGroup(self.times[0], (self.times[0],), 1)
        second = CandleGroup(self.times[1], tuple(self.times[1:]), 2)
        for groups in ([second, first], [first, replace(second, timestamp=first.timestamp)]):
            with self.subTest(groups=groups), self.assertRaisesRegex(ValueError, "strictly increasing"):
                aggregate_candles(self.rows, groups)

    def test_extra_columns_ignored_and_all_inputs_unchanged(self) -> None:
        rows = self.rows.assign(provider_note="untouched")
        before = rows.copy(deep=True)
        groups = [self.group]
        result = aggregate_candles(rows, groups)
        result.candles.iloc[0, 0] = 999
        result.metadata.iloc[0, 0] = 999
        assert_frame_equal(rows, before)
        self.assertEqual(groups, [self.group])

    def test_repeated_calls_are_deterministic(self) -> None:
        first = aggregate_candles(self.rows, [self.group])
        second = aggregate_candles(self.rows, [self.group])
        assert_frame_equal(first.candles, second.candles)
        assert_frame_equal(first.metadata, second.metadata)


if __name__ == "__main__":
    unittest.main()
