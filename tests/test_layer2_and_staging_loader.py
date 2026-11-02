"""Kiểm tra lớp chuẩn hóa chung và loader staging."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

from data_preprocessing.normalization.contracts import (
    NORMALIZED_FIELDS,
    NormalizationError,
)
from data_preprocessing.normalization.normalizer import normalize_raw_record
from data_preprocessing.normalization.role_gate import evaluate_role_gate
from data_preprocessing.cli.load_staging import stage_records


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def itviec_row(title="Senior Backend Engineer"):
    return {
        "job_id": "itviec:101",
        "crawl_run_id": "run_itviec",
        "source": "itviec",
        "source_job_id": "101",
        "job_url": "https://itviec.com/it-jobs/backend-101?ref=list",
        "observed_at": "2026-11-02T10:00:00+07:00",
        "raw_payload": {
            "title": title,
            "company_name": "ACME",
            "job_description": "<p>Build APIs</p>",
            "your_skills_and_experience": "At least 3 years. English required.",
            "skills": ["Python", "PostgreSQL"],
            "city_on_listing": "Ho Chi Minh",
            "working_model": "Hybrid",
            "date_posted": "2026-11-01",
            "salary": {"text": "20 - 30 triệu VND/tháng, gross", "visibility": "visible"},
        },
    }


def topdev_row(title="Data Engineer", *, with_description=True):
    payload = {
        "id": 202,
        "title": title,
        "company": {"display_name": "Beta"},
        "requirements": "At least 2 years. English required.",
        "skills_arr": ["Python", "AWS"],
        "addresses": {
            "full_addresses": ["Hà Nội"],
            "address_region_array": ["Ha Noi"],
        },
        "published_at": "2026-11-01T08:00:00+07:00",
        "salary": {
            "value": "2,000 - 3,000 USD",
            "min_filter": 2000,
            "max_filter": 3000,
            "currency": "USD",
            "unit": "MONTH",
        },
    }
    if with_description:
        payload["description"] = "Build data pipelines"
    return {
        "job_id": "topdev:202",
        "crawl_run_id": "run_topdev",
        "source": "topdev",
        "source_job_id": "202",
        "job_url": "https://topdev.vn/detail-jobs/data-202",
        "observed_at": "2026-11-02T10:00:00+07:00",
        "raw_payload": payload,
    }


def normalize(row):
    decision = evaluate_role_gate(row["raw_payload"], source=row["source"])
    return normalize_raw_record(row, decision)


class Layer2Tests(unittest.TestCase):
    def test_both_sources_produce_exact_same_thirty_columns(self):
        itviec = normalize(itviec_row())
        topdev = normalize(topdev_row())
        self.assertEqual(tuple(itviec), NORMALIZED_FIELDS)
        self.assertEqual(tuple(topdev), NORMALIZED_FIELDS)
        self.assertEqual(len(NORMALIZED_FIELDS), 30)

    def test_itviec_cleaning_and_text_salary(self):
        job = normalize(itviec_row())
        self.assertEqual(job["job_url"], "https://itviec.com/it-jobs/backend-101")
        self.assertEqual(job["job_roles"], ["Back-end Developer"])
        self.assertEqual(job["seniority_levels"], ["Senior"])
        self.assertEqual(job["cities"], ["Hồ Chí Minh"])
        self.assertEqual(job["work_mode"], "hybrid")
        self.assertEqual((job["salary_min"], job["salary_max"]),
                         (Decimal("20000000"), Decimal("30000000")))
        self.assertEqual(job["salary_currency"], "VND")
        self.assertEqual(job["salary_period"], "month")

    def test_topdev_maps_to_common_shapes(self):
        job = normalize(topdev_row())
        self.assertEqual(job["job_roles"], ["Data Engineer"])
        self.assertEqual(job["company_name"], "Beta")
        self.assertEqual(job["cities"], ["Hà Nội"])
        self.assertEqual(job["skills"], [
            {"name": "Python", "category": "programming_language"},
            {"name": "AWS", "category": "cloud"},
        ])
        self.assertEqual(job["salary_status"], "disclosed")

    def test_normalizer_refuses_job_that_did_not_pass_role_gate(self):
        row = itviec_row("Senior Accountant")
        decision = evaluate_role_gate(row["raw_payload"], source="itviec")
        self.assertFalse(decision.accepted)
        with self.assertRaises(NormalizationError):
            normalize_raw_record(row, decision)

    def test_staging_schema_is_single_table_without_explicit_indexes(self):
        sql = (PROJECT_ROOT / "data_preprocessing" / "sql" / "staging_schema.sql").read_text(encoding="utf-8").lower()
        self.assertEqual(sql.count("create table"), 1)
        self.assertIn("staging.normalized_jobs", sql)
        self.assertNotIn("create index", sql)


class FakeConnection:
    def __init__(self):
        self.writes = []

    def execute(self, query, values):
        self.writes.append((query, values))
        return SimpleNamespace(rowcount=1 if query.startswith("DELETE FROM") else 1)


class LoaderTests(unittest.TestCase):
    def test_loader_skips_role_and_normalization_failures_then_continues(self):
        outside_it = itviec_row("Senior Accountant")
        invalid = topdev_row(with_description=False)
        invalid["raw_payload"].pop("requirements")
        valid = topdev_row("Site Reliability Engineer")
        valid["job_id"] = "topdev:203"
        valid["source_job_id"] = "203"
        valid["raw_payload"]["id"] = 203
        connection = FakeConnection()

        summary = stage_records(connection, [outside_it, invalid, valid])

        self.assertEqual(summary.scanned, 3)
        self.assertEqual(summary.rejected_role, 1)
        self.assertEqual(summary.rejected_language, 0)
        self.assertEqual(summary.rejected_normalization, 1)
        self.assertEqual(summary.staged, 1)
        self.assertEqual(summary.failed_write, 0)
        upserts = [query for query, _ in connection.writes if query.startswith("INSERT INTO")]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(summary.removed_stale, 2)

    def test_loader_removes_existing_staging_row_for_unsupported_language(self):
        row = topdev_row("Data Engineer")
        row["raw_payload"]["description"] = "데이터 파이프라인을 구축하고 운영합니다."
        connection = FakeConnection()

        summary = stage_records(connection, [row])

        self.assertEqual(summary.rejected_language, 1)
        self.assertEqual(summary.removed_stale, 1)
        self.assertEqual(summary.staged, 0)
        self.assertTrue(connection.writes[0][0].startswith("DELETE FROM"))


if __name__ == "__main__":
    unittest.main()
