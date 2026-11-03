"""The unified command preserves each loader's argument and return code."""

from contextlib import redirect_stdout
from io import StringIO
from unittest import TestCase
from unittest.mock import patch

from data_preprocessing.cli import load as legacy_load
from data_preprocessing.cli import load_analytics as legacy_analytics
from data_preprocessing.cli import load_normalize_data as legacy_normalize
from it_job_market_dwh.commands import load
from it_job_market_dwh.commands import load_normalize_data as old_command_normalize
from it_job_market_dwh.loaders import analytics, normalize, postgres


class LoaderCommandTests(TestCase):
    def test_old_import_and_unified_command_use_the_new_modules(self):
        self.assertIs(legacy_load, load)
        self.assertIs(legacy_normalize, normalize)
        self.assertIs(legacy_analytics, analytics)
        self.assertIs(old_command_normalize, normalize)

    def test_dispatches_arguments_and_exit_code(self):
        for stage, module_name in load.STAGES.items():
            with self.subTest(stage=stage):
                with patch(module_name + ".main", return_value=4) as main:
                    self.assertEqual(load.main([stage, "--check-option"]), 4)
                    main.assert_called_once_with(["--check-option"])

    def test_no_stage_prints_usage(self):
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(load.main([]), 0)
        self.assertIn("normalize", output.getvalue())
        self.assertIn("warehouse", output.getvalue())

    def test_shared_raw_query_keeps_scope_and_limit(self):
        query, params = postgres.raw_selection_query(
            crawl_run_id="run-1", source="itviec", limit=10,
        )
        self.assertIn("r.status <> 'running'", query)
        self.assertIn("p.crawl_run_id = %s", query)
        self.assertIn("r.source = %s", query)
        self.assertTrue(query.endswith("LIMIT %s"))
        self.assertEqual(params, ["run-1", "itviec", 10])
