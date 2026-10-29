"""Tests cho việc chuẩn hoá lương, kinh nghiệm, địa điểm, chế độ làm việc của TopDev."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from data_preprocessing.topdev_normalize_data import normalize_topdev as normalizer


class TopDevSalaryExperienceTests(unittest.TestCase):
    def test_disclosed_salary_range(self):
        raw = {
            "id": 2138922,
            "title": "Backend Developer",
            "detail_url": "https://topdev.vn/detail-jobs/backend-dev-2138922",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Lập trình backend Python.",
            "salary": {
                "is_negotiable": "0",
                "unit": "MONTH",
                "currency": "VND",
                "min_filter": 12000000,
                "max_filter": 18000000,
                "value": "12,000,000 - 18,000,000 VND"
            }
        }
        job = normalizer.normalize_job(raw, "run_test_1")
        self.assertEqual(job["salary_status"], "disclosed")
        self.assertEqual(job["salary_min"], 12000000)
        self.assertEqual(job["salary_max"], 18000000)
        self.assertEqual(job["salary_currency"], "VND")
        self.assertEqual(job["salary_period"], "month")
        self.assertEqual(job["job_id"], "topdev:2138922")
        self.assertEqual(job["source"], "topdev")

    def test_negotiable_salary(self):
        raw = {
            "id": 2138779,
            "title": "Chuyên viên Kiểm thử",
            "detail_url": "https://topdev.vn/detail-jobs/qc-2138779",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Kiểm thử phần mềm.",
            "salary": {
                "is_negotiable": "1",
                "min_filter": 0,
                "max_filter": 0,
                "value": "Negotiable"
            }
        }
        job = normalizer.normalize_job(raw, "run_test_2")
        self.assertEqual(job["salary_status"], "negotiable")
        self.assertIsNone(job["salary_min"])
        self.assertIsNone(job["salary_max"])

    def test_salary_fallback_from_benefits_and_description(self):
        # Trường hợp API để negotiable nhưng benefits_original có mức lương cụ thể
        raw = {
            "id": 2138282,
            "title": "ĐẠI DIỆN KINH DOANH - FIELD SALES NGÀNH DẦU NHỚT",
            "detail_url": "https://topdev.vn/detail-jobs/sales-2138282",
            "observed_at": "2026-10-16T13:47:27.455632+07:00",
            "description": "Bán hàng theo định hướng công ty.",
            "salary": {
                "max": "*",
                "min": "*",
                "unit": "MONTH",
                "value": None,
                "currency": "VND",
                "max_filter": 0,
                "min_filter": 0,
                "is_negotiable": "1",
            },
            "benefits_original": [
                {
                    "value": "<p>Quyền lợi: Thu nhập: 12&ndash;25 triệu đồng/th&aacute;ng.</p>"
                }
            ]
        }
        job = normalizer.normalize_job(raw, "run_test_fallback")
        self.assertEqual(job["salary_status"], "disclosed")
        self.assertEqual(job["salary_min"], 12_000_000)
        self.assertEqual(job["salary_max"], 25_000_000)
        self.assertEqual(job["salary_currency"], "VND")
        self.assertEqual(job["salary_period"], "month")

        # Trường hợp lương lên đến X triệu trong description
        raw_desc = {
            "id": 2138283,
            "title": "Frontend Lead",
            "detail_url": "https://topdev.vn/detail-jobs/lead-2138283",
            "observed_at": "2026-10-16T13:47:27.455632+07:00",
            "description": "Lập trình ReactJS. Mức lương: Lên đến 35 triệu/tháng.",
            "salary": {
                "max": "*",
                "min": "*",
                "max_filter": 0,
                "min_filter": 0,
                "is_negotiable": "0",
            }
        }
        job_desc = normalizer.normalize_job(raw_desc, "run_test_desc")
        self.assertEqual(job_desc["salary_status"], "disclosed")
        self.assertIsNone(job_desc["salary_min"])
        self.assertEqual(job_desc["salary_max"], 35_000_000)

    def test_experience_extraction_from_text_and_months(self):
        raw = {
            "id": 2139001,
            "title": "Senior Frontend Engineer",
            "detail_url": "https://topdev.vn/detail-jobs/frontend-2139001",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Yêu cầu tối thiểu 3 năm kinh nghiệm ReactJS.",
            "job_types_str": "Từ xa",
            "job_levels_arr": ["Senior"],
            "addresses": {
                "address_region_array": ["Thành phố Hồ Chí Minh"],
                "full_addresses": ["Quận 1, Thành phố Hồ Chí Minh"]
            }
        }
        job = normalizer.normalize_job(raw, "run_test_3")
        self.assertEqual(job["work_mode"], "remote")
        self.assertIn("Senior", job["seniority_levels"])
        self.assertIn("Hồ Chí Minh", job["cities"])
        self.assertEqual(job["experience_min_years"], 3.0)

    def test_exact_thirty_fields_present_and_valid(self):
        raw = {
            "id": 2139002,
            "title": "DevOps Engineer",
            "detail_url": "https://topdev.vn/detail-jobs/devops-2139002",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Vận hành hệ thống CI/CD AWS Docker Kubernetes.",
            "skills_arr": ["AWS", "Docker", "Kubernetes"],
            "job_types_str": "Linh hoạt"
        }
        job = normalizer.normalize_job(raw, "run_test_4")
        self.assertEqual(tuple(job.keys()), normalizer.NORMALIZED_FIELDS)
        self.assertEqual(len(job), 30)
        self.assertEqual(job["work_mode"], "hybrid")
        self.assertIn("DevOps Engineer", job["job_roles"])

    def test_unmasked_salary_logged_in(self):
        raw = {
            "id": 2138340,
            "title": "Project Manager",
            "detail_url": "https://topdev.vn/detail-jobs/pm-2138340",
            "observed_at": "2026-10-16T12:00:00+07:00",
            "description": "Quản lý dự án phần mềm.",
            "salary": {
                "min": "20,000,000",
                "max": "40,000,000",
                "min_filter": 20000000,
                "max_filter": 40000000,
                "value": "20,000,000 - 40,000,000 VND",
                "is_negotiable": "0",
                "currency": "VND"
            },
            "is_salary_visible": True
        }
        job = normalizer.normalize_job(raw, "run_test_unmasked")
        self.assertEqual(job["salary_status"], "disclosed")
        self.assertEqual(job["salary_min"], 20000000.0)
        self.assertEqual(job["salary_max"], 40000000.0)
        self.assertEqual(job["salary_text"], "20,000,000 - 40,000,000 VND")

    def test_http_client_cookie_and_token_headers(self):
        from data_preprocessing.topdev_crawl_data.crawl_topdev import TopDevHttpClient
        client_cookie = TopDevHttpClient(cookie="Cookie: topdev_session=abc123xyz")
        self.assertEqual(client_cookie.headers.get("Cookie"), "topdev_session=abc123xyz")

        client_token = TopDevHttpClient(token="Bearer jwt.token.here")
        self.assertEqual(client_token.headers.get("Authorization"), "Bearer jwt.token.here")


if __name__ == "__main__":
    unittest.main()
