"""Tests cho việc trích xuất kỹ năng, ngôn ngữ, học vấn của TopDev."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from data_preprocessing.topdev_normalize_data import normalize_topdev as normalizer


class TopDevRequirementsTests(unittest.TestCase):
    def test_skills_and_extra_skills_categorization(self):
        raw = {
            "id": 2139003,
            "title": "Python Backend Engineer",
            "detail_url": "https://topdev.vn/detail-jobs/python-2139003",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Thành thạo lập trình hướng đối tượng OOP, RESTful API và Microservices.",
            "skills_arr": ["Python", "PostgreSQL", "FastAPI"],
            "extra_skills": ["Docker", "Git"],
        }
        job = normalizer.normalize_job(raw, "run_test_skills")
        skills_by_name = {s["name"]: s for s in job["skills"]}

        self.assertIn("Python", skills_by_name)
        self.assertEqual(skills_by_name["Python"]["category"], "programming_languages")
        self.assertEqual(skills_by_name["Python"]["requirement_type"], "required")

        self.assertIn("PostgreSQL", skills_by_name)
        self.assertEqual(skills_by_name["PostgreSQL"]["category"], "databases")

        self.assertIn("FastAPI", skills_by_name)
        self.assertEqual(skills_by_name["FastAPI"]["category"], "frameworks_libraries")

        self.assertIn("Docker", skills_by_name)
        self.assertEqual(skills_by_name["Docker"]["requirement_type"], "preferred")

        self.assertIn("OOP", job["required_knowledge"])
        self.assertIn("REST API", job["required_knowledge"])
        self.assertIn("Microservices", job["required_knowledge"])

    def test_language_and_education_extraction(self):
        raw = {
            "id": 2139004,
            "title": "Software Engineer (English)",
            "detail_url": "https://topdev.vn/detail-jobs/swe-2139004",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Tốt nghiệp Đại học chuyên ngành Công nghệ thông tin. Tiếng Anh giao tiếp tốt TOEIC 650. Kỹ năng làm việc nhóm và giao tiếp.",
        }
        job = normalizer.normalize_job(raw, "run_test_edu")

        self.assertTrue(len(job["foreign_languages"]) > 0)
        self.assertEqual(job["foreign_languages"][0]["language"], "English")
        self.assertEqual(job["foreign_languages"][0]["level_text"], "TOEIC 650")

        self.assertIn("Teamwork", job["soft_skills"])
        self.assertIn("Communication", job["soft_skills"])

        self.assertTrue(len(job["education_requirements"]) > 0)
        self.assertEqual(job["education_requirements"][0]["degree"], "bachelor")
        self.assertIn("Information Technology", job["education_requirements"][0]["majors"])


if __name__ == "__main__":
    unittest.main()
