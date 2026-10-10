"""Opt-in PostgreSQL roundtrip against an isolated temporary database.

Set ITVIEC_TEST_DSN to a test server account with CREATEDB, then run unittest
discovery. The harness creates and drops only its UUID-named database; it does
not inspect cookies, crawl the website, or change the configured project DB.
run_postgres_checks(connection_options) also accepts connection options in memory.
"""
from __future__ import annotations
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import main_itviec as pipeline
from data_preprocessing.itviec_crawl_data import crawl_itviec as crawler
from data_preprocessing.itviec_normalize_data import normalize_itviec as normalizer


def _raw(source_id, observed_at):
    return {"source_job_id": source_id,
            "url": "https://itviec.com/it-jobs/backend-engineer-" + source_id,
            "title": "Backend Engineer", "company_name": "Fixture Company",
            "crawled_at": observed_at, "date_posted": "2026-10-08",
            "job_description": "Build services.\nMaintain APIs.",
            "your_skills_and_experience": "Python is mandatory. Docker preferred. English required.",
            "skills": ["Python", "Docker"], "working_model": "Hybrid",
            "salary": {"text": "20–30 triệu VND/tháng, gross", "visibility": "visible"},
            "locations_structured": {"address": {"addressRegion": "Ho Chi Minh"}}}


