"""Raw-cache TTL/isolation/refresh/corruption; no network and no real sleeps."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from chronochart.application import JobSpec, execute_job
from chronochart.cache import CacheBusyError, CacheCorruptError, FileCache
from chronochart.local_providers import FixtureProvider, provider_identity
from chronochart.providers import HistoricalRequest, ProviderError


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.cache = FileCache(self.root / "cache", ttl=10, clock=lambda: self.now)
        self.request = HistoricalRequest("BTCUSDT", "15m", pd.Timestamp("2024-02-29", tz="UTC"),
                                         pd.Timestamp("2024-03-01", tz="UTC"), pd.Timestamp("2024-03-02", tz="UTC"))
        self.identity = provider_identity("fixture-crypto")
        self.calls = 0

    def load(self):
        self.calls += 1
        return FixtureProvider("crypto").fetch_history(self.request)

    def fail(self):
        self.calls += 1
        raise ProviderError("offline/network refresh failure")

    def path(self):
        return self.cache.directory / (self.cache.identity(self.identity, self.request)[0] + ".json")

    def test_hit_and_expiry_exact_boundary(self):
        first = self.cache.fetch(self.identity, self.request, self.load)
        self.now = 1009.999
        hit = self.cache.fetch(self.identity, self.request, self.fail)
        self.assertEqual(hit.status, "hit")
        self.assertEqual(self.calls, 1)
        assert_frame_equal(first.data.candles, hit.data.candles, check_freq=False)
        assert_frame_equal(first.data.metadata, hit.data.metadata, check_freq=False)
        self.now = 1010
        fresh = self.cache.fetch(self.identity, self.request, self.load)
        self.assertEqual(fresh.status, "expired")
        self.assertEqual(self.calls, 2)

    def test_keys_isolate_provider_symbol_interval_bounds_cutoff_forming_and_schema(self):
        key = self.cache.identity(self.identity, self.request)[0]
        variants = [replace(self.request, symbol="ETHUSDT"), replace(self.request, interval="1m"),
                    replace(self.request, start=self.request.start + pd.Timedelta(minutes=15)),
                    replace(self.request, end=self.request.end + pd.Timedelta(minutes=15)),
                    replace(self.request, as_of=self.request.as_of + pd.Timedelta(seconds=1)),
                    replace(self.request, forming="include")]
        for request in variants:
            self.assertNotEqual(key, self.cache.identity(self.identity, request)[0])
        self.assertNotEqual(key, self.cache.identity({**self.identity, "name": "different"}, self.request)[0])
        self.assertNotEqual(key, FileCache(self.root, schema_version=2).identity(self.identity, self.request)[0])

    def test_expired_or_explicit_refresh_failure_never_serves_old_data_later(self):
        for refresh in (False, True):
            with self.subTest(refresh=refresh):
                self.now = 1000
                self.cache.fetch(self.identity, self.request, self.load)
                self.now = 1001 if refresh else 1011
                with self.assertRaises(ProviderError):
                    self.cache.fetch(self.identity, self.request, self.fail, refresh=refresh)
                self.assertFalse(self.path().exists())
                # Even a clock rollback cannot resurrect the invalidated entry.
                self.now = 1002
                with self.assertRaises(ProviderError):
                    self.cache.fetch(self.identity, self.request, self.fail)

    def test_corrupt_defaults_raise_and_explicit_refetch_or_refresh_recover(self):
        self.cache.fetch(self.identity, self.request, self.load)
        self.path().write_text("broken JSON", encoding="utf-8")
        with self.assertRaises(CacheCorruptError):
            self.cache.fetch(self.identity, self.request, self.fail)
        self.assertEqual(self.calls, 1)
        refetch = FileCache(self.cache.directory, ttl=10, corrupt="refetch", clock=lambda: self.now)
        self.assertEqual(refetch.fetch(self.identity, self.request, self.load).status, "corrupt-refetch")
        self.path().write_text("broken again", encoding="utf-8")
        self.assertEqual(self.cache.fetch(self.identity, self.request, self.load, refresh=True).status, "refresh")

    def test_corrupt_refetch_failure_invalidates_file(self):
        self.cache.fetch(self.identity, self.request, self.load)
        self.path().write_text("broken JSON", encoding="utf-8")
        refetch = FileCache(self.cache.directory, corrupt="refetch", clock=lambda: self.now)
        with self.assertRaises(ProviderError):
            refetch.fetch(self.identity, self.request, self.fail)
        self.assertFalse(self.path().exists())

    def test_checksum_identity_and_future_creation_are_rejected(self):
        import json
        self.cache.fetch(self.identity, self.request, self.load)
        original = self.path().read_text(encoding="utf-8")
        for change in ("checksum", "identity", "clock"):
            value = json.loads(original)
            if change == "checksum":
                value["payload"]["candles"]["rows"][0][0] += 1
            elif change == "identity":
                value["identity"]["schema_version"] = 999
            else:
                value["created_at"], value["expires_at"] = 2000, 2010
            self.path().write_text(json.dumps(value), encoding="utf-8")
            with self.subTest(change=change), self.assertRaises(CacheCorruptError):
                self.cache.fetch(self.identity, self.request, self.fail)

    def test_longer_ttl_does_not_resurrect_stored_expiry(self):
        self.cache.fetch(self.identity, self.request, self.load)
        self.now = 1010
        longer = FileCache(self.cache.directory, ttl=1000, clock=lambda: self.now)
        self.assertEqual(longer.fetch(self.identity, self.request, self.load).status, "expired")

    def test_busy_key_never_reads_stale_and_releases_own_lock_on_failure(self):
        self.cache.fetch(self.identity, self.request, self.load)
        lock = self.path().with_suffix(".lock")
        lock.write_text("another owner", encoding="utf-8")
        with self.assertRaises(CacheBusyError):
            self.cache.fetch(self.identity, self.request, self.fail)
        self.assertTrue(lock.exists())
        lock.unlink()
        with self.assertRaises(ProviderError):
            self.cache.fetch(self.identity, self.request, self.fail, refresh=True)
        self.assertFalse(lock.exists())

    def test_invalid_ttl(self):
        for ttl in (0, -1, float("inf"), float("nan"), True, 31 * 86400):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                FileCache(self.root, ttl=ttl)

    def test_semantically_invalid_cache_is_not_accepted_with_a_matching_checksum(self):
        import hashlib
        import json
        from chronochart.storage import json_bytes

        self.cache.fetch(self.identity, self.request, self.load)
        value = json.loads(self.path().read_text(encoding="utf-8"))
        value["payload"]["candles"]["rows"][0][4] = -1  # negative volume
        value["sha256"] = hashlib.sha256(json_bytes(value["payload"])).hexdigest()
        self.path().write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(CacheCorruptError):
            self.cache.fetch(self.identity, self.request, self.fail)
        refetch = FileCache(self.cache.directory, corrupt="refetch", clock=lambda: self.now)
        self.assertEqual(refetch.fetch(self.identity, self.request, self.load).status, "corrupt-refetch")

    def test_invalid_loader_result_is_never_stored(self):
        def invalid():
            data = self.load()
            data.metadata["is_closed"] = False
            return data

        with self.assertRaises(ProviderError):
            self.cache.fetch(self.identity, self.request, invalid)
        self.assertFalse(self.path().exists())

    def test_target_changes_reuse_raw_cache_but_export_different_results(self):
        class CountingProvider:
            calls = 0

            def fetch_history(self, request):
                self.calls += 1
                return FixtureProvider("equity").fetch_history(request)

        provider = CountingProvider()
        spec = JobSpec("fixture-equity", "AAPL", "2024-01-01", "2024-05-01", "2024-06-01T00:00:00Z",
                       "1d", "trading-sessions", 10, "sessions", str(self.root / "ten.json"), cache_dir=str(self.root / "raw"))
        override = (provider, provider_identity("fixture-equity"))
        first = execute_job(spec, provider_override=override, clock=lambda: self.now)
        second = execute_job(replace(spec, target_amount=15, output=str(self.root / "fifteen.json")), provider_override=override, clock=lambda: self.now)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(second.provenance["cache"]["status"], "hit")
        self.assertEqual(first.provenance["cache"]["key"], second.provenance["cache"]["key"])
        self.assertNotEqual(len(first.result.candles), len(second.result.candles))

    def test_failed_application_refresh_reports_failure_and_preserves_old_export_only(self):
        spec = JobSpec("fixture-crypto", "BTCUSDT", "2024-02-29", "2024-03-01", "2024-03-02T00:00:00Z",
                       "15m", "intraday", 45, "minutes", str(self.root / "result.json"), cache_dir=str(self.root / "raw"))
        data = execute_job(spec, clock=lambda: self.now)
        previous = Path(spec.output).read_bytes()

        class FailingProvider:
            def fetch_history(self, request):
                raise ProviderError("refresh failed")

        with self.assertRaises(ProviderError):
            execute_job(replace(spec, cache_refresh=True, overwrite=True),
                        provider_override=(FailingProvider(), provider_identity("fixture-crypto")), clock=lambda: self.now)
        self.assertEqual(Path(spec.output).read_bytes(), previous)  # not reported as a new export
        self.assertFalse((Path(spec.cache_dir) / (data.provenance["cache"]["key"] + ".json")).exists())
