"""Tests cho main_topdev.py entry point và pipeline."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import main_topdev as pipeline


class TopDevMainEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.crawl_args = None
        self.stage_args = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(
            pipeline, "_configure_compose_database", side_effect=self.configure))
        self.stack.enter_context(mock.patch.object(pipeline, "check_database", side_effect=self.check))
        self.stack.enter_context(mock.patch.object(
            pipeline.crawl_topdev, "new_crawl_run_id", return_value="fixture_topdev_run"))
        self.stack.enter_context(mock.patch.object(pipeline.crawl_topdev, "run", side_effect=self.crawl))
        self.stack.enter_context(mock.patch.object(
            pipeline.normalize_topdev, "stage_database_run", side_effect=self.stage))

    def configure(self, args):
        self.events.append("configure")
        return "external"

    def check(self, args):
        self.events.append("check")
        return 0

    def crawl(self, args):
        self.events.append("crawl")
        self.crawl_args = args
        args.crawl_result = {"crawl_run_id": args.crawl_run_id, "collected_count": args.limit}
        return 0

    def stage(self, run_id, args):
        self.events.append("stage")
        self.assertEqual(run_id, args.crawl_run_id)
        self.stage_args = args
        return 0

    def test_default_pipeline_runs_all_steps(self):
        with mock.patch.object(sys, "argv", ["main_topdev.py"]):
            exit_code = pipeline.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.events, ["configure", "check", "crawl", "stage"])
        self.assertEqual(self.crawl_args.limit, 20)
        self.assertEqual(self.stage_args.crawl_run_id, "fixture_topdev_run")

    def test_check_db_flag_exits_early(self):
        with mock.patch.object(sys, "argv", ["main_topdev.py", "--check-db"]):
            exit_code = pipeline.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.events, ["configure", "check"])

    def test_process_run_id_skips_crawl(self):
        with mock.patch.object(sys, "argv", ["main_topdev.py", "--process-run-id", "existing_run_123"]):
            exit_code = pipeline.main()
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.events, ["configure", "check", "stage"])
        self.assertEqual(self.stage_args.crawl_run_id, "existing_run_123")


if __name__ == "__main__":
    unittest.main()
