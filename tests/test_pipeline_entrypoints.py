"""Pipeline entrypoints keep stage order and one PostgreSQL target."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import call, patch

from pipeline import load_to_dwh, main as pipeline_main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AirflowDagContractTests(TestCase):
    def test_pipeline_failures_are_not_masked_by_fallback_commands(self):
        dag_source = (PROJECT_ROOT / "dags" / "it_job_market_dag.py").read_text(encoding="utf-8")

        self.assertNotIn("||", dag_source)
        self.assertNotIn('trigger_rule="all_done"', dag_source)
        self.assertIn('DWH_PIPELINE_MODE', dag_source)
        self.assertIn('NORMALIZE_ONLY = " --normalize-only"', dag_source)
        self.assertIn('--mode {PIPELINE_MODE}', dag_source)
        self.assertIn(
            "exec python pipeline/main.py --source itviec --limit 5",
            dag_source,
        )
        self.assertIn(
            "exec python pipeline/main.py --source topdev --limit 10",
            dag_source,
        )
        self.assertIn("exec python load.py warehouse-v2 --apply", dag_source)
        self.assertIn("exec python load.py warehouse-v2-direct --apply", dag_source)
        self.assertRegex(
            dag_source,
            r"run_itviec_pipeline\s*>>\s*run_topdev_pipeline\s*>>\s*"
            r"load_dw_v2_direct\s*>>\s*publish_dw_v2",
        )

    def test_unified_compose_bootstraps_only_infrastructure_databases(self):
        compose = (PROJECT_ROOT / "docker" / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

        self.assertFalse((PROJECT_ROOT / "docker" / "docker-compose.airflow.yml").exists())
        self.assertIn("airflow-postgres:", compose)
        self.assertIn("POSTGRES_DB: airflow_db", compose)
        self.assertIn("airflow_postgres_data:/var/lib/postgresql/data", compose)
        self.assertNotIn("project-db-ready:", compose)
        self.assertIn("postgres:\n        condition: service_healthy", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn("sqlserver-db-init:", compose)
        self.assertIn("CREATE DATABASE [it_job_market_dw]", compose)
        self.assertIn("condition: service_completed_successfully", compose)


class PipelineEntrypointTests(TestCase):
    def test_full_pipeline_bypasses_analytics(self):
        with TemporaryDirectory() as directory:
            cookie = Path(directory) / "cookie.txt"
            cookie.touch()
            env = {"PGDATABASE": "it_job_market_v12", "DW_SQLSERVER_CONNECTION_STRING": "test"}
            with (patch.object(pipeline_main, "database_env", return_value=env),
                  patch.object(pipeline_main, "run_step", return_value=0) as run):
                status = pipeline_main.main([
                    "--cookie-file", str(cookie),
                    "--itviec-max-pages", "12",
                ])
            self.assertEqual(status, 0)
            self.assertEqual(run.call_args_list, [
                call([
                    "main_itviec.py", "--limit", "500", "--max-pages", "12",
                    "--cookie-file", str(cookie),
                ], env),
                call(["main_topdev.py", "--limit", "500"], env),
                call(["load.py", "normalize"], env),
                call(["load.py", "warehouse", "--apply"], env),
            ])

    def test_normalize_only_stops_after_postgres(self):
        env = {"PGDATABASE": "it_job_market_v12"}
        with (patch.object(pipeline_main, "database_env", return_value=env),
              patch.object(pipeline_main, "run_step", return_value=0) as run):
            self.assertEqual(pipeline_main.main(["--source", "topdev", "--normalize-only"]), 0)
        self.assertEqual(run.call_args_list, [
            call(["main_topdev.py", "--limit", "500"], env),
            call(["load.py", "normalize", "--source", "topdev"], env),
        ])

    def test_analytics_only_when_explicitly_requested(self):
        env = {"PGDATABASE": "it_job_market_v12"}
        with TemporaryDirectory() as directory:
            out = Path(directory) / "snapshot"
            with (patch.object(pipeline_main, "database_env", return_value=env),
                  patch.object(pipeline_main, "run_step", return_value=0) as run):
                self.assertEqual(pipeline_main.main(["--source", "topdev", "--out", str(out)]), 0)
            run.assert_called_with(["load.py", "analytics", "--apply", "--out", str(out), "--source", "topdev"], env)

    def test_single_source_and_fail_fast(self):
        env = {"PGDATABASE": "it_job_market_v12", "DW_SQLSERVER_CONNECTION_STRING": "test"}
        with (patch.object(pipeline_main, "database_env", return_value=env),
              patch.object(pipeline_main, "run_step", side_effect=[0, 7]) as run):
            status = pipeline_main.main(["--source", "topdev", "--limit", "3"])
        self.assertEqual(status, 7)
        self.assertEqual(run.call_args_list, [
            call(["main_topdev.py", "--limit", "3"], env),
            call(["load.py", "normalize", "--source", "topdev"], env),
        ])

    def test_source_filter_does_not_narrow_the_warehouse_snapshot(self):
        env = {"PGDATABASE": "it_job_market_v12", "DW_SQLSERVER_CONNECTION_STRING": "test"}
        with (patch.object(pipeline_main, "database_env", return_value=env),
              patch.object(pipeline_main, "run_step", return_value=0) as run):
            self.assertEqual(pipeline_main.main(["--source", "topdev"]), 0)
        self.assertEqual(run.call_args_list[-2:], [
            call(["load.py", "normalize", "--source", "topdev"], env),
            call(["load.py", "warehouse", "--apply"], env),
        ])

    def test_dwh_applies_by_default_and_previews_on_request(self):
        env = {"PGDATABASE": "it_job_market_v12", "DW_SQLSERVER_CONNECTION_STRING": "test"}
        with (patch.dict(load_to_dwh.os.environ, env, clear=True),
              patch.object(load_to_dwh, "database_env", return_value=env),
              patch.object(load_to_dwh, "run_step", return_value=0) as run):
            self.assertEqual(load_to_dwh.main([]), 0)
            self.assertEqual(load_to_dwh.main(["--preview"]), 0)
        self.assertEqual(run.call_args_list, [
            call(["load.py", "warehouse", "--apply"], env),
            call(["load.py", "warehouse"], env),
        ])

    def test_compose_config_selects_published_database(self):
        config = {"services": {"postgres": {
            "environment": {
                "POSTGRES_DB": "it_job_market_v12",
                "POSTGRES_USER": "admin",
                "POSTGRES_PASSWORD": "secret",
            },
            "ports": [{"target": 5432, "published": 55432, "host_ip": "127.0.0.1"}],
        }}}
        result = SimpleNamespace(returncode=0, stdout=json.dumps(config))
        with (patch.dict(pipeline_main.os.environ, {}, clear=True),
              patch.object(pipeline_main.subprocess, "run", return_value=result)):
            env = pipeline_main.database_env()
        self.assertEqual(
            {key: env[key] for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")},
            {"PGHOST": "127.0.0.1", "PGPORT": "55432", "PGDATABASE": "it_job_market_v12",
             "PGUSER": "admin", "PGPASSWORD": "secret"},
        )

    def test_database_url_overrides_old_pg_database_for_all_steps(self):
        with patch.dict(pipeline_main.os.environ, {
            "DATABASE_URL": "postgresql://alice:secret@127.0.0.1:5433/current_db",
            "PGDATABASE": "old_db",
        }, clear=True):
            env = pipeline_main.database_env()
        self.assertEqual(env["PGDATABASE"], "current_db")
        self.assertEqual(env["PGPORT"], "5433")
        self.assertEqual(env["PGUSER"], "alice")
