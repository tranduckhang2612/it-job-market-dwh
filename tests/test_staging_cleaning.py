"""Regression cases from the local data plus negative/adversarial cases."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_preprocessing import staging_cleaning as c
from data_preprocessing.topdev_normalize_data import normalize_topdev as td
from data_preprocessing.itviec_normalize_data import normalize_itviec as iv


def raw_topdev(**values):
    return {"id": 99, "title": "Backend Developer", "detail_url": "https://topdev.vn/detail-jobs/backend-99",
            "observed_at": "2026-10-16T12:00:00+07:00", "description": "Build reliable software.", **values}


class StagingCleaningTests(unittest.TestCase):
    def test_unicode_entities_and_invisible(self):
        self.assertEqual(c.clean_text("  Ha\u0300\u00a0No\u0323\u0302i\u200b &amp;amp; team  "), "Hà Nội & team")

    def test_nested_entity_idempotency(self):
        first = c.clean_text("A &amp;amp;nbsp; B")
        self.assertEqual(first, "A B")
        self.assertEqual(c.clean_text(first), first)

    def test_newlines_preserve_list_boundaries(self):
        self.assertEqual(c.clean_text("a  b\r\n\r\n\r\nc\t d", multiline=True), "a b\n\nc d")

    def test_null_placeholders(self):
        for value in [None, "", " N/A ", "null", "--", "\ufeff"]:
            self.assertIsNone(c.clean_text(value))

    def test_technology_punctuation(self):
        self.assertEqual(c.clean_text("C++ C# .NET List<String>"), "C++ C# .NET List<String>")

    def test_city_aliases_and_unknown_preserved(self):
        for alias, name in [("TP.HCM", "Hồ Chí Minh"), ("Tỉnh Bình Thuận", "Bình Thuận"),
                            ("Thành phố Hà Nội", "Hà Nội"), ("Tokyo", "Tokyo")]:
            self.assertEqual(c.canonical_city(alias), name)

    def test_alias_dedup_is_copy_and_idempotent(self):
        job = td.normalize_job(raw_topdev(), "run")
        job["skills"] = [{"name": n, "category": "technical", "requirement_type": r}
                         for n, r in [("ReactJS", "preferred"), ("React.js", "required"),
                                      ("Java", "required"), ("JavaScript", "required"), (".NET", "required")]]
        original = copy.deepcopy(job)
        result = c.clean_record(job)
        self.assertEqual(job, original)
        self.assertEqual([s["name"] for s in result["skills"]], ["React", "Java", "JavaScript", ".NET"])
        self.assertNotIn("requirement_type", result["skills"][0])
        self.assertEqual(c.clean_record(result), result)

    def test_all_addresses_retained(self):
        job = td.normalize_job(raw_topdev(addresses={"full_addresses": ["A", "B", "A"],
                                                    "address_region_array": ["Hà Nội", "TP.HCM"]}), "run")
        self.assertEqual(job["location_text"], "A\nB")
        self.assertEqual(job["cities"], ["Hà Nội", "Hồ Chí Minh"])

    def test_salary_narrative_overrides_empty_negotiable(self):
        result = td.normalize_salary({"salary": {"is_negotiable": "1", "currency": "VND", "unit": "MONTH"},
                                      "benefits_original": [{"value": "<p>Thu nhập: 12&ndash;25 triệu đồng/tháng.</p>"}]})
        self.assertEqual((result["salary_min"], result["salary_max"], result["salary_status"]), (12000000, 25000000, "disclosed"))

    def test_allowances_and_bonus_are_not_salary(self):
        result = td.normalize_salary({"benefits_original": [{"value": "<p>Lunch allowance: 2.000.000 VND/month</p><p>Lương tháng 13</p><p>Yearly learning budget: $300</p>"}]})
        self.assertIsNone(result["salary_min"])
        self.assertIsNone(result["salary_max"])

    def test_conflicting_offers_require_review(self):
        result = td.normalize_salary({"benefits_original": [{"value": "<p>Lương: 20 triệu VND/tháng</p><p>Lương: 30 triệu VND/tháng</p>"}]})
        self.assertEqual(result["salary_status"], "unknown")
        self.assertIsNone(result["salary_min"])

    def test_locale_numeric_and_year_period(self):
        result = td.normalize_salary({"salary": {"min": "1.200,50", "max": "2.400,75", "currency": "EUR", "unit": "YEAR"}})
        self.assertEqual((result["salary_min"], result["salary_max"], result["salary_period"]), (1200.5, 2400.75, "year"))

    def test_absent_currency_and_period_remain_null(self):
        result = td.normalize_salary({"salary": {"min_filter": 100, "max_filter": 200}})
        self.assertIsNone(result["salary_currency"])
        self.assertIsNone(result["salary_period"])

    def test_invalid_numbers_and_reversed_range(self):
        for obj in [{"min_filter": True}, {"max_filter": float("inf")}, {"min": "nan"}, {"min": "-10"},
                    {"min_filter": 300, "max_filter": 100}]:
            result = td.normalize_salary({"salary": obj})
            self.assertIsNone(result["salary_min"])
            self.assertIsNone(result["salary_max"])

    def test_net_technology_does_not_imply_net_salary(self):
        result = td.normalize_salary(raw_topdev(description="Build .NET applications.", salary={"min_filter": 20000000}))
        self.assertIsNone(result["salary_basis"])

    def test_experience_plus_and_months(self):
        for text, years in [("4+ years of experience in backend.", 4), ("Có ít nhất 6 tháng kinh nghiệm.", 0.5)]:
            self.assertEqual(td.normalize_experience({"requirements": text})["experience_min_years"], years)

    def test_preferred_experience_not_mandatory(self):
        result = td.normalize_experience({"requirements": "Minimum 2 years of experience.\nPreferred: 5 years of Python experience."})
        self.assertEqual(result["experience_min_years"], 2)

    def test_company_history_benefits_and_age_not_experience(self):
        result = td.normalize_experience({"requirements": "Độ tuổi: 28-40 tuổi. Good English.",
                                          "description": "Company has 20 years of history.",
                                          "benefits_original": "12 months bonus"})
        self.assertIsNone(result["experience_min_years"])

    def test_missing_experience_unit_not_guessed(self):
        result = td.normalize_experience({"requirements": "Có 1-2 kinh nghiệm tại vị trí tương đương."})
        self.assertIsNone(result["experience_min_years"])
        self.assertIn("1-2", result["experience_text"])

    def test_itviec_same_finishing_and_lineage(self):
        raw = {"source_job_id": "99", "url": "https://itviec.com/it-jobs/backend-99",
               "title": "  Backend\u200b   Developer ", "company_name": "N/A",
               "job_description": "Build C++ and .NET services.", "crawled_at": "2026-10-16T12:00:00+07:00"}
        original = copy.deepcopy(raw)
        job = iv.normalize_job(raw, "run")
        self.assertEqual(job["title"], "Backend Developer")
        self.assertIsNone(job["company_name"])
        self.assertEqual(job["job_id"], "itviec:99")
        self.assertEqual(job["observed_at"], raw["crawled_at"])
        self.assertEqual(len(job), 30)
        self.assertEqual(original, raw)

    def test_non_it_is_review_flag_not_deletion(self):
        job = td.normalize_job(raw_topdev(title="HR Intern"), "run")
        self.assertTrue(c.likely_non_it(job))
        self.assertEqual(job["job_id"], "topdev:99")
        self.assertFalse(c.likely_non_it({"title": "Software Developer - HR platform"}))

    def test_requirement_skills_and_korean(self):
        job = td.normalize_job(raw_topdev(requirements="Python required. Docker preferred. Korean communication required."), "run")
        skills = {s["name"]: s for s in job["skills"]}
        self.assertNotIn("requirement_type", skills["Python"])
        self.assertNotIn("requirement_type", skills["Docker"])
        self.assertIn("Korean", [f["language"] for f in job["foreign_languages"]])

    def test_negative_requirement_not_added(self):
        job = td.normalize_job(raw_topdev(requirements="Docker is not required. Python is required."), "run")
        self.assertNotIn("Docker", [s["name"] for s in job["skills"]])

    def test_postgres_fractional_timestamp_python310(self):
        for normalizer in (iv, td):
            self.assertEqual(normalizer.normalized_datetime("2026-10-16T06:28:50.72484+00:00"),
                             "2026-10-16T13:28:50.724840+07:00")


if __name__ == "__main__":
    unittest.main()
