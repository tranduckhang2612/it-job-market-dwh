"""Các bất biến của dữ liệu trước khi được ghi vào schema raw."""

from __future__ import annotations

from pathlib import Path
import unittest

from data_preprocessing.crawlers import itviec as crawl_itviec
from data_preprocessing.crawlers import topdev as crawl_topdev


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RawContractTests(unittest.TestCase):
    def test_itviec_identity_uses_source_id_and_canonical_url(self):
        identity = crawl_itviec.raw_job_identity({
            "source_job_id": "12345",
            "url": "https://itviec.com/it-jobs/backend-12345?ref=listing#salary",
        })
        self.assertEqual(identity, (
            "itviec:12345", "12345", "https://itviec.com/it-jobs/backend-12345"
        ))

    def test_topdev_identity_uses_source_id(self):
        identity = crawl_topdev.raw_job_identity({
            "id": 987,
            "detail_url": "https://topdev.vn/detail-jobs/backend-987?src=home",
        })
        self.assertEqual(identity, (
            "topdev:987", "987", "https://topdev.vn/detail-jobs/backend-987"
        ))

    def test_raw_timestamp_requires_timezone(self):
        with self.assertRaises(crawl_itviec.StorageError):
            crawl_itviec.database_timestamp("2026-11-02T10:00:00", "observed_at")

    def test_raw_json_rejects_nan(self):
        with self.assertRaises(crawl_itviec.StorageError):
            crawl_itviec.database_json({"salary": float("nan")})

    def test_schema_contains_only_raw_objects_and_no_explicit_index(self):
        sql = (PROJECT_ROOT / "data_preprocessing" / "sql" / "raw_schema.sql").read_text(encoding="utf-8").lower()
        self.assertIn("raw.crawl_runs", sql)
        self.assertIn("raw.job_postings", sql)
        self.assertNotIn("create schema if not exists staging", sql)
        self.assertNotIn("create schema if not exists analytics", sql)
        self.assertNotIn("create index", sql)


if __name__ == "__main__":
    unittest.main()
