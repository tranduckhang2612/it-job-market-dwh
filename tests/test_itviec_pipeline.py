"""Offline contract regressions: python -m unittest discover -s tests."""
from __future__ import annotations
import copy
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import main_itviec as pipeline
from data_preprocessing.itviec_normalize_data import normalize_itviec as normalizer


def raw_example(source_id="fixture_a", observed_at="2026-10-09T03:00:00+07:00"):
    return {
        "source_job_id": source_id,
        "url": "https://itviec.com/it-jobs/backend-engineer-" + source_id,
        "title": "Backend Engineer", "company_name": "Fixture Company",
        "crawled_at": observed_at, "date_posted": "2026-10-08",
        "job_description": "Build backend services.\nMaintain reliable APIs.",
        "your_skills_and_experience": "Python is mandatory. Docker preferred. English required.",
        "skills": ["Python", "Docker"],
        "salary": {"text": "20–30 triệu VND/tháng, gross", "visibility": "visible"},
        "locations_structured": {"address": {"addressRegion": "Ho Chi Minh"}},
        "working_model": "Hybrid",
    }


class CommonContractTests(unittest.TestCase):
    def setUp(self):
        self.record = normalizer.normalize_job(raw_example(), "fixture_run")

    def rejected(self, field, value):
        record = copy.deepcopy(self.record)
        record[field] = value
        with self.assertRaises((normalizer.CrawlError, ValueError, TypeError)):
            normalizer.validate_normalized_job(record)

    def test_exact_thirty_fields_and_nested_shapes(self):
        self.assertEqual(tuple(self.record), normalizer.NORMALIZED_FIELDS)
        self.assertEqual(len(self.record), 30)
        self.assertEqual(set(self.record["skills"][0]), {"name", "category", "requirement_type"})
        self.assertIn("\n", self.record["description"])
        self.assertEqual(self.record["observed_at"], "2026-10-09T03:00:00+07:00")

    def test_unknown_optional_values_are_valid(self):
        for field in ("source_job_id", "published_at", "company_name", "location_text", "work_mode",
                      "experience_text", "experience_min_years", "experience_max_years", "salary_text",
                      "salary_min", "salary_max", "salary_currency", "salary_period", "salary_basis"):
            self.record[field] = None
        self.record["job_id"] = "itviec:url_" + hashlib.sha256(self.record["job_url"].encode()).hexdigest()
        for field in ("job_roles", "seniority_levels", "cities", "skills", "required_knowledge",
                      "education_requirements", "foreign_languages", "soft_skills"):
            self.record[field] = []
        self.record["salary_status"] = "not_disclosed"
        normalizer.validate_normalized_job(self.record)

    def test_required_strings_cannot_be_whitespace(self):
        for field in ("job_id", "source", "job_url", "crawl_run_id", "observed_at", "title", "description"):
            with self.subTest(field=field):
                self.rejected(field, " \t\n ")

    def test_datetime_requires_valid_calendar_and_timezone(self):
        for field in ("observed_at", "published_at"):
            for value in ("not-a-date", "2026-02-30T10:00:00+07:00", "2026-10-09T10:00:00"):
                with self.subTest(field=field, value=value):
                    self.rejected(field, value)

    def test_identity_and_url_are_validated(self):
        self.rejected("job_id", "another-source:fixture_a")
        self.rejected("job_url", "https://example.com/it-jobs/test")
        self.rejected("source_job_id", "different_id")

    def test_string_lists_reject_empty_items_and_duplicates(self):
        for field in ("job_roles", "seniority_levels", "cities", "required_knowledge", "soft_skills"):
            for value in ([""], ["  "], ["same", "same"]):
                with self.subTest(field=field, value=value):
                    self.rejected(field, value)

    def test_nested_lists_reject_duplicates_and_empty_names(self):
        skill = {"name": "Python", "category": "programming_language", "requirement_type": "required"}
        language = {"language": "English", "level_text": None, "requirement_type": "required"}
        education = {"degree": "bachelor", "majors": ["Computer Science"], "requirement_type": "required"}
        cases = [("skills", [skill, copy.deepcopy(skill)]), ("skills", [{**skill, "name": " "}]),
                 ("skills", [{**skill, "category": " "}]),
                 ("foreign_languages", [language, copy.deepcopy(language)]),
                 ("foreign_languages", [{**language, "language": " "}]),
                 ("education_requirements", [education, copy.deepcopy(education)]),
                 ("education_requirements", [{**education, "majors": ["CS", "CS"]}]),
                 ("education_requirements", [{**education, "majors": [" "]}])]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.rejected(field, value)

    def test_salary_status_bounds_and_currency_agree(self):
        self.rejected("salary_status", "negotiable")
        record = copy.deepcopy(self.record)
        record.update(salary_status="disclosed", salary_min=None, salary_max=None)
        with self.assertRaises(normalizer.CrawlError):
            normalizer.validate_normalized_job(record)
        self.rejected("salary_currency", "vnd")
        self.rejected("salary_currency", "DOLLARS")

    def test_numbers_are_finite_nonnegative_and_ordered(self):
        for field in ("experience_min_years", "experience_max_years", "salary_min", "salary_max"):
            for value in (-1, True, float("nan"), float("inf")):
                with self.subTest(field=field, value=value):
                    self.rejected(field, value)
        record = copy.deepcopy(self.record)
        record.update(experience_min_years=5, experience_max_years=2)
        with self.assertRaises(normalizer.CrawlError):
            normalizer.validate_normalized_job(record)

    def test_required_preferred_are_scoped_to_each_skill(self):
        raw = raw_example()
        raw["your_skills_and_experience"] = "Python is mandatory, Docker preferred."
        types = {item["name"]: item["requirement_type"]
                 for item in normalizer.normalize_job(raw, "fixture_run")["skills"]}
        self.assertEqual(types["Python"], "required")
        self.assertEqual(types["Docker"], "preferred")

    def test_bare_language_requirement_is_not_lost(self):
        raw = raw_example()
        raw["your_skills_and_experience"] = "English required."
        languages = normalizer.normalize_job(raw, "fixture_run")["foreign_languages"]
        english = next(item for item in languages if item["language"] == "English")
        self.assertEqual(english["requirement_type"], "required")
        self.assertNotIn("IELTS", english["level_text"] or "")
        self.assertNotIn("CEFR", english["level_text"] or "")

    def test_optional_technology_experience_does_not_zero_general_requirement(self):
        raw = raw_example()
        raw["your_skills_and_experience"] = (
            "At least 3 years of experience. No experience with Kubernetes is required.")
        self.assertEqual(normalizer.normalize_job(raw, "fixture_run")["experience_min_years"], 3)

    def test_missing_experience_does_not_infer_fresher(self):
        raw = raw_example()
        raw["your_skills_and_experience"] = "Python development and REST APIs."
        record = normalizer.normalize_job(raw, "fixture_run")
        self.assertIsNone(record["experience_min_years"])
        self.assertNotIn("Fresher", record["seniority_levels"])

    def test_explicit_architect_manager_and_auditor_roles_are_mapped(self):
        cases = [('Chief Architect/CTO (Banking)', ['Solution Architect'], 'Architecture'),
                 ('Enterprise Architect (Infrastructure, Cloud)', [], 'Architecture'),
                 ('Technical Engineering Manager (Manufacturing)', [], 'Engineering Management'),
                 ('CV Xây Dựng Mô Hình', ['IT Auditor / IT Risk Manager'], 'IT Audit/Risk')]
        for title, expertise, expected in cases:
            with self.subTest(title=title):
                raw = raw_example()
                raw.update(title=title, job_expertise=expertise)
                self.assertIn(expected, normalizer.normalize_job(raw, 'fixture_run')['job_roles'])

    def test_cleaning_does_not_mutate_raw(self):
        raw = raw_example()
        before = copy.deepcopy(raw)
        normalizer.normalize_job(raw, "fixture_run")
        self.assertEqual(raw, before)

    def test_legacy_html_is_cleaned_without_joining_skills_or_splitting_words(self):
        raw = raw_example()
        raw['job_description'] = ('<h2>Tasks</h2><p>Java<strong>Script</strong> services</p>'
                                  '<ul><li><a class="itag">Spring Boot</a><a class="itag">REST API</a></li></ul>'
                                  '<pre><code>Python</code></pre><blockquote>Teamwork</blockquote>'
                                  '<script>secret-script-content</script>')
        before = copy.deepcopy(raw)
        description = normalizer.normalize_job(raw, 'fixture_run')['description']
        self.assertIn('JavaScript services', description)
        self.assertIn('Spring Boot REST API', description)
        self.assertIn('\nPython\n', description)
        self.assertNotIn('<code>', description)
        self.assertNotIn('secret-script-content', description)
        self.assertEqual(raw, before)

    def test_generic_type_example_is_retained_as_plain_text(self):
        raw = raw_example()
        raw['job_description'] = 'Work with List<String> and generic APIs.'
        self.assertIn('List<String>', normalizer.normalize_job(raw, 'fixture_run')['description'])

    def test_reprocess_entrypoint_keeps_run_id_and_never_crawls(self):
        with mock.patch.object(pipeline, "_configure_compose_database", return_value="explicit"), \
             mock.patch.object(pipeline, "check_database", return_value=0), \
             mock.patch.object(pipeline.crawl_itviec, "run") as crawl, \
             mock.patch.object(pipeline.crawl_itviec, "new_crawl_run_id") as new_id, \
             mock.patch.object(pipeline.normalize_itviec, "stage_database_run", return_value=0) as stage:
            self.assertEqual(pipeline.main(["--process-run-id", "fixture_existing_run", "--no-compose-db"]), 0)
        crawl.assert_not_called()
        new_id.assert_not_called()
        self.assertEqual(stage.call_args.args[0], "fixture_existing_run")


if __name__ == "__main__":
    unittest.main()
