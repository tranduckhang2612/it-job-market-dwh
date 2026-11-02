"""Kiểm tra các rule chuẩn hóa có bằng chứng của phiên bản 3.0."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

from data_preprocessing.normalization.contracts import (
    NORMALIZED_WITH_META_FIELDS,
    NORMALIZE_DATA_VERSION,
    NORMALIZATION_VERSION,
    PERSISTED_NORMALIZED_FIELDS,
)
from data_preprocessing.normalization.normalizer import (
    normalize_raw_record_for_storage,
    normalize_raw_record_with_meta,
)
from data_preprocessing.normalization.role_gate import evaluate_role_gate
from data_preprocessing.cli.load_normalize_data import normalize_records


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def topdev_row(title="Data Engineer", **payload_overrides):
    payload = {
        "id": 301,
        "title": title,
        "company": {"display_name": "Example"},
        "description": "Build and operate reliable data products.",
        "requirements": "At least 4 years of professional experience in software development.",
        "skills_arr": ["Python"],
        "job_types_str": "Fulltime",
        "addresses": {
            "full_addresses": ["District 1, Ho Chi Minh City"],
            "address_region_array": ["Ho Chi Minh City"],
        },
    }
    payload.update(payload_overrides)
    return {
        "job_id": "topdev:301",
        "crawl_run_id": "run_topdev_v3",
        "source": "topdev",
        "source_job_id": "301",
        "job_url": "https://topdev.vn/detail-jobs/data-301",
        "observed_at": "2026-11-02T10:00:00+07:00",
        "raw_payload": payload,
    }


def itviec_row(title="Cyber Security Engineer", **payload_overrides):
    payload = {
        "title": title,
        "company_name": "Example",
        "job_description": "Protect applications and cloud services.",
        "your_skills_and_experience": "At least 5 years of professional experience. English required.",
        "skills": ["Security"],
        "job_expertise": ["Fullstack Developer"],
        "city_on_listing": "Ha Noi",
    }
    payload.update(payload_overrides)
    return {
        "job_id": "itviec:401",
        "crawl_run_id": "run_itviec_v3",
        "source": "itviec",
        "source_job_id": "401",
        "job_url": "https://itviec.com/it-jobs/security-401",
        "observed_at": "2026-11-02T10:00:00+07:00",
        "raw_payload": payload,
    }


def normalize(row):
    decision = evaluate_role_gate(row["raw_payload"], source=row["source"])
    if not decision.accepted:
        raise AssertionError(decision)
    return normalize_raw_record_with_meta(row, decision)


class EvidenceNormalizationTests(unittest.TestCase):
    def test_storage_contract_excludes_all_removed_columns(self):
        row = topdev_row()
        job = normalize(row)
        decision = evaluate_role_gate(row["raw_payload"], source=row["source"])
        stored = normalize_raw_record_for_storage(row, decision)
        self.assertEqual(tuple(job), NORMALIZED_WITH_META_FIELDS)
        self.assertEqual(NORMALIZATION_VERSION, "3.0")
        self.assertEqual(NORMALIZE_DATA_VERSION, "5.1")
        self.assertEqual(tuple(stored), PERSISTED_NORMALIZED_FIELDS)
        self.assertEqual(len(PERSISTED_NORMALIZED_FIELDS), 18)
        for removed in (
            "experience_text", "experience_min_years", "experience_max_years",
            "education_requirements", "published_at", "required_knowledge",
            "salary_text", "salary_min", "salary_max", "salary_currency",
            "salary_period", "salary_basis", "salary_status",
        ):
            self.assertNotIn(removed, stored)
        self.assertNotIn("experience_text", stored["normalization_meta"]["fields"])
        self.assertEqual(set(job["normalization_meta"]["fields"]), {
            "title", "company_name", "description", "job_roles", "seniority_levels",
            "cities", "work_mode", "skills", "experience_text", "foreign_languages",
        })

    def test_experience_keeps_general_evidence_and_ignores_technology_years(self):
        row = topdev_row(requirements=(
            "At least 4 years of professional experience in software development. "
            "6 years Java. English TOEIC 650 required."
        ))
        job = normalize(row)

        self.assertIn("At least 4 years", job["experience_text"])
        self.assertNotIn("6 years Java", job["experience_text"])
        self.assertEqual(job["experience_min_years"], Decimal("4"))
        self.assertIsNone(job["experience_max_years"])
        self.assertEqual(job["seniority_levels"], ["Mid-level"])

    def test_technology_only_experience_does_not_infer_seniority(self):
        job = normalize(topdev_row(requirements="3 years Java. English required."))
        self.assertIsNone(job["experience_text"])
        self.assertIsNone(job["experience_min_years"])
        self.assertEqual(job["seniority_levels"], [])

    def test_technology_before_years_is_still_not_general_experience(self):
        job = normalize(topdev_row(
            requirements="Java development experience of at least 3 years.",
        ))
        self.assertIsNone(job["experience_text"])
        self.assertIsNone(job["experience_min_years"])
        self.assertEqual(job["seniority_levels"], [])

    def test_vietnamese_general_years_and_months_are_recognized(self):
        cases = (
            (
                "Có 4 năm kinh nghiệm làm việc trong lĩnh vực phát triển phần mềm.",
                Decimal("4"), "Mid-level",
            ),
            (
                "Có ít nhất 6 tháng kinh nghiệm làm việc.",
                Decimal("0.5"), "Fresher",
            ),
        )
        for requirement, expected_years, expected_level in cases:
            with self.subTest(requirement=requirement):
                job = normalize(topdev_row(requirements=requirement))
                self.assertEqual(job["experience_min_years"], expected_years)
                self.assertEqual(job["seniority_levels"], [expected_level])

    def test_multiple_general_experience_constraints_use_the_strongest_minimum(self):
        job = normalize(topdev_row(requirements=(
            "At least 2 years of professional experience. "
            "At least 5 years of work experience in a similar role."
        )))
        self.assertEqual(job["experience_min_years"], Decimal("5"))
        self.assertEqual(job["seniority_levels"], ["Senior"])

    def test_range_crossing_one_boundary_keeps_both_adjacent_levels(self):
        job = normalize(topdev_row(requirements="2-4 years of work experience."))
        self.assertEqual(job["experience_min_years"], Decimal("2"))
        self.assertEqual(job["experience_max_years"], Decimal("4"))
        self.assertEqual(job["seniority_levels"], ["Junior", "Mid-level"])
        meta = job["normalization_meta"]["fields"]["seniority_levels"]
        self.assertEqual(meta["source"], "general_experience")
        self.assertEqual(
            meta["candidates"]["general_experience"], ["Junior", "Mid-level"],
        )

    def test_experience_ranges_map_to_at_most_two_overlapping_levels(self):
        cases = (
            ("0.5-2 years of work experience.", ["Fresher", "Junior"]),
            ("1-2 years of work experience.", ["Junior"]),
            ("3-4 years of work experience.", ["Mid-level"]),
            ("4-6 years of work experience.", ["Mid-level", "Senior"]),
            ("5-7 years of work experience.", ["Senior"]),
            ("1-5 years of work experience.", []),
            ("Under 3 years of work experience.", ["Fresher", "Junior"]),
        )
        for requirement, expected in cases:
            with self.subTest(requirement=requirement):
                self.assertEqual(
                    normalize(topdev_row(requirements=requirement))["seniority_levels"],
                    expected,
                )

    def test_title_still_wins_over_multi_level_experience_range(self):
        job = normalize(topdev_row(
            title="Senior Data Engineer",
            requirements="2-4 years of work experience.",
        ))
        meta = job["normalization_meta"]["fields"]["seniority_levels"]
        self.assertEqual(job["seniority_levels"], ["Senior"])
        self.assertIn("general_experience: Junior, Mid-level", meta["conflicts"])

    def test_structured_months_are_preferred_and_stored_as_readable_years(self):
        job = normalize(itviec_row(
            title="Data Engineer",
            job_expertise=["Data Engineer"],
            experience_requirements_structured={"monthsOfExperience": 10},
        ))
        self.assertEqual(job["experience_text"], "10 months of experience")
        self.assertEqual(job["experience_min_years"], Decimal("0.83"))
        self.assertEqual(job["seniority_levels"], ["Fresher"])

    def test_seniority_precedence_and_conflict_metadata(self):
        job = normalize(topdev_row(
            title="Senior Data Engineer",
            job_levels_arr=["Junior"],
            requirements="At least 6 years of professional work experience.",
        ))
        meta = job["normalization_meta"]["fields"]["seniority_levels"]
        self.assertEqual(job["seniority_levels"], ["Junior"])
        self.assertEqual(meta["source"], "source_explicit")
        self.assertIn("title: Senior", meta["conflicts"])
        self.assertIn("general_experience: Senior", meta["conflicts"])

    def test_vietnamese_explicit_senior_alias_has_priority(self):
        job = normalize(topdev_row(
            job_levels_arr=["Chuyên viên cao cấp"],
            requirements="At least 4 years of professional work experience.",
        ))
        self.assertEqual(job["seniority_levels"], ["Senior"])
        self.assertEqual(
            job["normalization_meta"]["fields"]["seniority_levels"]["source"],
            "source_explicit",
        )

    def test_all_levels_is_a_sentinel_not_an_expansion(self):
        job = normalize(topdev_row(
            title="C/C++ Software Developer (All Levels)",
            job_levels_str="Tất cả cấp bậc",
            requirements="Strong programming fundamentals.",
        ))
        self.assertEqual(job["seniority_levels"], ["All levels"])

    def test_title_role_wins_and_conflicting_source_role_is_recorded(self):
        job = normalize(itviec_row())
        meta = job["normalization_meta"]["fields"]["job_roles"]
        self.assertEqual(job["job_roles"], ["Security Engineer"])
        self.assertIn("source_role_fields: Full-stack Developer", meta["conflicts"])

    def test_compound_ai_full_stack_title_keeps_both_roles(self):
        job = normalize(topdev_row(title="[Vũng Tàu] AI Full Stack Engineer"))
        self.assertEqual(job["job_roles"], ["Full-stack Developer", "AI Engineer"])
        self.assertEqual(job["cities"], ["Hồ Chí Minh"])
        self.assertIn(
            "title: Vũng Tàu",
            job["normalization_meta"]["fields"]["cities"]["conflicts"],
        )

    def test_specific_architect_role_does_not_gain_technical_architect(self):
        job = normalize(topdev_row(title="Data Architect"))
        self.assertEqual(job["job_roles"], ["Data Architect"])

    def test_skill_alias_categories_and_language_transfer(self):
        job = normalize(topdev_row(
            skills_arr=["ReactJS", "NodeJS", "Postgres", "C/C++", "English", "MysteryTech"],
            requirements="English TOEIC 650 required.",
        ))
        self.assertEqual(job["skills"], [
            {"name": "React", "category": "framework_library"},
            {"name": "Node.js", "category": "framework_library"},
            {"name": "PostgreSQL", "category": "database"},
            {"name": "C", "category": "programming_language"},
            {"name": "C++", "category": "programming_language"},
            {"name": "MysteryTech", "category": "other"},
        ])
        self.assertEqual(job["foreign_languages"], [
            {"language": "English", "level_text": "TOEIC 650"},
        ])

    def test_language_proficiency_keeps_explicit_source_phrase(self):
        job = normalize(topdev_row(requirements="Intermediate English is required."))
        self.assertEqual(job["foreign_languages"], [
            {"language": "English", "level_text": "Intermediate English"},
        ])

    def test_missing_foreign_language_is_stored_as_unknown(self):
        job = normalize(topdev_row(requirements="Strong analytical and teamwork skills."))
        self.assertEqual(job["foreign_languages"], [
            {"language": "unknown", "level_text": None},
        ])
        meta = job["normalization_meta"]["fields"]["foreign_languages"]
        self.assertIsNone(meta["source"])
        self.assertEqual(meta["confidence"], "none")

    def test_language_skill_variant_is_moved_and_explicit_b2_is_preserved(self):
        job = normalize(topdev_row(
            skills_arr=["Python", "English Communication"],
            requirements="English B2 is required.",
        ))
        self.assertNotIn("English Communication", [skill["name"] for skill in job["skills"]])
        self.assertEqual(job["foreign_languages"], [
            {"language": "English", "level_text": "English B2"},
        ])

    def test_overlapping_place_aliases_do_not_duplicate_city(self):
        job = normalize(topdev_row(addresses={
            "full_addresses": ["Bà Rịa - Vũng Tàu"],
            "address_region_array": ["Bà Rịa - Vũng Tàu"],
        }))
        self.assertEqual(job["cities"], ["Bà Rịa - Vũng Tàu"])

    def test_work_mode_uses_exact_description_fallback_and_never_defaults(self):
        remote = normalize(topdev_row(description="This role is fully remote."))
        unknown = normalize(topdev_row(description="Build and operate data products."))
        technical_terms = normalize(topdev_row(
            description="Manage hybrid cloud platforms and support remote users."
        ))
        self.assertEqual(remote["work_mode"], "remote")
        self.assertEqual(
            remote["normalization_meta"]["fields"]["work_mode"]["source"],
            "description",
        )
        self.assertIsNone(unknown["work_mode"])
        self.assertIsNone(technical_terms["work_mode"])


class FakeConnection:
    def __init__(self):
        self.writes = []

    def execute(self, query, values):
        self.writes.append((query, values))
        return SimpleNamespace(rowcount=1)


class NormalizeDataLoaderTests(unittest.TestCase):
    def test_schema_is_one_table_with_meta_and_without_explicit_index(self):
        sql = (PROJECT_ROOT / "data_preprocessing" / "sql" / "normalize_data_schema.sql").read_text(
            encoding="utf-8"
        ).lower()
        self.assertEqual(sql.count("create table"), 1)
        self.assertIn("normalize_data.normalized_jobs", sql)
        self.assertIn("normalization_meta", sql)
        self.assertNotIn("create index", sql)
        for removed in (
            "experience_text", "experience_min_years", "experience_max_years",
            "education_requirements", "published_at", "required_knowledge",
            "salary_text", "salary_min", "salary_max", "salary_currency",
            "salary_period", "salary_basis", "salary_status",
        ):
            self.assertNotIn(removed, sql)

    def test_loader_writes_only_normalize_data(self):
        connection = FakeConnection()
        summary = normalize_records(connection, [topdev_row()])
        self.assertEqual(summary.normalized, 1)
        self.assertEqual(summary.failed_write, 0)
        query = connection.writes[0][0]
        self.assertTrue(query.startswith("INSERT INTO normalize_data.normalized_jobs"))
        self.assertNotIn("INSERT INTO staging", query)

    def test_loader_removes_stale_normalized_row_after_gate_rejection(self):
        connection = FakeConnection()
        row = topdev_row(inLanguage="fr")

        summary = normalize_records(connection, [row])

        self.assertEqual(summary.rejected_language, 1)
        self.assertEqual(summary.removed_stale, 1)
        self.assertEqual(summary.normalized, 0)
        self.assertTrue(connection.writes[0][0].startswith(
            "DELETE FROM normalize_data.normalized_jobs"
        ))

    def test_dry_run_never_writes_or_deletes(self):
        connection = FakeConnection()

        valid_summary = normalize_records(connection, [topdev_row()], dry_run=True)
        rejected_summary = normalize_records(
            connection, [topdev_row(inLanguage="fr")], dry_run=True,
        )

        self.assertEqual(valid_summary.normalized, 1)
        self.assertEqual(rejected_summary.rejected_language, 1)
        self.assertEqual(connection.writes, [])


if __name__ == "__main__":
    unittest.main()
