"""Kiểm tra lớp lọc role trước normalize_data."""

from __future__ import annotations

import unittest

from data_preprocessing.normalization.role_gate import (
    detect_job_language,
    evaluate_role_gate,
    iter_normalization_candidates,
    load_role_catalog,
)


class RoleCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_role_catalog()

    def test_ds_markdown_is_the_catalog_source(self):
        roles = {definition.canonical_role for definition in self.catalog}
        self.assertGreaterEqual(len(roles), 70)
        self.assertIn("Back-end Developer", roles)
        self.assertIn("Site Reliability Engineer (SRE)", roles)

    def test_itviec_backend_title_is_accepted(self):
        decision = evaluate_role_gate(
            {"title": "Senior Backend Engineer", "job_expertise": ["Backend"]},
            source="itviec",
            catalog=self.catalog,
        )
        self.assertTrue(decision.accepted)
        self.assertIn("Back-end Developer", decision.matched_roles)

    def test_topdev_qa_title_is_accepted(self):
        decision = evaluate_role_gate(
            {"title": "QA Automation Engineer", "job_types_str": "Fulltime"},
            source="topdev",
            catalog=self.catalog,
        )
        self.assertTrue(decision.accepted)
        self.assertTrue({"QA Engineer", "Automation Test Engineer"}.intersection(decision.matched_roles))

    def test_acronym_matches_whole_token_only(self):
        self.assertTrue(evaluate_role_gate(
            {"title": "AI Engineer"}, source="topdev", catalog=self.catalog,
        ).accepted)
        self.assertFalse(evaluate_role_gate(
            {"title": "Retail Sales Manager"}, source="topdev", catalog=self.catalog,
        ).accepted)

    def test_only_english_and_vietnamese_are_accepted_languages(self):
        english = {"title": "Data Engineer", "description": "Build reliable data pipelines with Python."}
        vietnamese = {"title": "Kỹ sư dữ liệu", "description": "Phát triển hệ thống dữ liệu cho công ty."}
        korean = {"title": "Data Engineer", "description": "데이터 파이프라인을 구축하고 운영합니다."}
        japanese = {"title": "Data Engineer", "description": "データ基盤の開発と運用を担当します。"}
        french = {"title": "Ingénieur", "description": "Nous recherchons une personne avec expérience pour ce poste."}
        self.assertEqual(detect_job_language(english, "topdev"), "en")
        self.assertEqual(detect_job_language(vietnamese, "topdev"), "vi")
        self.assertEqual(detect_job_language(korean, "topdev"), "other")
        self.assertEqual(detect_job_language(japanese, "topdev"), "other")
        self.assertEqual(detect_job_language(french, "topdev"), "other")
        for payload in (korean, japanese):
            decision = evaluate_role_gate(payload, source="topdev", catalog=self.catalog)
            self.assertFalse(decision.accepted)
            self.assertEqual(decision.reason, "unsupported_language")

    def test_explicit_unsupported_language_is_rejected(self):
        decision = evaluate_role_gate(
            {"title": "Data Engineer", "inLanguage": "fr"},
            source="topdev",
            catalog=self.catalog,
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "unsupported_language")

    def test_non_it_and_missing_role_are_rejected(self):
        non_it = evaluate_role_gate(
            {"title": "Senior Accountant"}, source="itviec", catalog=self.catalog,
        )
        missing = evaluate_role_gate(
            {"description": "No role fields"}, source="itviec", catalog=self.catalog,
        )
        self.assertEqual(non_it.reason, "no_matching_role_keyword")
        self.assertEqual(missing.reason, "missing_role_text")

    def test_real_source_aliases_map_to_roles_in_ds(self):
        cases = {
            "SAP SD Consultant": "IT Consultant",
            "Chief Enterprise Architect (CEA)": "Technical Architect",
            "Network Engineer": "Systems Engineer",
            "[2026_KTUD]_Chuyên viên Kiểm thử ứng dụng": "Tester",
            "KOREAN IT INTERPRETER": "IT Communicator",
        }
        for title, expected in cases.items():
            with self.subTest(title=title):
                decision = evaluate_role_gate(
                    {"title": title}, source="topdev", catalog=self.catalog,
                )
                self.assertTrue(decision.accepted)
                self.assertIn(expected, decision.matched_roles)

    def test_iterator_skips_rejected_job_and_continues(self):
        rows = [
            {"job_id": "topdev:1", "source": "topdev", "raw_payload": {"title": "Accountant"}},
            {"job_id": "topdev:2", "source": "topdev", "raw_payload": {"title": "Data Engineer"}},
            {"job_id": "topdev:3", "source": "topdev", "raw_payload": {"title": "HR Manager"}},
            {"job_id": "topdev:4", "source": "topdev", "raw_payload": {"title": "SRE"}},
        ]
        rejected = []
        accepted = list(iter_normalization_candidates(
            rows,
            catalog=self.catalog,
            on_reject=lambda row, decision: rejected.append((row["job_id"], decision.reason)),
        ))
        self.assertEqual([row["job_id"] for row, _ in accepted], ["topdev:2", "topdev:4"])
        self.assertEqual([job_id for job_id, _ in rejected], ["topdev:1", "topdev:3"])


if __name__ == "__main__":
    unittest.main()
