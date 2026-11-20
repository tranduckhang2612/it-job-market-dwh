"""Contract and SQL Server tests for phase-three fact backfill."""
import os
from pathlib import Path
import re
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
SQL_ROOT = ROOT / "data_preprocessing" / "sql"
LEGACY_DDL = (SQL_ROOT / "dw.sql").read_text(encoding="utf-8")
SCHEMA_DDL = (SQL_ROOT / "dw_v2.sql").read_text(encoding="utf-8")
DIMENSION_DDL = (SQL_ROOT / "dw_v2_backfill_dimensions.sql").read_text(encoding="utf-8")
FACT_DDL = (SQL_ROOT / "dw_v2_backfill_facts.sql").read_text(encoding="utf-8")
SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")


class DwV2FactBackfillStaticTests(unittest.TestCase):
    def test_legacy_schema_is_read_only(self):
        self.assertNotRegex(
            FACT_DDL,
            r"(?im)^\s*(ALTER|DROP|TRUNCATE|DELETE|UPDATE|INSERT\s+INTO)\s+dw\.",
        )

    def test_v2_facts_are_append_only(self):
        self.assertNotRegex(
            FACT_DDL,
            r"(?im)^\s*(DELETE|UPDATE|TRUNCATE)\s+.*dbo\.fact_",
        )
        inserts = set(re.findall(r"INSERT INTO dbo\.(fact_[a-z_]+)", FACT_DDL))
        self.assertEqual(
            inserts,
            {"fact_job_observation", "fact_job_necessary_skill", "fact_job_role_city"},
        )

    def test_mapping_collapse_and_reconciliation_are_guarded(self):
        for message in (
            "Observation fact reconciliation failed",
            "Skill mapping would collapse legacy occurrences",
            "Skill fact reconciliation failed",
            "Role-city mapping would collapse legacy occurrences",
            "Role-city fact reconciliation failed",
        ):
            with self.subTest(message=message):
                self.assertIn(message, FACT_DDL)


@unittest.skipUnless(
    SQLSERVER_TEST_DSN,
    "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration",
)
class DwV2FactBackfillSqlServerTests(unittest.TestCase):
    def setUp(self):
        import pyodbc

        self.admin = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=True)
        self.addCleanup(self.admin.close)
        self.admin.execute("USE master")
        self.db = "dw_v2_fact_test_" + uuid.uuid4().hex
        self.admin.execute(f"CREATE DATABASE [{self.db}]")
        self.addCleanup(self.drop_database)
        self.conn = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=False)
        self.addCleanup(self.conn.close)
        self.conn.execute(f"USE [{self.db}]")
        self.conn.commit()
        self.execute_batch(LEGACY_DDL)
        self.execute_batch(SCHEMA_DDL)
        self.seed_legacy()
        self.execute_batch(DIMENSION_DDL)

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
        profile_keys = {}
        for job_id, job_key in job_keys.items():
            cursor.execute(
                "INSERT INTO dw.dim_role(fact_job_key,name,category) "
                "OUTPUT inserted.role_key VALUES(?,N'Data Engineer',N'Data/AI')",
                job_key,
            )
            role_key = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO dw.dim_city(fact_job_key,name) OUTPUT inserted.city_key "
                "VALUES(?,N'Hà Nội')",
                job_key,
            )
            city_key = cursor.fetchone()[0]
            profile_keys[job_id] = (role_key, city_key)
            cursor.execute(
                "INSERT INTO dw.dim_programming_language(fact_job_key,name,category) "
                "VALUES(?,N'Python',N'programming_language')",
                job_key,
            )
        cursor.execute(
            "INSERT INTO dw.dim_necessary_skill(skill_type,name,category) "
            "OUTPUT inserted.necessary_skill_key "
            "VALUES(N'programming_language',N'Python',N'programming_language')"
        )
        skill_key = cursor.fetchone()[0]
        for job_id, job_key in job_keys.items():
            cursor.execute(
                "INSERT INTO dw.fact_job_necessary_skill("
                "fact_job_key,necessary_skill_key,context_no,level_text) VALUES(?,?,0,N'B2')",
                job_key, skill_key,
            )
            role_key, city_key = profile_keys[job_id]
            cursor.execute(
                "INSERT INTO dw.fact_job_role_city(fact_job_key,role_key,city_key) "
                "VALUES(?,?,?)",
                job_key, role_key, city_key,
            )
        self.conn.commit()

    def test_backfill_preserves_all_fact_grains_and_is_idempotent(self):
        legacy = {
            "fact_job_observation": self.rows("SELECT COUNT(*) FROM dw.fact_job")[0][0],
            "fact_job_necessary_skill": self.rows(
                "SELECT COUNT(*) FROM dw.fact_job_necessary_skill"
            )[0][0],
            "fact_job_role_city": self.rows("SELECT COUNT(*) FROM dw.fact_job_role_city")[0][0],
        }

        self.execute_batch(FACT_DDL)
        first = {
            fact: self.rows(f"SELECT COUNT(*) FROM dbo.{fact}")[0][0]
            for fact in legacy
        }
        self.execute_batch(FACT_DDL)
        second = {
            fact: self.rows(f"SELECT COUNT(*) FROM dbo.{fact}")[0][0]
            for fact in legacy
        }

        self.assertEqual(first, legacy)
        self.assertEqual(second, legacy)
        self.assertEqual(
            self.rows(
                "SELECT SUM(job_posting_count) FROM dbo.fact_job_observation"
            ),
            [(2,)],
        )
        self.assertEqual(
            self.rows(
                "SELECT SUM(skill_mention_count) FROM dbo.fact_job_necessary_skill"
            ),
            [(2,)],
        )
        self.assertEqual(
            self.rows(
                "SELECT SUM(combination_count) FROM dbo.fact_job_role_city"
            ),
            [(2,)],
        )


if __name__ == "__main__":
    unittest.main()
