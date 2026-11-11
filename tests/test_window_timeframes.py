"""Boundary, source-resolution, and policy contracts for named time windows."""

import unittest
from unittest.mock import patch

import exchange_calendars as xcals
import pandas as pd
from pandas.testing import assert_frame_equal, assert_index_equal

from chronochart.aggregation import OHLCV
from chronochart.pipeline import aggregate_time_windows
from chronochart.window_timeframes import (
    MissingBarsError, SourceResolution, WindowTimeframe, build_window_groups,
)


def utc(value):
    return pd.Timestamp(value, tz="UTC")


class WindowTimeframeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exchange = xcals.get_calendar("XNYS", start="2023-01-01", end="2026-12-31")

    def daily(self, start, end, market="crypto"):
        if market == "crypto":
            return pd.date_range(start, end, freq="D", inclusive="left", tz="UTC")
        return self.exchange.sessions_in_range(start, end).tz_localize("UTC")

    def bars(self, index):
        return pd.DataFrame(
            [[i, i + 2, i - 1, i + 1, 10] for i in range(1, len(index) + 1)],
            index=index, columns=OHLCV,
        )

    def intraday(self, day, interval="15min", market="equity"):
        if market == "crypto":
            return pd.date_range(utc(day), utc(day) + pd.Timedelta(days=1), freq=interval, inclusive="left")
        return pd.date_range(
            self.exchange.session_open(day), self.exchange.session_close(day),
            freq=interval, inclusive="left",
        )

    def plan(self, index, mode="45-minute intraday", market="crypto", source=None, **kwargs):
        options = dict(start=utc("2025-07-03"), end=utc("2025-07-04"), as_of=utc("2026-01-01"))
        options.update(kwargs)
        return build_window_groups(
            index, WindowTimeframe(mode, market), source or SourceResolution.intraday("15min"), **options,
        )

    def test_explicit_modes_and_invalid_source_declarations(self):
        for mode in ("3 weeks", "2 months", "45 minutes", "3 trading weeks", None):
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                WindowTimeframe(mode, "crypto")
        with self.assertRaises(ValueError):
            WindowTimeframe("3 calendar weeks", "stocks")
        for value in ("0min", "-1min", "30s", "1D", "bad", 15, pd.NaT):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SourceResolution.intraday(value)
        for source in (("daily", pd.Timedelta(minutes=15)), ("intraday", None), ("weekly", None)):
            with self.subTest(source=source), self.assertRaises(ValueError):
                SourceResolution(*source)

    def test_crypto_exact_45_minute_boundaries_left_closed(self):
        index = pd.date_range(utc("2025-07-03"), periods=6, freq="15min")
        plan = self.plan(index, end=utc("2025-07-03 01:30"))
        self.assertEqual([group.timestamp for group in plan.groups], [utc("2025-07-03"), utc("2025-07-03 00:45")])
        self.assertEqual(plan.groups[0].members, tuple(index[:3]))
        self.assertEqual(plan.groups[1].members, tuple(index[3:]))
        self.assertTrue(plan.metadata["is_complete"].all())
        self.assertEqual(plan.metadata.attrs["closure"], "left")

    def test_crypto_24_7_weekends_and_holidays(self):
        for day in ("2025-07-04", "2025-07-05", "2025-12-25"):
            with self.subTest(day=day):
                index = self.intraday(day, market="crypto")
                plan = self.plan(index, start=utc(day), end=utc(day) + pd.Timedelta(days=1))
                self.assertEqual(len(plan.groups), 32)
                self.assertTrue(plan.metadata["is_complete"].all())

    def test_crypto_intraday_year_change_has_no_anchor_reset(self):
        index = pd.date_range(utc("2024-12-31 23:15"), periods=6, freq="15min")
        plan = self.plan(index, start=utc("2024-12-31 23:15"), end=utc("2025-01-01 00:45"))
        self.assertEqual([g.timestamp for g in plan.groups], [utc("2024-12-31 23:15"), utc("2025-01-01")])
        self.assertTrue(plan.metadata["is_complete"].all())

    def test_two_month_alignment_and_leap_day(self):
        index = self.daily("2024-01-01", "2024-03-02")
        plan = self.plan(
            index, "2 calendar months", source=SourceResolution.daily(),
            start=utc("2024-01-01"), end=utc("2024-03-02"),
        )
        self.assertEqual([g.timestamp for g in plan.groups], [utc("2024-01-01"), utc("2024-03-01")])
        self.assertEqual(plan.metadata.iloc[0]["expected_count"], 60)
        self.assertIn(utc("2024-02-29"), plan.groups[0].members)
        self.assertNotIn(utc("2024-03-01"), plan.groups[0].members)
        self.assertIn(utc("2024-03-01"), plan.groups[1].members)
        self.assertEqual(plan.metadata.iloc[1]["window_end"], utc("2024-05-01"))

    def test_nonleap_february_and_month_year_change(self):
        index = self.daily("2024-11-01", "2025-03-01")
        plan = self.plan(
            index, "2 calendar months", source=SourceResolution.daily(),
            start=utc("2024-11-01"), end=utc("2025-03-01"),
        )
        self.assertEqual([g.timestamp for g in plan.groups], [utc("2024-11-01"), utc("2025-01-01")])
        self.assertEqual(plan.metadata["expected_count"].tolist(), [61, 59])
        self.assertTrue(plan.metadata["is_complete"].all())

    def test_three_calendar_weeks_monday_fixed_anchor_not_15_rows(self):
        index = self.daily("2025-01-01", "2025-03-01")
        plan = self.plan(
            index, "3 calendar weeks", source=SourceResolution.daily(),
            start=utc("2025-01-01"), end=utc("2025-03-01"),
        )
        anchor = utc("2000-01-03")
        for group in plan.groups:
            self.assertEqual(group.expected_count, 21)
            self.assertEqual(group.timestamp.dayofweek, 0)
            self.assertEqual((group.timestamp - anchor).days % 21, 0)
        self.assertTrue(plan.metadata.iloc[0]["leading_partial"])
        self.assertTrue(plan.metadata.iloc[-1]["trailing_partial"])

    def test_equity_calendar_daily_labels_use_exchange_date_not_previous_local_date(self):
        index = self.daily("2025-01-01", "2025-02-28", market="equity")
        start = pd.Timestamp("2025-01-01", tz="America/New_York")
        end = pd.Timestamp("2025-03-01", tz="America/New_York")
        plan = self.plan(
            index, "2 calendar months", market="equity", source=SourceResolution.daily(), start=start, end=end,
        )
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(plan.groups[0].timestamp, utc("2025-01-01 05:00"))
        self.assertEqual(plan.groups[0].members, tuple(index))
        self.assertTrue(plan.metadata.iloc[0]["is_complete"])
        self.assertNotIn(utc("2025-01-01"), plan.groups[0].members)  # holiday

    def test_equity_month_boundaries_follow_dst_not_fixed_utc_offset(self):
        index = self.daily("2025-03-01", "2025-04-30", market="equity")
        plan = self.plan(
            index, "2 calendar months", market="equity", source=SourceResolution.daily(),
            start=pd.Timestamp("2025-03-01", tz="America/New_York"),
            end=pd.Timestamp("2025-05-01", tz="America/New_York"),
        )
        self.assertEqual(plan.groups[0].timestamp, utc("2025-03-01 05:00"))
        self.assertEqual(plan.metadata.iloc[0]["window_end"], utc("2025-05-01 04:00"))

    def test_equity_three_calendar_weeks_span_dst_with_local_monday_boundaries(self):
        index = self.daily("2025-02-24", "2025-03-21", market="equity")
        plan = self.plan(
            index, "3 calendar weeks", market="equity", source=SourceResolution.daily(),
            start=pd.Timestamp("2025-02-24", tz="America/New_York"),
            end=pd.Timestamp("2025-03-22", tz="America/New_York"),
        )
        dst_windows = 0
        for _, row in plan.metadata.iterrows():
            left, right = row["window_start"], row["window_end"]
            self.assertEqual(left.tz_convert("America/New_York").dayofweek, 0)
            self.assertEqual(left.tz_convert("America/New_York").hour, 0)
            if right - left == pd.Timedelta(hours=503):
                dst_windows += 1
        self.assertEqual(dst_windows, 1)

    def test_equity_regular_session_short_final_window_is_complete(self):
        index = self.intraday("2025-07-02")
        plan = self.plan(index, market="equity", start=utc("2025-07-02"), end=utc("2025-07-03"))
        self.assertEqual(len(plan.groups), 9)
        self.assertEqual(plan.groups[0].timestamp, utc("2025-07-02 13:30"))
        self.assertEqual(plan.metadata.iloc[-1]["window_start"], utc("2025-07-02 19:30"))
        self.assertEqual(plan.metadata.iloc[-1]["window_end"], utc("2025-07-02 20:00"))
        self.assertTrue(plan.metadata.iloc[-1]["short_window"])
        self.assertEqual(plan.metadata.iloc[-1]["expected_count"], 2)
        self.assertTrue(plan.metadata["is_complete"].all())
        self.assertFalse(plan.metadata.iloc[-1]["trailing_partial"])

    def test_equity_early_close_short_window_retention(self):
        index = self.intraday("2025-07-03")
        for policy in ("retain", "drop"):
            plan = self.plan(index, market="equity", short_session=policy)
            self.assertEqual(len(plan.groups), 5)
            self.assertEqual(plan.metadata.iloc[-1]["window_end"], utc("2025-07-03 17:00"))
            self.assertEqual(plan.metadata.iloc[-1]["retained"], policy == "retain")
            self.assertTrue(plan.metadata.iloc[-1]["is_complete"])

    def test_intraday_dst_open_shifts_in_spring_and_fall(self):
        for day, hour in (("2025-03-07", 14), ("2025-03-10", 13), ("2025-10-31", 13), ("2025-11-03", 14)):
            with self.subTest(day=day):
                plan = self.plan(
                    self.intraday(day), market="equity", start=utc(day), end=utc(day) + pd.Timedelta(days=1),
                )
                self.assertEqual(plan.groups[0].timestamp.hour, hour)
                self.assertEqual(plan.groups[0].timestamp.minute, 30)
                self.assertEqual(len(plan.groups), 9)

    def test_equity_weekend_and_holiday_empty_not_missing(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        for day in ("2025-07-04", "2025-07-05"):
            plan = self.plan(empty, market="equity", start=utc(day), end=utc(day) + pd.Timedelta(days=1))
            self.assertEqual(plan.groups, ())
            self.assertTrue(plan.metadata.empty)
            self.assertEqual(plan.metadata["is_complete"].dtype, "bool")

    def test_daily_or_coarser_bars_cannot_create_intraday_even_empty(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        for market in ("equity", "crypto"):
            for source in (SourceResolution.daily(), SourceResolution.intraday("60min")):
                with self.subTest(market=market, source=source), self.assertRaisesRegex(ValueError, "fine intraday"):
                    self.plan(empty, market=market, source=source)

    def test_straddling_source_grid_rejected_even_when_offending_bar_absent(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        for market in ("equity", "crypto"):
            for interval in ("10min", "30min"):
                with self.subTest(market=market, interval=interval), self.assertRaisesRegex(ValueError, "straddles"):
                    self.plan(empty, market=market, source=SourceResolution.intraday(interval), missing="retain")

    def test_finer_source_resolves_target_straddling(self):
        index = self.intraday("2025-07-03", interval="5min")
        plan = self.plan(index, market="equity", source=SourceResolution.intraday("5min"))
        self.assertEqual(plan.metadata.iloc[0]["expected_count"], 9)
        self.assertTrue(plan.metadata["is_complete"].all())

    def test_calendar_months_accept_fine_intraday_source(self):
        index = pd.date_range(utc("2024-01-01"), utc("2024-03-01"), freq="15min", inclusive="left")
        plan = self.plan(
            index, "2 calendar months", source=SourceResolution.intraday("15min"),
            start=utc("2024-01-01"), end=utc("2024-03-01"),
        )
        self.assertEqual(len(plan.groups), 1)
        self.assertEqual(plan.groups[0].expected_count, 60 * 24 * 4)
        self.assertTrue(plan.metadata.iloc[0]["is_complete"])

    def test_calendar_boundary_straddling_is_also_rejected(self):
        with self.assertRaisesRegex(ValueError, "straddles"):
            self.plan(
                pd.DatetimeIndex([], tz="UTC"), "2 calendar months",
                source=SourceResolution.intraday("7min"),
                start=utc("2024-01-01"), end=utc("2024-03-01"), missing="retain",
            )

    def test_request_boundary_cannot_split_source_bar(self):
        index = pd.DatetimeIndex([], tz="UTC")
        with self.assertRaisesRegex(ValueError, "Request boundary"):
            self.plan(index, start=utc("2025-07-03 00:05"), end=utc("2025-07-03 00:45"), missing="retain")

    def test_missing_bars_do_not_shift_later_crypto_boundaries(self):
        full_index = pd.date_range(utc("2025-07-03"), periods=6, freq="15min")
        absent = full_index[1]
        index = full_index.delete(1)
        with self.assertRaises(MissingBarsError) as caught:
            self.plan(index, end=utc("2025-07-03 01:30"))
        self.assertEqual(caught.exception.bars, (absent,))
        plan = self.plan(index, end=utc("2025-07-03 01:30"), missing="retain")
        self.assertEqual(plan.groups[1].timestamp, utc("2025-07-03 00:45"))
        self.assertEqual(plan.groups[1].members, tuple(full_index[3:]))
        self.assertFalse(plan.metadata.iloc[0]["coverage_complete"])

    def test_missing_equity_bar_not_confused_with_overnight_or_holiday(self):
        full = self.intraday("2025-07-03")
        index = full.delete(1)
        plan = self.plan(index, market="equity", end=utc("2025-07-07"), missing="retain")
        self.assertEqual(len(plan.groups), 5)
        self.assertEqual(plan.metadata.iloc[0]["missing_bars"], (full[1],))
        self.assertTrue(plan.metadata.iloc[1:]["coverage_complete"].all())

    def test_missing_calendar_day_leap_day_does_not_shift_months(self):
        index = self.daily("2024-01-01", "2024-05-01").drop(utc("2024-02-29"))
        plan = self.plan(
            index, "2 calendar months", source=SourceResolution.daily(),
            start=utc("2024-01-01"), end=utc("2024-05-01"), missing="retain",
        )
        self.assertEqual(plan.metadata.iloc[0]["missing_bars"], (utc("2024-02-29"),))
        self.assertEqual(plan.groups[1].timestamp, utc("2024-03-01"))
        self.assertTrue(plan.metadata.iloc[1]["is_complete"])

    def test_forming_window_counts_not_final_completeness(self):
        index = pd.date_range(utc("2025-07-03"), periods=3, freq="15min")
        for policy in ("retain", "drop"):
            plan = self.plan(index, end=utc("2025-07-03 00:45"), as_of=utc("2025-07-03 00:35"), unfinished=policy)
            row = plan.metadata.iloc[0]
            self.assertTrue(row["count_complete"])
            self.assertTrue(row["coverage_complete"])
            self.assertFalse(row["is_complete"])
            self.assertEqual(row["pending_bars"], (index[2],))
            self.assertEqual(row["retained"], policy == "retain")

    def test_future_unopened_source_row_rejected(self):
        index = pd.date_range(utc("2025-07-03"), periods=3, freq="15min")
        with self.assertRaisesRegex(ValueError, "not opened"):
            self.plan(index, end=utc("2025-07-03 00:45"), as_of=utc("2025-07-03 00:15"))

    def test_calendar_equity_maturity_is_calendar_end_not_last_close(self):
        index = self.daily("2025-01-01", "2025-02-28", market="equity")
        plan = self.plan(
            index, "2 calendar months", market="equity", source=SourceResolution.daily(),
            start=pd.Timestamp("2025-01-01", tz="America/New_York"),
            end=pd.Timestamp("2025-03-01", tz="America/New_York"), as_of=utc("2025-02-28 23:00"),
        )
        self.assertTrue(plan.metadata.iloc[0]["count_complete"])
        self.assertFalse(plan.metadata.iloc[0]["window_closed"])

    def test_leading_trailing_partials_and_missing_drop_metadata(self):
        index = pd.date_range(utc("2025-07-03 00:15"), periods=7, freq="15min").delete(3)
        plan = self.plan(
            index, start=utc("2025-07-03 00:15"), end=utc("2025-07-03 02:00"),
            leading="drop", trailing="drop", missing="drop",
        )
        self.assertEqual(len(plan.groups), 3)
        self.assertEqual(plan.metadata["retained"].tolist(), [False, False, False])
        self.assertTrue(plan.metadata.iloc[0]["leading_partial"])
        self.assertTrue(plan.metadata.iloc[-1]["trailing_partial"])
        self.assertEqual(plan.metadata.iloc[0]["missing_bars"], ())

    def test_overlapping_intraday_queries_keep_full_interior_results(self):
        index = pd.date_range(utc("2025-07-03"), periods=12, freq="15min")
        rows = self.bars(index)
        options = dict(timeframe=WindowTimeframe("45-minute intraday", "crypto"), source=SourceResolution.intraday("15min"), as_of=utc("2026-01-01"))
        full = aggregate_time_windows(rows, start=index[0], end=utc("2025-07-03 03:00"), **options)
        short = aggregate_time_windows(rows.iloc[1:-1], start=index[1], end=index[-1], **options)
        interior = full.candles.index[1:3]
        assert_frame_equal(full.candles.loc[interior], short.candles.loc[interior])
        self.assertEqual(list(short.metadata.index), list(full.metadata.index))

    def test_overlapping_month_and_week_queries_have_stable_labels(self):
        for mode in ("3 calendar weeks", "2 calendar months"):
            with self.subTest(mode=mode):
                index = self.daily("2024-01-01", "2024-05-01")
                full = self.plan(index, mode, source=SourceResolution.daily(), start=utc("2024-01-01"), end=utc("2024-05-01"))
                subset = self.daily("2024-01-10", "2024-04-20")
                short = self.plan(subset, mode, source=SourceResolution.daily(), start=utc("2024-01-10"), end=utc("2024-04-20"))
                for group in short.groups:
                    self.assertIn(group.timestamp, full.metadata.index)
                    self.assertEqual(short.metadata.loc[group.timestamp, "window_end"], full.metadata.loc[group.timestamp, "window_end"])

    def test_overlapping_equity_intraday_queries_keep_interior_candles(self):
        index = self.intraday("2025-07-03")
        rows = self.bars(index)
        options = dict(
            timeframe=WindowTimeframe("45-minute intraday", "equity"),
            source=SourceResolution.intraday("15min"), as_of=utc("2026-01-01"),
        )
        full = aggregate_time_windows(rows, start=index[0], end=utc("2025-07-03 17:00"), **options)
        short = aggregate_time_windows(rows.iloc[1:-1], start=index[1], end=index[-1], **options)
        assert_frame_equal(full.candles.iloc[1:-1], short.candles.iloc[1:-1])
        self.assertEqual(list(full.metadata.index), list(short.metadata.index))

    def test_daily_equity_labels_at_calendar_week_boundary_stay_in_new_window(self):
        index = self.daily("2024-01-01", "2024-03-01", market="equity")
        plan = self.plan(
            index, "3 calendar weeks", market="equity", source=SourceResolution.daily(),
            start=pd.Timestamp("2024-01-01", tz="America/New_York"),
            end=pd.Timestamp("2024-03-02", tz="America/New_York"), missing="retain",
        )
        checked = 0
        for group in plan.groups:
            date = group.timestamp.tz_convert("America/New_York").tz_localize(None).tz_localize("UTC")
            if date in index:
                self.assertIn(date, group.members)
                checked += 1
        self.assertGreater(checked, 0)

    def test_early_close_exact_maturity_and_no_bar_at_close(self):
        index = self.intraday("2025-07-03")
        before = self.plan(index, market="equity", as_of=utc("2025-07-03 16:59:59"))
        at = self.plan(index, market="equity", as_of=utc("2025-07-03 17:00"))
        self.assertTrue(before.metadata.iloc[-1]["count_complete"])
        self.assertFalse(before.metadata.iloc[-1]["is_complete"])
        self.assertTrue(at.metadata.iloc[-1]["is_complete"])
        with self.assertRaisesRegex(ValueError, "off-grid"):
            self.plan(index.append(pd.DatetimeIndex([utc("2025-07-03 17:00")])), market="equity")

    def test_resource_limits_reject_without_incomplete_results(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        with patch("chronochart.window_timeframes.MAX_SOURCE_SLOTS", 2):
            with self.assertRaisesRegex(ValueError, "source slots"):
                self.plan(empty, end=utc("2025-07-03 00:45"), missing="retain")
        with patch("chronochart.window_timeframes.MAX_TARGET_WINDOWS", 1):
            with self.assertRaisesRegex(ValueError, "target windows"):
                self.plan(empty, end=utc("2025-07-03 01:30"), missing="retain")

    def test_inputs_are_not_mutated_and_dropped_bars_still_validated(self):
        index = self.intraday("2025-07-03")[::-1]
        rows = self.bars(index)
        before = rows.copy(deep=True)
        index_before = index.copy(deep=True)
        options = dict(timeframe=WindowTimeframe("45-minute intraday", "equity"), source=SourceResolution.intraday("15min"), start=utc("2025-07-03"), end=utc("2025-07-04"), as_of=utc("2026-01-01"), short_session="drop")
        result = aggregate_time_windows(rows, **options)
        result.candles.iloc[0, 0] = 999
        assert_frame_equal(rows, before)
        assert_index_equal(index, index_before)
        last_label = self.intraday("2025-07-03")[-1]
        rows.loc[last_label, "High"] = -1
        with self.assertRaisesRegex(ValueError, "Invalid OHLC"):
            aggregate_time_windows(rows, **options)

    def test_empty_closed_expected_grid_requires_explicit_missing_policy(self):
        empty = pd.DatetimeIndex([], tz="UTC")
        with self.assertRaises(MissingBarsError):
            self.plan(empty, end=utc("2025-07-03 00:45"))
        plan = self.plan(empty, end=utc("2025-07-03 00:45"), missing="retain")
        self.assertEqual(plan.metadata.iloc[0]["source_count"], 0)
        self.assertEqual(len(plan.metadata.iloc[0]["missing_bars"]), 3)
        self.assertFalse(plan.metadata.iloc[0]["retained"])

    def test_invalid_indices_requests_and_policies(self):
        valid = pd.date_range(utc("2025-07-03"), periods=3, freq="15min")
        for index in (valid.tz_localize(None), valid.tz_convert("America/New_York"), valid.append(valid[:1]), pd.DatetimeIndex([pd.NaT], tz="UTC"), valid + pd.Timedelta(minutes=1), valid + pd.Timedelta(days=1), pd.Index([1, 2])):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.plan(index, end=utc("2025-07-03 00:45"))
        for options in (
            {"start": pd.Timestamp("2025-07-03")}, {"start": utc("1999-01-01")},
            {"end": utc("2025-07-03")}, {"as_of": pd.NaT}, {"as_of": "2026-01-01"},
            {"leading": "bad"}, {"trailing": "bad"}, {"unfinished": "bad"},
            {"missing": "bad"}, {"short_session": "bad"},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.plan(valid, **options)


if __name__ == "__main__":
    unittest.main()
