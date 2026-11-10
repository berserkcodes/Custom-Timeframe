"""Calendar expectations and stable session windows, independent of providers."""

import unittest

import exchange_calendars as xcals
import pandas as pd
from pandas.testing import assert_frame_equal, assert_index_equal

from chronochart.aggregation import OHLCV
from chronochart.pipeline import aggregate_trading_sessions
from chronochart.timeframes import (
    ANCHOR, MissingSessionsError, TradingSessionTimeframe, build_trading_session_groups,
)


class TradingSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.calendar = xcals.get_calendar("XNYS", start="2000-01-01", end="2026-12-31")

    def sessions(self, start: str, end: str) -> pd.DatetimeIndex:
        return self.calendar.sessions_in_range(start, end).tz_localize("UTC")

    def plan(self, index=None, count=10, **kwargs):
        if index is None:
            index = self.sessions("2000-01-03", "2000-01-31")
        options = dict(start="2000-01-03", end="2000-01-31", as_of=pd.Timestamp("2000-03-01", tz="UTC"))
        options.update(kwargs)
        return build_trading_session_groups(index, TradingSessionTimeframe(count), **options)

    def candles(self, index):
        return pd.DataFrame([[10, 13, 9, 12, 100]] * len(index), index=index, columns=OHLCV)

    def test_counts_and_prototype_alias(self):
        for count in (1, 7, 10, 15, 30):
            self.assertEqual(TradingSessionTimeframe.parse(f"{count} trading sessions").sessions, count)
        self.assertEqual(TradingSessionTimeframe.parse("3 trading weeks"), TradingSessionTimeframe(15))
        self.assertEqual(TradingSessionTimeframe.parse("1 trading session").sessions, 1)

    def test_reject_calendar_units_and_invalid_timeframes(self):
        for value in ("3 weeks", "3 calendar weeks", "10 days", "10 sessions", "0 trading sessions", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TradingSessionTimeframe.parse(value)
        for count in (0, -1, True, 10.0, "10", 2**63):
            with self.subTest(count=count), self.assertRaises(ValueError):
                TradingSessionTimeframe(count)

    def test_ten_session_hand_checked_anchor_boundaries(self):
        plan = self.plan()
        self.assertEqual([g.timestamp for g in plan.groups], [
            ANCHOR, pd.Timestamp("2000-01-18", tz="UTC"),
        ])
        self.assertEqual(plan.metadata["window_end"].tolist(), [
            pd.Timestamp("2000-01-14", tz="UTC"), pd.Timestamp("2000-01-31", tz="UTC"),
        ])
        self.assertTrue(plan.metadata["is_complete"].all())
        self.assertEqual(plan.metadata.attrs["exchange"], "XNYS")
        self.assertEqual(plan.metadata.attrs["calendar_version"], "4.13.2")

    def test_fifteen_session_alias_boundary_skips_mlk_holiday(self):
        index = self.sessions("2000-01-03", "2000-01-24")
        timeframe = TradingSessionTimeframe.parse("3 trading weeks")
        plan = build_trading_session_groups(
            index, timeframe, start="2000-01-03", end="2000-01-24",
            as_of=pd.Timestamp("2000-02-01", tz="UTC"),
        )
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(len(plan.groups[0].members), 15)
        self.assertEqual(plan.metadata.iloc[0]["window_end"], pd.Timestamp("2000-01-24", tz="UTC"))
        self.assertTrue(plan.metadata.iloc[0]["is_complete"])
        self.assertNotIn(pd.Timestamp("2000-01-17", tz="UTC"), plan.groups[0].members)

    def test_missing_session_does_not_shift_later_windows(self):
        full = self.plan()
        absent = pd.Timestamp("2000-01-10", tz="UTC")
        index = self.sessions("2000-01-03", "2000-01-31").drop(absent)
        result = self.plan(index, missing="retain")
        self.assertEqual([g.timestamp for g in result.groups], [g.timestamp for g in full.groups])
        self.assertEqual(result.groups[1], full.groups[1])
        self.assertEqual(result.metadata.iloc[0]["missing_sessions"], (absent,))
        self.assertFalse(result.metadata.iloc[0]["coverage_complete"])
        self.assertEqual(result.metadata.iloc[0]["expected_count"], 10)
        with self.assertRaises(MissingSessionsError) as caught:
            self.plan(index)
        self.assertEqual(caught.exception.sessions, (absent,))

    def test_missing_drop_omits_candle_not_metadata(self):
        index = self.sessions("2000-01-03", "2000-01-31").delete(3)
        result = aggregate_trading_sessions(
            self.candles(index), TradingSessionTimeframe(10), start="2000-01-03", end="2000-01-31",
            as_of=pd.Timestamp("2000-03-01", tz="UTC"), missing="drop",
        )
        self.assertEqual(list(result.candles.index), [pd.Timestamp("2000-01-18", tz="UTC")])
        self.assertEqual(result.metadata["retained"].tolist(), [False, True])

    def test_truncated_query_flags_edges_not_missing_coverage(self):
        index = self.sessions("2000-01-05", "2000-01-12")
        plan = self.plan(index, start="2000-01-05", end="2000-01-12")
        info = plan.metadata.iloc[0]
        self.assertEqual(plan.groups[0].timestamp, ANCHOR)
        self.assertTrue(info["leading_partial"])
        self.assertTrue(info["trailing_partial"])
        self.assertTrue(info["window_closed"])
        self.assertTrue(info["coverage_complete"])
        self.assertFalse(info["is_complete"])
        self.assertEqual(info["missing_sessions"], ())
        self.assertEqual(info["requested_count"], len(index))
        self.assertEqual(info["expected_count"], 10)

    def test_overlapping_queries_have_identical_windows_and_full_interior_candles(self):
        full_index = self.sessions("2000-01-03", "2000-02-11")
        subset = self.sessions("2000-01-10", "2000-02-04")
        full = self.plan(full_index, end="2000-02-11")
        short = self.plan(subset, start="2000-01-10", end="2000-02-04")
        for label in short.metadata.index:
            self.assertEqual(short.metadata.loc[label, "window_end"], full.metadata.loc[label, "window_end"])
        options = dict(timeframe=TradingSessionTimeframe(10), as_of=pd.Timestamp("2000-03-01", tz="UTC"))
        full_output = aggregate_trading_sessions(self.candles(full_index), start="2000-01-03", end="2000-02-11", **options)
        short_output = aggregate_trading_sessions(self.candles(subset), start="2000-01-10", end="2000-02-04", **options)
        interior = pd.Timestamp("2000-01-18", tz="UTC")
        assert_frame_equal(full_output.candles.loc[[interior]], short_output.candles.loc[[interior]])

    def test_independent_leading_trailing_drop_policies(self):
        index = self.sessions("2000-01-05", "2000-02-04")
        for leading, trailing, retained in (
            ("retain", "retain", [True, True, True]),
            ("drop", "retain", [False, True, True]),
            ("retain", "drop", [True, True, False]),
            ("drop", "drop", [False, True, False]),
        ):
            with self.subTest(leading=leading, trailing=trailing):
                plan = self.plan(index, start="2000-01-05", end="2000-02-04", leading=leading, trailing=trailing)
                self.assertEqual(plan.metadata["retained"].tolist(), retained)
                self.assertEqual(sum(len(g.members) for g in plan.groups), len(index))

    def test_weekend_only_request_is_typed_empty(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        plan = self.plan(empty, start="2000-01-08", end="2000-01-09")
        self.assertEqual(plan.groups, ())
        self.assertTrue(plan.metadata.empty)
        self.assertEqual(plan.metadata["source_count"].dtype, "int64")
        result = aggregate_trading_sessions(
            self.candles(empty), TradingSessionTimeframe(10), start="2000-01-08", end="2000-01-09",
            as_of=pd.Timestamp("2000-03-01", tz="UTC"),
        )
        self.assertTrue(result.candles.empty)

    def test_holiday_only_request_is_empty_not_missing(self):
        index = pd.DatetimeIndex([], tz="UTC")
        self.assertTrue(self.plan(index, start="2025-07-04", end="2025-07-04").metadata.empty)

    def test_wholly_missing_closed_window_has_metadata_no_synthetic_prices(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        with self.assertRaises(MissingSessionsError):
            self.plan(empty)
        result = aggregate_trading_sessions(
            self.candles(empty), TradingSessionTimeframe(10), start="2000-01-03", end="2000-01-31",
            as_of=pd.Timestamp("2000-03-01", tz="UTC"), missing="retain",
        )
        self.assertTrue(result.candles.empty)
        self.assertEqual(result.metadata["source_count"].tolist(), [0, 0])
        self.assertEqual([len(x) for x in result.metadata["missing_sessions"]], [10, 10])
        self.assertFalse(result.metadata["retained"].any())

    def test_unfinished_and_missing_are_independent(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        plan = self.plan(
            empty, 1, start="2025-07-02", end="2025-07-03",
            as_of=pd.Timestamp("2025-07-03T16:00:00Z"), missing="retain",
        )
        first, second = plan.metadata.iloc[0], plan.metadata.iloc[1]
        self.assertTrue(first["window_closed"])
        self.assertFalse(first["coverage_complete"])
        self.assertFalse(second["window_closed"])
        self.assertTrue(second["coverage_complete"])
        self.assertEqual(second["missing_sessions"], ())
        self.assertEqual(second["pending_sessions"], (pd.Timestamp("2025-07-03", tz="UTC"),))

    def test_early_close_becomes_expected_exactly_at_close(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        options = dict(start="2025-07-03", end="2025-07-03")
        before = self.plan(empty, 1, as_of=pd.Timestamp("2025-07-03T16:59:59Z"), **options)
        self.assertFalse(before.metadata.iloc[0]["window_closed"])
        self.assertEqual(before.metadata.iloc[0]["window_close"], pd.Timestamp("2025-07-03T17:00:00Z"))
        with self.assertRaises(MissingSessionsError):
            self.plan(empty, 1, as_of=pd.Timestamp("2025-07-03T17:00:00Z"), **options)

    def test_forming_source_candle_not_reported_complete(self):
        index = pd.DatetimeIndex(["2025-07-03"], tz="UTC")
        for policy in ("retain", "drop"):
            result = aggregate_trading_sessions(
                self.candles(index), TradingSessionTimeframe(1), start="2025-07-03", end="2025-07-03",
                as_of=pd.Timestamp("2025-07-03T16:00:00Z"), unfinished=policy,
            )
            self.assertTrue(result.metadata.iloc[0]["count_complete"])
            self.assertFalse(result.metadata.iloc[0]["is_complete"])
            self.assertEqual(len(result.candles), int(policy == "retain"))

    def test_dst_changes_actual_close_instant(self):
        for date, close in (("2025-03-07", "21:00:00"), ("2025-03-10", "20:00:00")):
            with self.subTest(date=date):
                index = pd.DatetimeIndex([date], tz="UTC")
                plan = self.plan(index, 1, start=date, end=date, as_of=pd.Timestamp("2025-03-11", tz="UTC"))
                self.assertEqual(plan.metadata.iloc[0]["window_close"], pd.Timestamp(f"{date}T{close}Z"))

    def test_special_exchange_closures_are_not_missing(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        self.assertTrue(self.plan(empty, start="2001-09-11", end="2001-09-14").metadata.empty)

    def test_invalid_source_labels(self):
        valid = self.sessions("2000-01-03", "2000-01-31")
        invalid = [
            valid.tz_localize(None), valid.tz_convert("America/New_York"),
            valid + pd.Timedelta(hours=1), valid.append(valid[:1]),
            pd.DatetimeIndex([pd.NaT], tz="UTC"),
            pd.DatetimeIndex(["2000-01-08"], tz="UTC"),
            pd.DatetimeIndex(["2000-01-17"], tz="UTC"),
            pd.DatetimeIndex(["1999-12-31"], tz="UTC"), pd.Index([1, 2]),
        ]
        for index in invalid:
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.plan(index)

    def test_source_session_cannot_precede_its_open(self):
        index = pd.DatetimeIndex(["2025-07-03"], tz="UTC")
        with self.assertRaisesRegex(ValueError, "not opened"):
            self.plan(index, 1, start="2025-07-03", end="2025-07-03", as_of=pd.Timestamp("2025-07-03T13:29:59Z"))

    def test_invalid_request_bounds_and_as_of(self):
        for options in (
            {"start": "2000-01-32"}, {"start": "2000-01-02"}, {"end": "2100-01-01"},
            {"start": "2000-02-01"}, {"start": 0}, {"start": "today"},
            {"start": pd.NaT}, {"start": pd.Timestamp("2000-01-03T01:00:00Z")},
            {"start": pd.Timestamp("2000-01-03", tz="America/New_York")},
            {"as_of": pd.Timestamp("2000-03-01")}, {"as_of": pd.NaT}, {"as_of": "2000-03-01"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.plan(**options)

    def test_invalid_policies_and_calendar_horizon(self):
        for name in ("leading", "trailing", "unfinished", "missing"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.plan(**{name: "ignore"})
        with self.assertRaisesRegex(ValueError, "horizon"):
            self.plan(count=2**63 - 1)
        with self.assertRaisesRegex(ValueError, "TradingSessionTimeframe"):
            build_trading_session_groups(pd.DatetimeIndex([], tz="UTC"), 10, start="2000-01-03", end="2000-01-04", as_of=pd.Timestamp("2000-03-01", tz="UTC"))

    def test_unsorted_inputs_and_outputs_do_not_mutate_inputs(self):
        index = self.sessions("2000-01-03", "2000-01-31")[::-1]
        before_index = index.copy(deep=True)
        rows = self.candles(index)
        before_rows = rows.copy(deep=True)
        result = aggregate_trading_sessions(
            rows, TradingSessionTimeframe(10), start="2000-01-03", end="2000-01-31",
            as_of=pd.Timestamp("2000-03-01", tz="UTC"),
        )
        result.candles.iloc[0, 0] = 999
        result.metadata.iloc[0, 3] = 999
        assert_index_equal(index, before_index)
        assert_frame_equal(rows, before_rows)
        self.assertEqual(self.plan().metadata.iloc[0]["source_count"], 10)

    def test_invalid_ohlcv_is_not_hidden_by_policy_drops(self):
        index = self.sessions("2000-01-05", "2000-01-12")
        rows = self.candles(index)
        rows.iloc[0, 1] = -1
        with self.assertRaisesRegex(ValueError, "Invalid OHLC"):
            aggregate_trading_sessions(
                rows, TradingSessionTimeframe(10), start="2000-01-05", end="2000-01-12",
                as_of=pd.Timestamp("2000-03-01", tz="UTC"), leading="drop", trailing="drop",
            )


if __name__ == "__main__":
    unittest.main()
