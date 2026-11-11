"""Daily yfinance mapping and stable synthetic providers, with no live HTTP."""

from unittest.mock import Mock
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from chronochart.local_providers import FixtureProvider, YFinanceProvider
from chronochart.providers import HistoricalRequest, ProviderError, ProviderResponseError


class LocalProviderTests(unittest.TestCase):
    def request(self, **kwargs):
        values = dict(symbol="AAPL", interval="1d", start=pd.Timestamp("2024-01-01", tz="UTC"),
                      end=pd.Timestamp("2024-01-05", tz="UTC"), as_of=pd.Timestamp("2024-02-01", tz="UTC"))
        values.update(kwargs)
        return HistoricalRequest(**values)

    def test_yfinance_daily_mapping_policy_timeout_and_no_mutation(self):
        raw = pd.DataFrame([[100, 102, 99, 101, 1000]] * 3,
                           index=pd.date_range("2024-01-02", periods=3, tz="America/New_York"),
                           columns=["Open", "High", "Low", "Close", "Volume"])
        before = raw.copy(deep=True)
        ticker = Mock()
        ticker.history.return_value = raw
        factory = Mock(return_value=ticker)
        result = YFinanceProvider(ticker_factory=factory).fetch_history(self.request())
        self.assertEqual(list(result.candles.index), list(pd.date_range("2024-01-02", periods=3, tz="UTC")))
        self.assertEqual(result.metadata.iloc[0]["bar_end"], pd.Timestamp("2024-01-02T21:00:00Z"))
        options = ticker.history.call_args.kwargs
        self.assertFalse(options["auto_adjust"])
        self.assertFalse(options["repair"])
        self.assertTrue(options["keepna"])
        self.assertEqual(options["timeout"], 10)
        assert_frame_equal(raw, before)

    def test_yfinance_failures_do_not_switch_to_fixture(self):
        ticker = Mock()
        ticker.history.side_effect = RuntimeError("network unavailable")
        with self.assertRaises(ProviderError):
            YFinanceProvider(ticker_factory=lambda _: ticker).fetch_history(self.request())
        with self.assertRaises(ValueError):
            YFinanceProvider(ticker_factory=lambda _: ticker).fetch_history(self.request(interval="15m"))
        ticker.history.return_value = pd.DataFrame()
        ticker.history.side_effect = None
        self.assertTrue(YFinanceProvider(ticker_factory=lambda _: ticker).fetch_history(self.request()).candles.empty)

    def test_yfinance_naive_or_holiday_index_rejected(self):
        ticker = Mock()
        for index in (pd.DatetimeIndex(["2024-01-02"]), pd.DatetimeIndex(["2024-01-01"], tz="America/New_York")):
            ticker.history.return_value = pd.DataFrame([[100, 102, 99, 101, 100]], index=index, columns=["Open", "High", "Low", "Close", "Volume"])
            with self.subTest(index=index), self.assertRaises(ProviderResponseError):
                YFinanceProvider(ticker_factory=lambda _: ticker).fetch_history(self.request())

    def test_fixture_values_are_stable_overlapping_queries(self):
        provider = FixtureProvider("equity")
        full = provider.fetch_history(self.request())
        short = provider.fetch_history(self.request(start=pd.Timestamp("2024-01-03", tz="UTC")))
        self.assertTrue(full.candles.attrs["synthetic"])
        assert_frame_equal(full.candles.loc[short.candles.index], short.candles, check_freq=False)
