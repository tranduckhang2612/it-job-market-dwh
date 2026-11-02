"""Kiểm tra raw -> normalize_data trên một database PostgreSQL tạm."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from types import SimpleNamespace
import unittest
import uuid

from data_preprocessing.crawlers import itviec as crawl_itviec
from data_preprocessing.storage.raw import ensure_raw_schema, validate_raw_schema
from data_preprocessing.crawlers import topdev as crawl_topdev
from data_preprocessing.cli.load_normalize_data import (
    ensure_normalize_data_schema,
    fetch_raw_records,
    normalize_records,
)


def run_postgres_checks(connection_options):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    options = (conninfo_to_dict(connection_options)
               if isinstance(connection_options, str) else dict(connection_options))
    options.pop("autocommit", None)
    options.setdefault("connect_timeout", 5)
    database_name = "raw_crawler_test_" + uuid.uuid4().hex
    admin = psycopg.connect(**options, autocommit=True)
    connection = None
    created = False
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        created = True
        test_options = {**options, "dbname": database_name}
        connection = psycopg.connect(**test_options, autocommit=True)
        assert ensure_raw_schema(connection) is True
        assert ensure_raw_schema(connection) is False
        validate_raw_schema(connection, RuntimeError)

        args = SimpleNamespace(
            db_dsn=make_conninfo(**test_options), db_host=None, db_port=None,
            db_name=None, db_user=None, db_password=None,
            db_password_prompt=False, db_connect_timeout=5,
        )
        now = datetime.now(timezone.utc).isoformat()

        itviec_run = {
            "crawl_run_id": "itviec_test", "started_at": now, "finished_at": now,
            "requested_count": 1, "collected_count": 1, "complete": True,
            "storage_failed": False, "interrupted": False,
        }
        itviec_job = {
            "source_job_id": "101", "url": "https://itviec.com/it-jobs/test-101",
            "crawled_at": now, "title": "Backend Engineer",
            "job_description": "Build APIs", "skills": ["Python"],
        }
        sink = crawl_itviec.PostgresSink(args)
        try:
            sink.start_run({**itviec_run, "collected_count": 0, "complete": False}, [])
            assert sink.save_job(itviec_job, itviec_run, []) is True
            sink.finish_run(itviec_run, [])
        finally:
            sink.close()

        topdev_run = {
            "crawl_run_id": "topdev_test", "started_at": now, "finished_at": now,
            "requested_count": 1, "collected_count": 1, "complete": True,
            "storage_failed": False, "interrupted": False,
        }
        topdev_job = {
            "id": 202, "detail_url": "https://topdev.vn/detail-jobs/test-202",
            "observed_at": now, "title": "Data Engineer",
            "description": "Build data pipelines", "skills_arr": ["Python"],
        }
        sink = crawl_topdev.PostgresSink(args)
        try:
            sink.start_run({**topdev_run, "collected_count": 0, "complete": False}, [])
            assert sink.save_job(topdev_job, topdev_run, []) is True
            sink.finish_run(topdev_run, [])
        finally:
            sink.close()

        runs = connection.execute(
            "SELECT source, status, collected_count FROM raw.crawl_runs ORDER BY source"
        ).fetchall()
        jobs = connection.execute(
            "SELECT job_id, raw_payload->>'title' FROM raw.job_postings ORDER BY job_id"
        ).fetchall()
        assert runs == [("itviec", "succeeded", 1), ("topdev", "succeeded", 1)]
        assert jobs == [("itviec:101", "Backend Engineer"), ("topdev:202", "Data Engineer")]

        assert ensure_normalize_data_schema(connection) is True
        assert ensure_normalize_data_schema(connection) is False
        summary = normalize_records(connection, fetch_raw_records(connection))
        assert summary.scanned == 2
        assert summary.normalized == 2
        assert summary.rejected_role == 0
        assert summary.rejected_normalization == 0
        assert summary.failed_write == 0
        # Upsert cùng batch phải giữ đúng hai dòng, không nhân bản lịch sử.
        connection.execute(
            "UPDATE normalize_data.normalized_jobs "
            "SET normalized_at=TIMESTAMPTZ '2000-01-01 00:00:00+00'"
        )
        assert normalize_records(connection, fetch_raw_records(connection)).normalized == 2
        normalized = connection.execute(
            "SELECT job_id, source, title, normalization_version, "
            "jsonb_typeof(normalization_meta), "
            "normalized_at=TIMESTAMPTZ '2000-01-01 00:00:00+00' "
            "FROM normalize_data.normalized_jobs ORDER BY job_id"
        ).fetchall()
        assert normalized == [
            ("itviec:101", "itviec", "Backend Engineer", "5.1", "object", True),
            ("topdev:202", "topdev", "Data Engineer", "5.1", "object", True),
        ]
    finally:
        if connection is not None:
            connection.close()
        try:
            if created:
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=%s AND pid<>pg_backend_pid()", (database_name,),
                )
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        finally:
            admin.close()


@unittest.skipUnless(
    os.environ.get("ITVIEC_TEST_DSN"),
    "Set ITVIEC_TEST_DSN for isolated PostgreSQL integration",
)
class PostgreSQLRawRoundtripTests(unittest.TestCase):
    def test_both_sources_write_raw(self):
        run_postgres_checks(os.environ["ITVIEC_TEST_DSN"])


if __name__ == "__main__":
    unittest.main()
