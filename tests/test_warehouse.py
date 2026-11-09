import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from data_preprocessing.warehouse import job_values as warehouse
from test_analytics_atomic import sample


class WarehouseTests(unittest.TestCase):
    def test_core_skill_category_refines_known_names_and_keeps_unknown_source_category(self):
        for name, original, expected in (
            (" LLM ", "other", "data_ai"),
            ("Large Language Models", "other", "data_ai"),
            ("natural  language processing", "other", "data_ai"),
            ("NLP", "other", "data_ai"),
            ("Computer Vision", "other", "data_ai"),
            ("Kafka", "other", "data_streaming"),
            ("GraphQL", "other", "integration"),
            ("Microsoft SQL Server", "other", "database"),
            ("AUTOSAR", "other", "embedded"),
            ("SQL", "database", "database"),
            ("Unlisted LLM tool", "other", "other"),
            ("Unlisted database", "database", "database"),
        ):
            with self.subTest(name=name):
                self.assertEqual(warehouse.core_skill_category(name, original), expected)

    def test_framework_language_known_aliases_and_unassigned_patterns(self):
        for name, expected in {
            "Spring Boot": "Java", "ReactJS": "JavaScript", "Angular": "TypeScript",
            "NestJS": "TypeScript", "FastAPI": "Python", " dotnet ": "C#",
            "Flutter": "Dart", "Laravel": "PHP", "MVVM": None, "Unknown": None,
        }.items():
            with self.subTest(name=name):
                self.assertEqual(warehouse.framework_language(name), expected)

    def test_role_categories_match_project_taxonomy(self):
        for name, expected in {
            "Software Developer / Engineer": "Software Engineer",
            "Business Analyst": "Software Engineer",
            "Unlisted role": "Software Engineer",
            "": "Software Engineer",
            "  natural language processing (nlp) engineer ": "Data/AI",
            "Database Administrator (DBA)": "Data/AI",
            "Security Test Engineer": "Security",
            "DevSecOps Engineer": "Security",
            "Firmware Developer": "Embbed",
            "Embedded Systems Developer": "Embbed",
            "Back-end Developer": "Web/App",
            "iOS Developer": "Web/App",
        }.items():
            with self.subTest(name=name):
                self.assertEqual(warehouse.role_category(name), expected)

    def test_read_normalized_source_retains_duplicates_across_jobs_and_uses_utc(self):
        pg = MagicMock()
        at = datetime(2020, 12, 30, 3, tzinfo=timezone(timedelta(hours=7)))
        skills = [{"name": "Python", "category": "programming_language"},
                  {"name": "ReactJS", "category": "other"}]
        rows = [sample(observed_at=at, skills=skills),
                sample(sid="2", source="itviec", skills=skills)]
        occurrences = [("topdev:1", "Python", "programming_language", None),
                       ("itviec:2", "Python", "programming_language", None)]
        pg.execute.side_effect = [[], rows]
        with patch.object(warehouse.psycopg, "connect") as connect:
            connect.return_value.__enter__.return_value = pg
            actual_jobs, groups = warehouse.read_source()
        self.assertEqual(actual_jobs["topdev:1"][-1], datetime(2020, 12, 29, 20))
        self.assertEqual(set(groups), set(warehouse.GROUPS))
        self.assertEqual(groups["programming_language"], occurrences)
        self.assertEqual([row[1] for row in groups["framework"]], ["React", "React"])
        self.assertEqual(pg.execute.call_count, 2)
        setup, query = (call.args[0] for call in pg.execute.call_args_list)
        self.assertIn("REPEATABLE READ, READ ONLY", setup)
        self.assertIn("FROM normalize_data.normalized_jobs", query)
        self.assertIn("SELECT DISTINCT ON (job_id)", query)
        self.assertIn("observed_at DESC, crawl_run_id DESC", query)
        self.assertNotIn("analytics.", query)

    def test_preview_counts_occurrences_without_sql_server(self):
        rows = [("topdev:1", "Python", "programming_language", None)] * 2
        output = io.StringIO()
        with patch.object(warehouse, "read_source", return_value=({"topdev:1": ()}, {"programming_language": rows})):
            with redirect_stdout(output):
                self.assertEqual(warehouse.main([]), 0)
        self.assertEqual(json.loads(output.getvalue()), {"jobs": 1, "links": {"programming_language": 2}})

    def test_empty_or_invalid_snapshot_does_not_touch_sql_server(self):
        conn = MagicMock()
        with self.assertRaisesRegex(ValueError, "Empty source snapshot"):
            warehouse.apply(conn, ({}, {}))
        with self.assertRaisesRegex(ValueError, "valid job IDs"):
            warehouse.apply(conn, ({"topdev:1": ()}, {}))
        self.assertEqual(conn.mock_calls, [])
