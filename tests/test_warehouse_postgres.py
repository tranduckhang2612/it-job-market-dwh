"""Exercise the direct source reader in a real PostgreSQL database without analytics."""
import os
import unittest
import uuid
from datetime import timedelta
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from data_preprocessing.warehouse import job_values as warehouse
from test_analytics_atomic import sample


@unittest.skipUnless(os.environ.get("ITVIEC_TEST_DSN"), "Set ITVIEC_TEST_DSN for PostgreSQL integration")
class WarehousePostgresTests(unittest.TestCase):
    def setUp(self):
        options = conninfo_to_dict(os.environ["ITVIEC_TEST_DSN"])
        admin = psycopg.connect(**options, autocommit=True)
        self.addCleanup(admin.close)
        name = "dw_source_test_" + uuid.uuid4().hex
        db = sql.Identifier(name)
        admin.execute(sql.SQL("CREATE DATABASE {}").format(db))
        self.addCleanup(admin.execute, sql.SQL("DROP DATABASE {}").format(db))
        self.options = dict(options, dbname=name)
        self.conn = psycopg.connect(**self.options, autocommit=True)
        self.addCleanup(self.conn.close)
        for filename in ("raw_schema.sql", "normalize_data_schema.sql"):
            ddl = warehouse.DW_DDL.with_name(filename).read_text(encoding="utf-8")
            self.conn.execute(ddl, prepare=False)

    def insert(self, job):
        at, job_id, run = (job[field] for field in ("observed_at", "job_id", "crawl_run_id"))
        self.conn.execute("INSERT INTO raw.crawl_runs(crawl_run_id,source,started_at,requested_count) "
                          "VALUES (%s,%s,%s,1)", (run, job["source"], at))
        self.conn.execute("INSERT INTO raw.job_postings(job_id,crawl_run_id,job_url,observed_at,raw_payload) "
                          "VALUES (%s,%s,%s,%s,'{}'::jsonb)", (job_id, run, job["job_url"], at))
        columns = warehouse.NORMALIZED_COLUMNS
        params = [Jsonb(job[name]) if isinstance(job[name], (list, dict)) else job[name] for name in columns]
        statement = sql.SQL("INSERT INTO normalize_data.normalized_jobs ({}) VALUES ({})").format(
            sql.SQL(",").join(map(sql.Identifier, columns)), sql.SQL(",").join(sql.Placeholder() for _ in columns)
        )
        self.conn.execute(statement, params)

    def test_latest_per_job_and_duplicate_labels_without_analytics_schema(self):
        python = [{"name": "Python", "category": "programming_language"}]
        old = sample(run="run_a", title="Older observation")
        latest = sample(run="run_c", title="Latest Python observation", skills=python,
                        observed_at=old["observed_at"] + timedelta(hours=1),
                        foreign_languages=[{"language": "English", "level_text": "B2"},
                                           {"language": "English", "level_text": "C1"}])
        tied = dict(latest, crawl_run_id="run_b", title="Loses timestamp tie")
        second = sample(sid="2", source="itviec", run="run_d", skills=python)
        for job in (latest, tied, old, second):
            self.insert(job)
        self.assertIsNone(self.conn.execute("SELECT to_regnamespace('analytics')").fetchone()[0])
        connect = psycopg.connect
        with patch.object(warehouse.psycopg, "connect", side_effect=lambda _, **kwargs: connect(**self.options, **kwargs)):
            jobs, groups = warehouse.read_source()
        self.assertEqual(set(jobs), {"topdev:1", "itviec:2"})
        self.assertEqual(jobs["topdev:1"][1], "Latest Python observation")
        self.assertEqual(jobs["topdev:1"][4], "run_c")
        self.assertEqual(groups["programming_language"], [
            ("itviec:2", "Python", "programming_language", None),
            ("topdev:1", "Python", "programming_language", None),
        ])
        self.assertEqual({row[3] for row in groups["foreign_language"] if row[0] == "topdev:1"}, {"B2", "C1"})
        self.assertIsNone(self.conn.execute("SELECT to_regnamespace('analytics')").fetchone()[0])
