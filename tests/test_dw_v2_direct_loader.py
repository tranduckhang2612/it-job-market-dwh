"""Direct normalize_data-to-dw_v2 loader tests for phase 7A."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unittest

import data_preprocessing  # Adds src to sys.path for source-checkout runs.
from it_job_market_dwh.analytics.atomic import GROUPS
from it_job_market_dwh.loaders import warehouse_v2_direct as direct
from tests import test_dw_v2_schema as schema_support


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "it_job_market_dwh" / "loaders" / "warehouse_v2_direct.py").read_text(
    encoding="utf-8"
)
DAG_SOURCE = (ROOT / "dags" / "it_job_market_dag.py").read_text(encoding="utf-8")


def group_row(name, category="", level_text=None):
    return {"name": name, "category": category, "level_text": level_text}


def observation(run_id, observed_at, title="Python Developer"):
    groups = {group: [] for group in GROUPS}
    groups.update({
        "programming_language": [group_row("Python", "programming_language")],
        "framework": [group_row("Django", "framework_library")],
        "core_skill": [group_row("SQL", "database")],
        "role": [group_row("Software Engineer")],
        "city": [group_row("Hà Nội")],
        "foreign_language": [group_row("English", level_text="B2")],
        "level": [group_row("Senior")],
        "soft_skill": [group_row("Communication", "soft_skill")],
    })
    return {
        "scalar": {
            "job_id": "topdev:direct-1",
            "source": "topdev",
            "source_job_id": "direct-1",
            "job_url": "https://example.test/direct-1",
            "crawl_run_id": run_id,
            "observed_at": observed_at,
            "title": title,
            "company_name": "Example Company",
            "work_mode": "hybrid",
        },
        "groups": groups,
    }


class DirectLoaderStaticTests(unittest.TestCase):
    def test_loader_never_reads_or_writes_legacy_dw(self):
        self.assertNotIn("dw.fact_", SOURCE)
        self.assertNotIn("dw.dim_", SOURCE)
        self.assertNotIn("dw.sql", SOURCE)
        self.assertIn("normalize_data.normalized_jobs", SOURCE)

    def test_observation_identity_matches_migration_contract(self):
        expected = hashlib.sha256("topdev:1|run-1".encode("utf-16le")).hexdigest().upper()
        self.assertEqual(direct.observation_id("topdev:1", "run-1"), expected)

    def test_airflow_runs_direct_loader_before_shadow_publisher(self):
        self.assertIn("exec python load.py warehouse-v2-direct --apply", DAG_SOURCE)
        self.assertRegex(
            DAG_SOURCE,
            r"run_itviec_pipeline\s*>>\s*run_topdev_pipeline\s*>>\s*"
            r"load_dw_v2_direct\s*>>\s*publish_dw_v2",
        )


class DirectLoaderSqlServerTests(schema_support.DwV2SqlServerIntegrationTests):
    def test_direct_loader_preserves_history_and_is_idempotent(self):
        first = observation(
            "run-direct-1",
            datetime(2026, 11, 19, 1, 0, tzinfo=timezone.utc),
        )
        second = observation(
            "run-direct-2",
            datetime(2026, 11, 20, 1, 0, tzinfo=timezone.utc),
            title="Senior Python Developer",
        )

        result_one = direct.apply(self.conn, [first])
        result_two = direct.apply(self.conn, [second])
        before = {
            table: self.conn.execute(f"SELECT COUNT(*) FROM dw_v2.{table}").fetchone()[0]
            for table in (
                "fact_job_observation", "fact_job_necessary_skill", "fact_job_role_city",
            )
        }
        result_repeat = direct.apply(self.conn, [second])
        after = {
            table: self.conn.execute(f"SELECT COUNT(*) FROM dw_v2.{table}").fetchone()[0]
            for table in before
        }

        self.assertEqual(result_one["inserted"]["observations"], 1)
        self.assertEqual(result_two["inserted"]["observations"], 1)
        self.assertEqual(result_repeat["inserted"], {})
        self.assertEqual(before, {
            "fact_job_observation": 2,
            "fact_job_necessary_skill": 8,
            "fact_job_role_city": 2,
        })
        self.assertEqual(after, before)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM dw_v2.fact_job_observation WHERE work_mode=N'hybrid'"
            ).fetchone()[0],
            2,
        )
        self.assertIsNone(self.conn.execute("SELECT SCHEMA_ID(N'dw')").fetchone()[0])

    def test_empty_snapshot_fails_before_database_mutation(self):
        with self.assertRaisesRegex(ValueError, "Empty normalized snapshot"):
            direct.apply(self.conn, [])
        self.assertIsNone(self.conn.execute("SELECT SCHEMA_ID(N'dw_v2')").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
