"""Read-only reconciliation tests for phase four."""
import os
from pathlib import Path
import re
import unittest

from tests import test_dw_v2_backfill_facts as fact_support


ROOT = Path(__file__).resolve().parents[1]
RECONCILE_DDL = (
    ROOT / "data_preprocessing" / "sql" / "dw_v2_reconcile.sql"
).read_text(encoding="utf-8")
SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")


class DwV2ReconcileStaticTests(unittest.TestCase):
    def test_script_is_read_only(self):
        self.assertNotRegex(
            RECONCILE_DDL,
            r"(?im)^\s*(INSERT|UPDATE|DELETE|MERGE|ALTER|CREATE|DROP|TRUNCATE)\b",
        )

    def test_checks_rows_measures_grains_and_dashboards(self):
        for message in (
            "Observation row count mismatch",
            "Skill fact row count mismatch",
            "Role-city fact row count mismatch",
            "Observation measure mismatch",
            "Skill measure mismatch",
            "Role-city measure mismatch",
            "Duplicate observation grain detected",
            "Duplicate skill grain detected",
            "Duplicate role-city grain detected",
            "Observation anti-join mismatch",
            "Skill fact anti-join mismatch",
            "Role-city fact anti-join mismatch",
            "Dashboard reconciliation failed for jobs by date and source",
            "Dashboard reconciliation failed for skill demand",
            "Dashboard reconciliation failed for role-city demand",
        ):
            with self.subTest(message=message):
                self.assertIn(message, RECONCILE_DDL)


@unittest.skipUnless(
    SQLSERVER_TEST_DSN,
    "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration",
)
class DwV2ReconcileSqlServerTests(fact_support.DwV2FactBackfillSqlServerTests):
    def setUp(self):
        super().setUp()
        self.execute_batch(fact_support.FACT_DDL)

    def test_reconciliation_passes_for_equivalent_models(self):
        self.execute_batch(RECONCILE_DDL)

    def test_reconciliation_rejects_fact_drift(self):
        import pyodbc

        self.conn.execute(
            "DELETE FROM dw_v2.fact_job_necessary_skill WHERE job_necessary_skill_key=("
            "SELECT MIN(job_necessary_skill_key) FROM dw_v2.fact_job_necessary_skill)"
        )
        self.conn.commit()
        with self.assertRaises(pyodbc.Error):
            self.execute_batch(RECONCILE_DDL)


if __name__ == "__main__":
    unittest.main()