def run_postgres_checks(connection_options):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    check = unittest.TestCase()
    options = (conninfo_to_dict(connection_options) if isinstance(connection_options, str)
               else dict(connection_options))
    options.pop("autocommit", None)
    options.setdefault("connect_timeout", 5)
    database_name = "itviec_quality_test_" + uuid.uuid4().hex
    admin = psycopg.connect(**options, autocommit=True)
    created = False
    connection = None
    completed = []
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
        created = True
        test_options = {**options, "dbname": database_name}
        connection = psycopg.connect(**test_options, autocommit=True)
        dsn = make_conninfo(**test_options)
        args = SimpleNamespace(db_dsn=dsn, db_host=None, db_port=None, db_name=None,
                               db_user=None, db_password_prompt=False, db_connect_timeout=5)
        schema = (PROJECT_ROOT / "data_preprocessing/itviec_db/itviec_schema.sql").read_text(encoding="utf-8")
        connection.execute(schema, prepare=False)

        def count(view, run=None):
            query = sql.SQL("SELECT count(*) FROM staging.{}").format(sql.Identifier(view))
            if run is not None:
                query += sql.SQL(" WHERE crawl_run_id = %s")
            return connection.execute(query, (run,) if run is not None else None).fetchone()[0]

        def summary(run):
            return connection.execute("SELECT metadata->'staging' FROM raw.crawl_runs "
                                      "WHERE crawl_run_id=%s", (run,)).fetchone()[0]

        def snapshot_raw():
            return connection.execute("SELECT job_id, crawl_run_id, source_job_id, job_url, observed_at, "
                                      "raw_payload FROM raw.job_postings ORDER BY crawl_run_id, job_id").fetchall()

        def insert_run(run, source_ids, *, observed_at="2026-10-09T03:00:00+07:00", requested=None):
            requested = requested or len(source_ids)
            metadata = {"crawl_run_id": run, "started_at": observed_at,
                        "requested_count": requested, "collected_count": 0, "complete": False,
                        "scope": "Synthetic regression fixtures"}
            sink = crawler.PostgresSink(args)
            try:
                sink.start_run(metadata, [])
                for position, source_id in enumerate(source_ids, 1):
                    progress = {**metadata, "collected_count": position, "complete": position == requested}
                    check.assertTrue(sink.save_job(_raw(source_id, observed_at), progress, []))
                    metadata.update(collected_count=position, complete=position == requested)
                end = datetime.fromisoformat(observed_at) + timedelta(minutes=1)
                metadata["finished_at"] = end.isoformat()
                sink.finish_run(metadata, [])
            finally:
                sink.close()

        run = "fixture_first_run"
        insert_run(run, ["fixture_a", "fixture_b"])
        raw_before = snapshot_raw()
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        for view in ("jobs", "latest_jobs", "dwh_ready_jobs", "dwh_latest_jobs"):
            cursor = connection.execute(sql.SQL("SELECT * FROM staging.{}").format(sql.Identifier(view)))
            check.assertEqual(tuple(column.name for column in cursor.description), normalizer.NORMALIZED_FIELDS)
            check.assertEqual(len(cursor.fetchall()), 2)
        first_summary = summary(run)
        check.assertTrue(first_summary["ready_for_dwh"])
        check.assertEqual(first_summary["quality"]["validated_count"], 2)
        check.assertEqual(first_summary["quality"]["error_count"], 0)
        check.assertEqual(snapshot_raw(), raw_before)
        completed.append("raw storage, exact 30 fields, quality gate, and immutable source lineage")

        ids_before = connection.execute("SELECT job_id, observation_id FROM staging.job_observations "
                                        "WHERE crawl_run_id=%s ORDER BY job_id", (run,)).fetchall()
        jobs_before = connection.execute("SELECT * FROM staging.jobs ORDER BY job_id").fetchall()
        with mock.patch.object(crawler, "run", side_effect=AssertionError("Replay must not crawl")), \
             mock.patch.object(crawler, "new_crawl_run_id", side_effect=AssertionError("Replay must keep run ID")):
            check.assertEqual(pipeline.main(["--no-compose-db", "--db-dsn", dsn, "--process-run-id", run]), 0)
        check.assertEqual(connection.execute("SELECT job_id, observation_id FROM staging.job_observations "
                                            "WHERE crawl_run_id=%s ORDER BY job_id", (run,)).fetchall(), ids_before)
        check.assertEqual(connection.execute("SELECT * FROM staging.jobs ORDER BY job_id").fetchall(), jobs_before)
        check.assertEqual(snapshot_raw(), raw_before)
        completed.append("entrypoint replay is idempotent and never crawls")

        lock_name = "itviec:staging:" + run
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 0))", (lock_name,))
        try:
            summary_before = summary(run)
            check.assertNotEqual(normalizer.stage_database_run(run, args), 0)
            check.assertEqual(summary(run), summary_before)
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (lock_name,))
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("concurrent same-run processing is refused without clobbering quality metadata")

        real_normalize = normalizer.normalize_job
        def reject_first(raw, *positional, **keyword):
            if raw.get("source_job_id") == "fixture_a":
                raise normalizer.CrawlError("Synthetic rejection to verify stale-row removal.")
            return real_normalize(raw, *positional, **keyword)

        with mock.patch.object(normalizer, "normalize_job", side_effect=reject_first):
            check.assertEqual(normalizer.stage_database_run(run, args), 2)
        check.assertEqual(count("job_observations", run), 1)
        check.assertEqual(count("dwh_ready_jobs", run), 0)
        check.assertFalse(summary(run)["ready_for_dwh"])
        check.assertEqual(summary(run)["status"], "partial")
        check.assertEqual(snapshot_raw(), raw_before)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        check.assertEqual(count("dwh_ready_jobs", run), 2)
        completed.append("rejected replay removes stale observation and closes the complete batch gate")

        real_write = normalizer._database_write_job
        def violate_child_constraint(db, job):
            if job["job_id"] != "itviec:fixture_a":
                return real_write(db, job)
            with db.transaction():
                real_write(db, job)
                db.execute("INSERT INTO staging.job_skills "
                           "(observation_id,item_order,name,category,requirement_type) "
                           "SELECT observation_id,999,'Python','programming_language','required' "
                           "FROM staging.job_observations WHERE job_id=%s AND crawl_run_id=%s",
                           (job["job_id"], run))
        with mock.patch.object(normalizer, "_database_write_job", side_effect=violate_child_constraint):
            check.assertEqual(normalizer.stage_database_run(run, args), 2)
        check.assertEqual(count("job_observations", run), 1)
        check.assertEqual(count("dwh_ready_jobs", run), 0)
        check.assertTrue(any(error["stage"] == "staging_write" for error in summary(run)["errors"]))
        check.assertEqual(snapshot_raw(), raw_before)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("child constraint rollback cannot leave a stale observation eligible")

        real_children = normalizer._database_child_rows
        def omit_written_skill(job, observation_id):
            rows = real_children(job, observation_id)
            if job["job_id"] == "itviec:fixture_a":
                rows["job_skills"] = []
            return rows
        with mock.patch.object(normalizer, "_database_child_rows", side_effect=omit_written_skill):
            check.assertEqual(normalizer.stage_database_run(run, args), 2)
        check.assertEqual(count("dwh_ready_jobs", run), 0)
        check.assertGreater(summary(run)["quality"]["error_count"], 0)
        check.assertEqual(snapshot_raw(), raw_before)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("read-back quality gate catches missing child values after otherwise valid SQL writes")

        with mock.patch.object(normalizer, "_database_write_job", side_effect=psycopg.OperationalError(
                "Synthetic connection error; no connection details.")):
            check.assertEqual(normalizer.stage_database_run(run, args), 4)
        check.assertEqual(count("dwh_ready_jobs", run), 0)
        check.assertFalse(summary(run)["ready_for_dwh"])
        check.assertEqual(snapshot_raw(), raw_before)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("fatal storage failure leaves raw intact and never publishes an unfinished run")

        connection.execute("UPDATE raw.crawl_runs SET status='running' WHERE crawl_run_id=%s", (run,))
        check.assertNotEqual(normalizer.stage_database_run(run, args), 0)
        check.assertEqual(count("dwh_ready_jobs", run), 0)
        connection.execute("UPDATE raw.crawl_runs SET status='succeeded' WHERE crawl_run_id=%s", (run,))
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("active crawl cannot be staged or published")

        connection.execute("UPDATE staging.job_observations SET observed_at=observed_at+interval '1 second' "
                           "WHERE job_id='itviec:fixture_a' AND crawl_run_id=%s", (run,))
        check.assertEqual(count("dwh_ready_jobs", run), 1)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        connection.execute("UPDATE staging.job_observations SET normalization_version='1.0' "
                           "WHERE job_id='itviec:fixture_a' AND crawl_run_id=%s", (run,))
        check.assertEqual(count("dwh_ready_jobs", run), 1)
        check.assertEqual(normalizer.stage_database_run(run, args), 0)
        completed.append("DWH view rejects mismatched raw lineage and obsolete normalization version")

        second = "fixture_second_run"
        insert_run(second, ["fixture_a"], observed_at="2026-10-10T03:00:00+07:00")
        check.assertEqual(normalizer.stage_database_run(second, args), 0)
        check.assertEqual(count("dwh_ready_jobs"), 3)
        check.assertEqual(count("dwh_latest_jobs"), 2)
        latest_run = connection.execute("SELECT crawl_run_id FROM staging.dwh_latest_jobs "
                                        "WHERE job_id='itviec:fixture_a'").fetchone()[0]
        check.assertEqual(latest_run, second)
        completed.append("multiple crawl runs preserve history and latest view avoids duplicate jobs")

        partial_crawl = "fixture_partial_crawl"
        insert_run(partial_crawl, ["fixture_c"], requested=3)
        check.assertEqual(normalizer.stage_database_run(partial_crawl, args), 0)
        check.assertEqual(count("dwh_ready_jobs", partial_crawl), 1)
        check.assertTrue(summary(partial_crawl)["ready_for_dwh"])
        check.assertFalse(summary(partial_crawl)["source_complete"])
        completed.append("partial source coverage is separate from accepted row quality")

        final_raw = snapshot_raw()
        final_count = count("job_observations")
        ready_count = count("dwh_ready_jobs")
        connection.execute(schema, prepare=False)
        check.assertEqual(snapshot_raw(), final_raw)
        check.assertEqual(count("job_observations"), final_count)
        check.assertEqual(count("dwh_ready_jobs"), ready_count)
        completed.append("schema migration is repeatable with existing data")
        return completed
    finally:
        if connection is not None:
            connection.close()
        try:
            if created:
                # Sessions created by this harness should already be closed; terminate
                # only remaining sessions in our own temporary database if a check failed.
                admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                              "WHERE datname=%s AND pid<>pg_backend_pid()", (database_name,))
                admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database_name)))
        finally:
            admin.close()


@unittest.skipUnless(os.environ.get("ITVIEC_TEST_DSN"), "Set ITVIEC_TEST_DSN for isolated PostgreSQL integration")
class PostgreSQLRoundtripTests(unittest.TestCase):
    def test_full_pipeline_quality_and_replay(self):
        checks = run_postgres_checks(os.environ["ITVIEC_TEST_DSN"])
        self.assertGreaterEqual(len(checks), 9)


if __name__ == "__main__":
    unittest.main()
