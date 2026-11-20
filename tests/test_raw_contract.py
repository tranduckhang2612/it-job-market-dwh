"""Các bất biến của dữ liệu trước khi được ghi vào schema raw."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest import mock

from data_preprocessing.crawlers import itviec as crawl_itviec
from data_preprocessing.crawlers import topdev as crawl_topdev


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RawContractTests(unittest.TestCase):
    def test_topdev_incremental_underfill_is_not_a_pipeline_failure(self):
        metadata = {
            "complete": False,
            "storage_failed": False,
            "interrupted": False,
        }
        self.assertEqual(crawl_topdev.crawl_exit_code(metadata, [], []), 0)
        self.assertEqual(crawl_topdev.crawl_exit_code(metadata, [{}], []), 0)
        self.assertEqual(crawl_topdev.crawl_exit_code(metadata, [{}], [{}]), 2)
        self.assertEqual(crawl_topdev.crawl_exit_code(metadata, [], [{}]), 1)

    def test_itviec_cookie_allows_blank_lines_but_rejects_extra_content(self):
        with TemporaryDirectory() as directory:
            cookie = Path(directory) / "cookie.txt"
            cookie.write_text("session=encoded-value\n\n", encoding="utf-8")
            self.assertEqual(
                crawl_itviec.read_cookie_file(cookie),
                [("session", "encoded-value")],
            )
            cookie.write_text("session=encoded-value\nnot-a-cookie", encoding="utf-8")
            with self.assertRaises(crawl_itviec.CrawlError):
                crawl_itviec.read_cookie_file(cookie)

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
        self.assertIn("raw.seen_jobs", sql)
        self.assertNotIn("create schema if not exists staging", sql)
        self.assertNotIn("create schema if not exists analytics", sql)
        self.assertNotIn("create index", sql)

    def test_existing_ids_are_queried_in_a_bounded_batch(self):
        class Cursor:
            def __init__(self):
                self.executed = None

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, query, parameters):
                self.executed = (query, parameters)

            def fetchall(self):
                return [("itviec:1",)]

        cursor = Cursor()
        sink = object.__new__(crawl_itviec.PostgresSink)
        sink.conn = SimpleNamespace(cursor=lambda: cursor)
        sink.db_errors = ()

        existing = sink.find_existing_job_ids(["itviec:1", "itviec:2", "itviec:1"])

        self.assertEqual(existing, {"itviec:1"})
        query, parameters = cursor.executed
        self.assertIn("unnest(%s::text[])", query)
        self.assertIn("FROM raw.seen_jobs AS stored", query)
        self.assertIn("WHERE stored.job_id = candidate.job_id", query)
        self.assertEqual(parameters, (["itviec:1", "itviec:2"],))

    def test_losing_atomic_claim_does_not_insert_raw_job(self):
        class Context:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class Cursor(Context):
            def __init__(self):
                self.queries = []

            def execute(self, query, parameters):
                self.queries.append((query, parameters))

            def fetchone(self):
                return None

        cursor = Cursor()
        sink = object.__new__(crawl_itviec.PostgresSink)
        sink.conn = SimpleNamespace(cursor=lambda: cursor, transaction=Context)
        sink.db_errors = ()
        sink._payload = lambda value: value

        inserted = sink.save_job({
            "source_job_id": "101",
            "url": "https://itviec.com/it-jobs/backend-101",
            "crawled_at": "2026-11-18T10:00:00+00:00",
        }, {
            "crawl_run_id": "losing_run",
            "started_at": "2026-11-18T10:00:00+00:00",
            "requested_count": 1,
            "collected_count": 1,
            "complete": True,
        }, [])

        self.assertFalse(inserted)
        self.assertEqual(len(cursor.queries), 1)
        self.assertIn("INSERT INTO raw.seen_jobs", cursor.queries[0][0])

    def test_topdev_skips_old_page_and_collects_new_page(self):
        class FakeSink:
            instance = None

            def __init__(self, _args):
                self.queries = []
                self.saved = []
                FakeSink.instance = self

            def start_run(self, _metadata, _errors):
                pass

            def find_existing_job_ids(self, candidate_ids):
                self.queries.append(candidate_ids)
                return set(candidate_ids) & {"topdev:1", "topdev:2"}

            def save_job(self, job, _progress, _errors):
                self.saved.append(job["id"])
                return True

            def finish_run(self, _metadata, _errors):
                pass

            def close(self):
                pass

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            def get_json(self, _url, params=None):
                page = params["page"]
                ids = (1, 2) if page == 1 else (3, 4)
                return {
                    "data": [
                        {"id": job_id, "detail_url": f"https://topdev.vn/detail-jobs/job-{job_id}"}
                        for job_id in ids
                    ],
                    "meta": {"last_page": 2},
                }

            def close(self):
                pass

        args = SimpleNamespace(
            crawl_run_id="topdev_batch_test", limit=2, max_pages=2,
            no_db=False, output=None, save_json=False, output_dir=None,
            cookie_file=None, cookie=None, token=None, timeout=5,
        )
        with (
            mock.patch.object(crawl_topdev, "PostgresSink", FakeSink),
            mock.patch.object(crawl_topdev, "TopDevHttpClient", FakeClient),
            mock.patch.object(crawl_topdev, "fetch_job_details", side_effect=lambda _client, job: job),
            mock.patch.object(crawl_topdev.time, "sleep"),
        ):
            status = crawl_topdev.run(args)

        self.assertEqual(status, 0)
        self.assertEqual(FakeSink.instance.queries, [
            ["topdev:1", "topdev:2"], ["topdev:3", "topdev:4"],
        ])
        self.assertEqual(FakeSink.instance.saved, [3, 4])


if __name__ == "__main__":
    unittest.main()
