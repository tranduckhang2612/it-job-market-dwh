"""Contract tests for the phase-one Kimball schema."""
import os
from pathlib import Path
import re
import unittest
import uuid


DDL_PATH = Path(__file__).resolve().parents[1] / "data_preprocessing" / "sql" / "dw_v2.sql"
DDL = DDL_PATH.read_text(encoding="utf-8")
SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")

DIMENSIONS = {
    "dim_date",
    "dim_job",
    "dim_source",
    "dim_company",
    "dim_crawl_run",
    "dim_skill",
    "dim_role",
    "dim_city",
    "dim_proficiency_level",
}
FACTS = {
    "fact_job_observation",
    "fact_job_necessary_skill",
    "fact_job_role_city",
}


class DwV2StaticContractTests(unittest.TestCase):
    def test_creates_expected_dimensions_and_facts(self):
        created = set(re.findall(r"CREATE TABLE dw_v2\.([a-z_]+)", DDL))
        self.assertEqual(created, DIMENSIONS | FACTS)

    def test_facts_never_reference_other_facts(self):
        self.assertNotRegex(DDL, r"REFERENCES\s+dw_v2\.fact_",)

    def test_phase_one_does_not_mutate_legacy_schema(self):
        self.assertNotRegex(DDL, r"(?im)^\s*(ALTER|DROP|TRUNCATE|DELETE|UPDATE)\s+.*\bdw\.")
        self.assertNotIn("CREATE SCHEMA dw\n", DDL)

    def test_fact_grains_are_enforced(self):
        expected = (
            "UNIQUE(job_key, crawl_run_key)",
            "UNIQUE(job_key, crawl_run_key, skill_key, context_no)",
            "UNIQUE(job_key, crawl_run_key, role_key, city_key)",
        )
        compact = re.sub(r"\s+", " ", DDL)
        for constraint in expected:
            with self.subTest(constraint=constraint):
                self.assertIn(constraint, compact)

    def test_all_facts_carry_shared_conformed_dimension_keys(self):
        for fact in FACTS:
            match = re.search(
                rf"CREATE TABLE dw_v2\.{fact}\s*\((.*?)\n\s*\);",
                DDL,
                flags=re.DOTALL,
            )
            self.assertIsNotNone(match, fact)
            body = match.group(1)
            for column in ("job_key", "date_key", "source_key", "crawl_run_key"):
                with self.subTest(fact=fact, column=column):
                    self.assertRegex(body, rf"\b{column}\b")


@unittest.skipUnless(
    SQLSERVER_TEST_DSN,
    "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration",
)
class DwV2SqlServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        import pyodbc

        self.admin = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=True)
        self.addCleanup(self.admin.close)
        self.admin.execute("USE master")
        self.db = "dw_v2_test_" + uuid.uuid4().hex
        self.admin.execute(f"CREATE DATABASE [{self.db}]")
        self.addCleanup(self.drop_database)
        self.conn = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=False)
        self.addCleanup(self.conn.close)
        self.conn.execute(f"USE [{self.db}]")
        self.conn.commit()

    def drop_database(self):
        self.admin.execute(
            f"ALTER DATABASE [{self.db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
            f"DROP DATABASE [{self.db}];"
        )

    def execute_ddl(self):
        cursor = self.conn.cursor()
        cursor.execute(DDL)
        while cursor.nextset():
            pass
        self.conn.commit()

    def rows(self, query):
        return [tuple(row) for row in self.conn.cursor().execute(query)]

    def test_schema_is_idempotent_and_leaves_legacy_objects_untouched(self):
        self.conn.execute("CREATE SCHEMA dw")
        self.conn.execute("CREATE TABLE dw.phase_one_marker(id int NOT NULL PRIMARY KEY)")
        self.conn.execute("INSERT INTO dw.phase_one_marker VALUES(1)")
        self.conn.commit()

        self.execute_ddl()
        self.execute_ddl()

        tables = {
            name
            for (name,) in self.rows(
                "SELECT name FROM sys.tables WHERE schema_id=SCHEMA_ID('dw_v2')"
            )
        }
        self.assertEqual(tables, DIMENSIONS | FACTS)
        self.assertEqual(self.rows("SELECT * FROM dw.phase_one_marker"), [(1,)])
        self.assertEqual(
            self.rows(
                "SELECT COUNT(*) FROM dw_v2.dim_source WHERE source_key=0"
            ),
            [(1,)],
        )

    def test_fact_foreign_keys_target_dimensions_only(self):
        self.execute_ddl()
        targets = {
            (parent, referenced)
            for parent, referenced in self.rows(
                "SELECT OBJECT_NAME(f.parent_object_id), OBJECT_NAME(f.referenced_object_id) "
                "FROM sys.foreign_keys f "
                "WHERE OBJECT_SCHEMA_NAME(f.parent_object_id)='dw_v2' "
                "AND OBJECT_NAME(f.parent_object_id) LIKE 'fact_%'"
            )
        }
        self.assertTrue(targets)
        self.assertTrue(all(parent in FACTS for parent, _ in targets))
        self.assertTrue(all(referenced in DIMENSIONS for _, referenced in targets))


if __name__ == "__main__":
    unittest.main()
