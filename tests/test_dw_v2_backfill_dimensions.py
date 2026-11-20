"""Contract and SQL Server tests for phase-two dimension backfill."""
import os
from pathlib import Path
import re
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DDL = (ROOT / "data_preprocessing" / "sql" / "dw_v2.sql").read_text(encoding="utf-8")
BACKFILL_DDL = (
    ROOT / "data_preprocessing" / "sql" / "dw_v2_backfill_dimensions.sql"
).read_text(encoding="utf-8")
LEGACY_DDL = (ROOT / "data_preprocessing" / "sql" / "dw.sql").read_text(encoding="utf-8")
SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")


class DwV2BackfillStaticTests(unittest.TestCase):
    def test_legacy_schema_is_read_only(self):
        self.assertNotRegex(
            BACKFILL_DDL,
            r"(?im)^\s*(ALTER|DROP|TRUNCATE|DELETE|UPDATE|INSERT\s+INTO)\s+dw\.",
        )

    def test_creates_required_mapping_tables(self):
        mappings = set(
            re.findall(r"CREATE TABLE dw_v2_migration\.([a-z_]+)", BACKFILL_DDL)
        )
        self.assertEqual(
            mappings,
            {"map_job", "map_skill", "map_role", "map_city", "map_proficiency"},
        )

    def test_reconciliation_is_mandatory(self):
        for message in (
            "Job dimension reconciliation failed",
            "Skill dimension reconciliation failed",
            "Role dimension reconciliation failed",
            "City dimension reconciliation failed",
            "Proficiency dimension reconciliation failed",
        ):
            with self.subTest(message=message):
                self.assertIn(message, BACKFILL_DDL)

    def test_phase_two_does_not_load_facts(self):
        self.assertNotRegex(BACKFILL_DDL, r"INSERT\s+INTO\s+dw_v2\.fact_",)


@unittest.skipUnless(
    SQLSERVER_TEST_DSN,
    "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration",
)
class DwV2BackfillSqlServerTests(unittest.TestCase):
    def setUp(self):
        import pyodbc

        self.admin = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=True)
        self.addCleanup(self.admin.close)
        self.admin.execute("USE master")
        self.db = "dw_v2_backfill_test_" + uuid.uuid4().hex
        self.admin.execute(f"CREATE DATABASE [{self.db}]")
        self.addCleanup(self.drop_database)
        self.conn = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=False)
        self.addCleanup(self.conn.close)
        self.conn.execute(f"USE [{self.db}]")
        self.conn.commit()
        self.execute_batch(LEGACY_DDL)
        self.execute_batch(SCHEMA_DDL)
        self.seed_legacy()

    def drop_database(self):
        self.admin.execute(
            f"ALTER DATABASE [{self.db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"DROP DATABASE [{self.db}];"
        )

    def execute_batch(self, sql):
        cursor = self.conn.cursor()
        cursor.execute(sql)
        while cursor.nextset():
            pass
        self.conn.commit()

    def rows(self, query):
        return [tuple(row) for row in self.conn.cursor().execute(query)]

    def seed_legacy(self):
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO dw.dim_date(date_key,calendar_date) VALUES(20240101,'2024-01-01')"
        )
        for job_id, source, title, company, url, run, hour in (
            ("topdev:1", "topdev", "Python Developer", "Example Co", "https://e/1", "run-1", 8),
            ("itviec:2", "itviec", "Data Engineer", "Example Co", "https://e/2", "run-1", 9),
        ):
            cursor.execute(
                "INSERT INTO dw.fact_job(job_id,source,title,company_name,job_url,crawl_run_id,"
                "observed_at_utc,observed_date_key) "
                "VALUES(?,?,?,?,?,?,DATEADD(hour,?,'2024-01-01'),20240101)",
                job_id, source, title, company, url, run, hour,
            )
        job_keys = dict(self.rows("SELECT job_id,fact_job_key FROM dw.fact_job"))
        for job_key in job_keys.values():
            cursor.execute(
                "INSERT INTO dw.dim_role(fact_job_key,name,category) "
                "VALUES(?,N'Data Engineer',N'Data/AI')",
                job_key,
            )
            cursor.execute(
                "INSERT INTO dw.dim_city(fact_job_key,name) VALUES(?,N'Hà Nội')",
                job_key,
            )
            cursor.execute(
                "INSERT INTO dw.dim_programming_language(fact_job_key,name,category) "
                "VALUES(?,N'Python',N'programming_language')",
                job_key,
            )
        cursor.execute(
            "INSERT INTO dw.dim_necessary_skill(skill_type,name,category) "
            "VALUES(N'programming_language',N'Python',N'programming_language')"
        )
        skill_key = cursor.execute(
            "SELECT necessary_skill_key FROM dw.dim_necessary_skill"
        ).fetchone()[0]
        for context_no, job_key in enumerate(job_keys.values()):
            cursor.execute(
                "INSERT INTO dw.fact_job_necessary_skill("
                "fact_job_key,necessary_skill_key,context_no,level_text) VALUES(?,?,?,N'B2')",
                job_key, skill_key, context_no,
            )
        self.conn.commit()

    def test_backfill_deduplicates_dimensions_and_is_idempotent(self):
        legacy_counts = {
            table: self.rows(f"SELECT COUNT(*) FROM dw.{table}")[0][0]
            for table in ("fact_job", "dim_role", "dim_city", "dim_necessary_skill")
        }

        self.execute_batch(BACKFILL_DDL)
        first = {
            table: self.rows(f"SELECT COUNT(*) FROM dw_v2.{table}")[0][0]
            for table in ("dim_job", "dim_company", "dim_skill", "dim_role", "dim_city")
        }
        self.execute_batch(BACKFILL_DDL)
        second = {
            table: self.rows(f"SELECT COUNT(*) FROM dw_v2.{table}")[0][0]
            for table in first
        }

        self.assertEqual(first, second)
        self.assertEqual(first["dim_job"], 2)
        self.assertEqual(first["dim_company"], 2)  # Unknown + Example Co.
        self.assertEqual(first["dim_skill"], 2)  # Unknown + Python.
        self.assertEqual(first["dim_role"], 2)  # Unknown + one conformed role.
        self.assertEqual(first["dim_city"], 2)  # Unknown + one conformed city.
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_migration.map_job"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_migration.map_role"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_migration.map_city"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_migration.map_skill"), [(1,)])
        self.assertEqual(
            {
                table: self.rows(f"SELECT COUNT(*) FROM dw.{table}")[0][0]
                for table in legacy_counts
            },
            legacy_counts,
        )


if __name__ == "__main__":
    unittest.main()
