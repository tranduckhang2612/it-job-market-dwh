"""Semantic and compatibility view tests for phase five."""
import os
from pathlib import Path
import re
import unittest

from tests import test_dw_v2_backfill_facts as fact_support


ROOT = Path(__file__).resolve().parents[1]
VIEWS_DDL = (
    ROOT / "data_preprocessing" / "sql" / "dw_v2_semantic_views.sql"
).read_text(encoding="utf-8")
SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")

SEMANTIC_VIEWS = {
    "job_observation",
    "latest_job",
    "job_skill",
    "job_role_city",
    "skill_demand_daily",
    "role_city_demand_daily",
}
COMPAT_VIEWS = {
    "fact_job",
    "dim_necessary_skill",
    "fact_job_necessary_skill",
    "job_necessary_skill",
    "fact_job_role_city",
    "job_role_city",
}


class DwV2SemanticViewStaticTests(unittest.TestCase):
    def test_creates_expected_views(self):
        found = re.findall(
            r"CREATE OR ALTER VIEW (semantic|dw_v2_compat)\.([a-z_]+)",
            VIEWS_DDL,
        )
        by_schema = {"semantic": set(), "dw_v2_compat": set()}
        for schema, view in found:
            by_schema[schema].add(view)
        self.assertEqual(by_schema["semantic"], SEMANTIC_VIEWS)
        self.assertEqual(by_schema["dw_v2_compat"], COMPAT_VIEWS)

    def test_compatibility_views_select_latest_observation(self):
        self.assertGreaterEqual(VIEWS_DDL.count("ROW_NUMBER() OVER"), 5)
        self.assertGreaterEqual(VIEWS_DDL.count("observation_rank = 1"), 5)

    def test_daily_views_expose_distinct_job_counts(self):
        self.assertEqual(VIEWS_DDL.count("COUNT(DISTINCT fact.job_key)"), 2)


@unittest.skipUnless(
    SQLSERVER_TEST_DSN,
    "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration",
)
class DwV2SemanticViewSqlServerTests(fact_support.DwV2FactBackfillSqlServerTests):
    def setUp(self):
        super().setUp()
        self.execute_batch(fact_support.FACT_DDL)
        self.execute_batch(VIEWS_DDL)

    def view_names(self, schema):
        return {
            name
            for (name,) in self.rows(
                "SELECT name FROM sys.views WHERE schema_id=SCHEMA_ID(?)",
                schema,
            )
        }

    def rows(self, query, *params):
        return [tuple(row) for row in self.conn.cursor().execute(query, *params)]

    def test_views_are_idempotent_and_preserve_fact_counts(self):
        self.execute_batch(VIEWS_DDL)
        self.assertEqual(self.view_names("semantic"), SEMANTIC_VIEWS)
        self.assertEqual(self.view_names("dw_v2_compat"), COMPAT_VIEWS)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM semantic.job_observation"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM semantic.job_skill"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM semantic.job_role_city"), [(2,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw_v2_compat.fact_job"), [(2,)])
        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM dw_v2_compat.fact_job_necessary_skill"),
            [(2,)],
        )
        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM dw_v2_compat.fact_job_role_city"),
            [(2,)],
        )

    def test_compatibility_columns_match_legacy_read_contract(self):
        expected = {
            "fact_job": {
                "fact_job_key", "job_id", "source", "title", "company_name", "job_url",
                "crawl_run_id", "observed_at_utc", "observed_date_key", "job_posting_count",
            },
            "fact_job_necessary_skill": {
                "fact_job_necessary_skill_key", "fact_job_key", "necessary_skill_key",
                "context_no", "level_text", "skill_mention_count",
            },
            "fact_job_role_city": {
                "fact_job_role_city_key", "fact_job_key", "role_key", "city_key",
                "combination_count",
            },
        }
        for view, columns in expected.items():
            actual = {
                name
                for (name,) in self.rows(
                    "SELECT name FROM sys.columns "
                    "WHERE object_id=OBJECT_ID(?)",
                    f"dw_v2_compat.{view}",
                )
            }
            with self.subTest(view=view):
                self.assertEqual(actual, columns)


if __name__ == "__main__":
    unittest.main()
