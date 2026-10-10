"""Check the one-command entry point without HTTP, DB writes or real cookies."""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import main_itviec as pipeline


class MainEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.default_path = pipeline.DEFAULT_COOKIE_FILE
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cookie = Path(self.temp.name) / "fixture_cookie.txt"
        self.cookie.touch()
        self.events = []
        self.crawl_args = None
        self.stage_args = None
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(pipeline, "DEFAULT_COOKIE_FILE", self.cookie))
        self.stack.enter_context(mock.patch.object(
            pipeline, "_configure_compose_database", side_effect=self.configure))
        self.stack.enter_context(mock.patch.object(pipeline, "check_database", side_effect=self.check))
        self.stack.enter_context(mock.patch.object(
            pipeline.crawl_itviec, "new_crawl_run_id", return_value="fixture_new_run"))
        self.stack.enter_context(mock.patch.object(pipeline.crawl_itviec, "run", side_effect=self.crawl))
        self.stack.enter_context(mock.patch.object(
            pipeline.normalize_itviec, "stage_database_run", side_effect=self.stage))

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

    def test_no_arguments_runs_all_steps_with_twenty_jobs_and_saved_cookie(self):
        with mock.patch.object(sys, "argv", ["main_itviec.py"]):
            self.assertEqual(pipeline.main(), 0)
        self.assertEqual(self.events, ["configure", "check", "crawl", "stage"])
        self.assertEqual(self.crawl_args.limit, 20)
        self.assertEqual(self.crawl_args.cookie_file, self.cookie)
        self.assertFalse(self.crawl_args.browser)
        self.assertFalse(self.crawl_args.save_json)
        self.assertFalse(self.crawl_args.save_html)
        self.assertIs(self.crawl_args, self.stage_args)
        self.assertEqual(self.stage_args.crawl_run_id, "fixture_new_run")

    def test_explicit_cookie_and_limit_override_defaults(self):
        alternate = Path(self.temp.name) / "alternate_cookie.txt"
        self.cookie.unlink()
        self.assertEqual(pipeline.main(["--cookie-file", str(alternate), "--limit", "5"]), 0)
        self.assertEqual(self.crawl_args.cookie_file, alternate)
        self.assertEqual(self.crawl_args.limit, 5)

    def test_missing_default_cookie_stops_before_database_or_crawl(self):
        self.cookie.unlink()
        with self.assertLogs("itviec.pipeline", level="ERROR") as logs:
            self.assertEqual(pipeline.main([]), 1)
        self.assertIn(str(self.cookie), "\n".join(logs.output))
        self.assertEqual(self.events, [])

    def test_database_check_needs_no_cookie_and_does_not_crawl(self):
        self.cookie.unlink()
        self.assertEqual(pipeline.main(["--check-db"]), 0)
        self.assertEqual(self.events, ["configure", "check"])

    def test_reprocessing_needs_no_cookie_and_stages_existing_run(self):
        self.cookie.unlink()
        self.assertEqual(pipeline.main(["--process-run-id", "fixture_existing_run"]), 0)
        self.assertEqual(self.events, ["configure", "check", "stage"])
        self.assertEqual(self.stage_args.crawl_run_id, "fixture_existing_run")
        self.assertIsNone(self.stage_args.cookie_file)

    def test_browser_and_login_keep_their_own_session_behavior(self):
        self.cookie.unlink()
        for flag in ("--browser", "--login"):
            with self.subTest(flag=flag):
                self.events.clear()
                self.assertEqual(pipeline.main([flag]), 0)
                self.assertTrue(self.crawl_args.browser)
                self.assertIsNone(self.crawl_args.cookie_file)
                self.assertEqual(self.events, ["configure", "check", "crawl", "stage"])

    def test_default_path_is_project_relative_and_works_from_another_directory(self):
        self.assertEqual(self.default_path, PROJECT_ROOT / "data_preprocessing" /
                         "itviec_crawl_data" / "cookie_itviec.txt")
        previous = Path.cwd()
        try:
            os.chdir(self.temp.name)
            self.assertEqual(pipeline.main([]), 0)
        finally:
            os.chdir(previous)
        self.assertTrue(self.crawl_args.cookie_file.is_absolute())
        self.assertEqual(self.crawl_args.cookie_file, self.cookie)

    def test_browser_can_use_an_explicit_cookie_without_the_default_file(self):
        alternate = Path(self.temp.name) / "alternate_cookie.txt"
        self.cookie.unlink()
        self.assertEqual(pipeline.main(["--browser", "--cookie-file", str(alternate)]), 0)
        self.assertTrue(self.crawl_args.browser)
        self.assertEqual(self.crawl_args.cookie_file, alternate)


if __name__ == "__main__":
    unittest.main()
