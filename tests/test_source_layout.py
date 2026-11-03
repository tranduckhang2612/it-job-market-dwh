"""The old imports and repository resource paths survive the source move."""

import unittest

from data_preprocessing.analytics import storage as old_analytics_storage
from data_preprocessing.cli import crawl_itviec as old_crawl_command
from data_preprocessing.crawlers import itviec as old_itviec
from data_preprocessing.normalization import role_gate as old_role_gate
from data_preprocessing.storage import raw as old_raw_storage
from data_preprocessing.warehouse import job_values as old_job_values
from it_job_market_dwh.analytics import storage as analytics_storage
from it_job_market_dwh.commands import crawl_itviec as crawl_command
from it_job_market_dwh.ingestion import itviec, raw_storage
from it_job_market_dwh.normalization import role_gate
from it_job_market_dwh.paths import PROJECT_ROOT, SQL_ROOT
from it_job_market_dwh.warehouse import job_values


class SourceLayoutTests(unittest.TestCase):
    def test_legacy_imports_resolve_to_the_same_modules(self):
        self.assertIs(old_itviec, itviec)
        self.assertIs(old_role_gate, role_gate)
        self.assertIs(old_raw_storage, raw_storage)
        self.assertIs(old_analytics_storage, analytics_storage)
        self.assertIs(old_crawl_command, crawl_command)
        self.assertIs(old_job_values, job_values)

    def test_resources_keep_their_existing_locations(self):
        self.assertEqual(role_gate.DEFAULT_ROLE_CATALOG, PROJECT_ROOT / "ds.md")
        self.assertEqual(raw_storage.RAW_SCHEMA_FILE, SQL_ROOT / "raw_schema.sql")
        self.assertEqual(analytics_storage.DDL, SQL_ROOT / "analytics_schema.sql")
        self.assertEqual(job_values.DW_DDL, SQL_ROOT / "dw.sql")
        self.assertEqual(
            crawl_command.DEFAULT_COOKIE_FILE,
            PROJECT_ROOT / "data_preprocessing/itviec_crawl_data/cookie_itviec.txt",
        )
        self.assertEqual(
            itviec.DEFAULT_AUTH_FILE,
            PROJECT_ROOT / "data_preprocessing/itviec_crawl_data/.itviec_auth.json",
        )


if __name__ == "__main__":
    unittest.main()
