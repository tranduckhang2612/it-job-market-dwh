"""Operational BI/ML cutover tests for phase six."""

from pathlib import Path
import re
import unittest

import data_preprocessing  # Adds the src directory for source-checkout test runs.
from it_job_market_dwh.loaders import warehouse_v2
from tests import test_dw_v2_semantic_views as semantic_support


ROOT = Path(__file__).resolve().parents[1]
CUTOVER_SQL = (
    ROOT / "data_preprocessing" / "sql" / "dw_v2_consumer_cutover.sql"
).read_text(encoding="utf-8")
DIRECT_PUBLISH_SQL = (
    ROOT / "data_preprocessing" / "sql" / "dw_v2_direct_publish.sql"
).read_text(encoding="utf-8")
DAG_SOURCE = (ROOT / "dags" / "it_job_market_dag.py").read_text(encoding="utf-8")


class DwV2CutoverStaticTests(unittest.TestCase):
    def test_publisher_runs_in_guarded_order(self):
        self.assertEqual(warehouse_v2.SCRIPT_NAMES, (
            "dw_v2.sql",
            "dw_v2_backfill_dimensions.sql",
            "dw_v2_backfill_facts.sql",
            "dw_v2_semantic_views.sql",
            "dw_v2_consumer_cutover.sql",
        ))

    def test_direct_publisher_has_no_legacy_dependency(self):
        self.assertEqual(warehouse_v2.DIRECT_SCRIPT_NAMES, (
            "dw_v2.sql",
            "dw_v2_semantic_views.sql",
            "dw_v2_direct_publish.sql",
        ))
        self.assertNotRegex(DIRECT_PUBLISH_SQL, re.compile(r"\bdw\.", re.IGNORECASE))
        self.assertIn("Skill fact lineage does not match an observation", DIRECT_PUBLISH_SQL)
        self.assertIn("Role-city fact lineage does not match an observation", DIRECT_PUBLISH_SQL)
        self.assertIn("Semantic observation row count mismatch", DIRECT_PUBLISH_SQL)

    def test_airflow_publishes_only_after_both_sources(self):
        self.assertIn('task_id="publish_dw_v2"', DAG_SOURCE)
        self.assertIn("exec python load.py warehouse-v2 --apply", DAG_SOURCE)
        self.assertRegex(
            DAG_SOURCE,
            r"run_itviec_pipeline\s*>>\s*run_topdev_pipeline\s*>>\s*"
            r"load_dw_v2_direct\s*>>\s*publish_dw_v2",
        )
        self.assertIn('DWH_PIPELINE_MODE', DAG_SOURCE)
        self.assertIn('--mode {PIPELINE_MODE}', DAG_SOURCE)

    def test_cutover_validates_latest_snapshot_without_rejecting_history(self):
        self.assertIn("dw_v2_compat.fact_job", CUTOVER_SQL)
        self.assertIn("semantic.job_observation", CUTOVER_SQL)
        self.assertNotRegex(
            CUTOVER_SQL,
            re.compile(
                r"COUNT_BIG\(\*\) FROM dw\.fact_job\)\s*<>\s*"
                r"\(SELECT COUNT_BIG\(\*\) FROM dw_v2\.fact_job_observation",
                re.IGNORECASE,
            ),
        )

    def test_reader_role_is_semantic_only(self):
        self.assertIn("CREATE ROLE [bi_ml_reader]", CUTOVER_SQL)
        self.assertIn(
            "GRANT SELECT, VIEW DEFINITION ON SCHEMA::[semantic] TO [bi_ml_reader]",
            CUTOVER_SQL,
        )
        self.assertIn(
            "GRANT SELECT, VIEW DEFINITION ON SCHEMA::[dw_v2_compat] TO [bi_ml_reader]",
            CUTOVER_SQL,
        )
        self.assertIn("DENY SELECT ON SCHEMA::[dw_v2] TO [bi_ml_reader]", CUTOVER_SQL)


