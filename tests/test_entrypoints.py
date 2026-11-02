"""Kiểm tra hai entrypoint raw mà không gọi HTTP hoặc PostgreSQL thật."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from data_preprocessing.cli import crawl_itviec as main_itviec
from data_preprocessing.cli import crawl_topdev as main_topdev


class ITviecEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cookie = Path(self.temp.name) / "cookie.txt"
        self.cookie.touch()
        self.events = []

    def run_main(self, arguments):
        def crawl(args):
            self.events.append(("crawl", args.crawl_run_id, args.limit))
            return 0

        with (
            mock.patch.object(main_itviec, "DEFAULT_COOKIE_FILE", self.cookie),
            mock.patch.object(main_itviec, "_configure_compose_database", return_value="external"),
            mock.patch.object(main_itviec, "check_database", side_effect=lambda args: self.events.append(("check",)) or 0),
            mock.patch.object(main_itviec.crawl_itviec, "new_crawl_run_id", return_value="itviec_test_run"),
            mock.patch.object(main_itviec.crawl_itviec, "run", side_effect=crawl),
        ):
            return main_itviec.main(arguments)

    def test_default_run_checks_raw_then_crawls(self):
        self.assertEqual(self.run_main([]), 0)
        self.assertEqual(self.events, [("check",), ("crawl", "itviec_test_run", 20)])

    def test_check_db_does_not_need_cookie_or_crawl(self):
        self.cookie.unlink()
        self.assertEqual(self.run_main(["--check-db"]), 0)
        self.assertEqual(self.events, [("check",)])

    def test_missing_cookie_stops_before_database(self):
        self.cookie.unlink()
        with self.assertLogs("itviec.pipeline", level="ERROR"):
            self.assertEqual(self.run_main([]), 1)
        self.assertEqual(self.events, [])


class TopDevEntrypointTests(unittest.TestCase):
    def test_default_run_checks_raw_then_crawls(self):
        events = []

        def crawl(args):
            events.append(("crawl", args.crawl_run_id, args.limit))
            return 0

        with (
            mock.patch.object(main_topdev, "_configure_compose_database", return_value="external"),
            mock.patch.object(main_topdev, "check_database", side_effect=lambda args: events.append(("check",)) or 0),
            mock.patch.object(main_topdev.crawl_topdev, "new_crawl_run_id", return_value="topdev_test_run"),
            mock.patch.object(main_topdev.crawl_topdev, "run", side_effect=crawl),
        ):
            self.assertEqual(main_topdev.main([]), 0)
        self.assertEqual(events, [("check",), ("crawl", "topdev_test_run", 20)])

    def test_check_db_does_not_crawl(self):
        with (
            mock.patch.object(main_topdev, "_configure_compose_database", return_value="external"),
            mock.patch.object(main_topdev, "check_database", return_value=0),
            mock.patch.object(main_topdev.crawl_topdev, "run") as crawl,
        ):
            self.assertEqual(main_topdev.main(["--check-db"]), 0)
        crawl.assert_not_called()


if __name__ == "__main__":
    unittest.main()