class DwV2CutoverSqlServerTests(
    semantic_support.DwV2SemanticViewSqlServerTests
):
    @staticmethod
    def consume(cursor):
        while True:
            if cursor.description is not None:
                cursor.fetchall()
            if not cursor.nextset():
                break

    def setUp(self):
        super().setUp()
        cursor = self.conn.cursor()
        cursor.execute(CUTOVER_SQL)
        self.consume(cursor)

    def test_cutover_is_idempotent_and_creates_reader_role(self):
        cursor = self.conn.cursor()
        cursor.execute(CUTOVER_SQL)
        self.consume(cursor)
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) FROM sys.database_principals "
                "WHERE name=N'bi_ml_reader' AND type=N'R'"
            ),
            [(1,)],
        )

    def test_reader_can_query_views_but_not_physical_facts(self):
        import pyodbc

        cursor = self.conn.cursor()
        cursor.execute("CREATE USER [phase6_reader_test] WITHOUT LOGIN")
        cursor.execute("ALTER ROLE [bi_ml_reader] ADD MEMBER [phase6_reader_test]")
        cursor.execute("EXECUTE AS USER=N'phase6_reader_test'")
        try:
            self.assertEqual(
                cursor.execute("SELECT COUNT(*) FROM semantic.job_observation").fetchone()[0],
                2,
            )
            with self.assertRaises(pyodbc.Error):
                cursor.execute("SELECT COUNT(*) FROM dw_v2.fact_job_observation").fetchone()
        finally:
            cursor.execute("REVERT")

    def test_cutover_allows_older_history_beyond_current_snapshot(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO dw_v2.dim_crawl_run("
            "crawl_run_id,source_scope,pipeline_version,started_at_utc,finished_at_utc,run_status) "
            "VALUES(N'phase6:older',N'test',N'test',SYSUTCDATETIME(),SYSUTCDATETIME(),N'succeeded')"
        )
        cursor.execute(
            "INSERT INTO dw_v2.fact_job_observation("
            "observation_id,job_key,date_key,source_key,company_key,crawl_run_key,"
            "observed_at_utc,job_title_snapshot,work_mode) "
            "SELECT N'phase6:older-observation',job_key,date_key,source_key,company_key,"
            "(SELECT crawl_run_key FROM dw_v2.dim_crawl_run WHERE crawl_run_id=N'phase6:older'),"
            "DATEADD(day,-1,observed_at_utc),job_title_snapshot,work_mode "
            "FROM dw_v2.fact_job_observation WHERE job_observation_key=("
            "SELECT MIN(job_observation_key) FROM dw_v2.fact_job_observation)"
        )
        cursor.execute(CUTOVER_SQL)
        self.consume(cursor)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.fact_job"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM semantic.job_observation"), [(3,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_compat.fact_job"), [(2,)])


class DwV2DirectPublishSqlServerTests(
    semantic_support.DwV2SemanticViewSqlServerTests
):
    @staticmethod
    def consume(cursor):
        while True:
            if cursor.description is not None:
                cursor.fetchall()
            if not cursor.nextset():
                break

    def run_direct_publish(self):
        cursor = self.conn.cursor()
        cursor.execute(DIRECT_PUBLISH_SQL)
        self.consume(cursor)

    def test_direct_publish_is_idempotent(self):
        self.run_direct_publish()
        self.run_direct_publish()
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) FROM sys.database_principals "
                "WHERE name=N'bi_ml_reader' AND type=N'R'"
            ),
            [(1,)],
        )

    def test_direct_publish_rejects_broken_child_lineage(self):
        import pyodbc

        self.conn.cursor().execute(
            "UPDATE dw_v2.fact_job_necessary_skill SET observation_id=N'broken-lineage' "
            "WHERE job_necessary_skill_key=("
            "SELECT MIN(job_necessary_skill_key) FROM dw_v2.fact_job_necessary_skill)"
        )
        self.conn.commit()
        with self.assertRaises(pyodbc.Error):
            self.run_direct_publish()
        self.conn.rollback()


if __name__ == "__main__":
    unittest.main()
