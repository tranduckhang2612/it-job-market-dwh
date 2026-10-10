import unittest

from data_preprocessing.itviec_normalize_data.normalize_itviec import normalize_experience, normalize_salary


class SalaryTests(unittest.TestCase):
    def salary(self, text, **kwargs):
        return normalize_salary({"salary": {"text": text, **kwargs}})

    def test_vnd_range_and_explicit_period_basis(self):
        actual = self.salary("20–30 triệu VND/tháng, gross")
        self.assertEqual((actual["salary_min"], actual["salary_max"]), (20_000_000, 30_000_000))
        self.assertEqual((actual["salary_currency"], actual["salary_period"], actual["salary_basis"]), ("VND", "month", "gross"))

    def test_vnd_thousands_and_commas(self):
        for text in ["20.000.000 - 30.000.000 VND", "20 000 000 - 30 000 000 VND", "20,000,000–30,000,000 VND"]:
            with self.subTest(text=text):
                actual = self.salary(text)
                self.assertEqual((actual["salary_min"], actual["salary_max"]), (20_000_000, 30_000_000))

    def test_usd_range(self):
        for text in ["1,500–3,000 USD/month", "$1,500 to $3,000/month", "1.500 - 3.000 USD/month"]:
            with self.subTest(text=text):
                actual = self.salary(text)
                self.assertEqual((actual["salary_min"], actual["salary_max"], actual["salary_currency"]), (1500, 3000, "USD" if "USD" in text else None))

    def test_decimal_million_and_k(self):
        actual = self.salary("20,5–30,5 triệu VND/tháng")
        self.assertEqual((actual["salary_min"], actual["salary_max"]), (20_500_000, 30_500_000))
        actual = self.salary("$1.5k–$3k/month")
        self.assertEqual((actual["salary_min"], actual["salary_max"]), (1500, 3000))

    def test_one_sided_bounds(self):
        for text, low, high in [("Up to 3,000 USD", None, 3000), ("Tối đa 30 triệu VND", None, 30_000_000), ("From 2,000 USD", 2000, None), ("Từ 20tr VND", 20_000_000, None), ("$2k+", 2000, None)]:
            with self.subTest(text=text):
                result = self.salary(text)
                self.assertEqual((result["salary_min"], result["salary_max"]), (low, high))

    def test_no_invented_currency_period_or_gross(self):
        result = self.salary("20–30 triệu")
        self.assertEqual(result["salary_min"], 20_000_000)
        for key in ["salary_currency", "salary_period", "salary_basis"]:
            self.assertIsNone(result[key])

    def test_vietnamese_range_connector_is_not_currency_marker(self):
        result = self.salary("Từ 20 triệu đến 30 triệu")
        self.assertEqual((result["salary_min"], result["salary_max"]), (20_000_000, 30_000_000))
        self.assertIsNone(result["salary_currency"])

    def test_structured_numeric(self):
        result = self.salary(None, structured={"currency": "USD", "value": {"minValue": 1500, "maxValue": 3000, "unitText": "YEAR"}})
        self.assertEqual((result["salary_min"], result["salary_max"], result["salary_period"]), (1500, 3000, "year"))
        result = self.salary(None, structured={"currency": "VND", "value": {"value": 20_000_000}})
        self.assertEqual((result["salary_min"], result["salary_max"], result["salary_period"]), (20_000_000, 20_000_000, None))

    def test_visible_text_wins_over_structured_bounds(self):
        result = self.salary("Up to 3000 USD/month", structured={"currency": "USD", "value": {"minValue": 1, "maxValue": 2, "unitText": "YEAR"}})
        self.assertEqual((result["salary_min"], result["salary_max"], result["salary_period"]), (None, 3000, "month"))

    def test_login_required_does_not_leak_structured_salary(self):
        result = self.salary("Sign in to view salary", visibility="login_required", structured={"currency": "USD", "value": {"minValue": 1500, "maxValue": 3000, "unitText": "MONTH"}})
        self.assertEqual(result["salary_status"], "unknown")
        for key in ["salary_min", "salary_max", "salary_currency", "salary_period", "salary_basis"]:
            self.assertIsNone(result[key])

    def test_statuses_no_zero_sentinel(self):
        for text, status in [("Thỏa thuận", "negotiable"), ("Thoả thuận", "negotiable"), ("Negotiable", "negotiable"), ("You'll love it", "not_disclosed"), ("Competitive salary", "not_disclosed"), (None, "unknown"), ("0 VND", "unknown"), ("13th month bonus", "unknown")]:
            with self.subTest(text=text):
                result = self.salary(text)
                self.assertEqual(result["salary_status"], status)
                self.assertIsNone(result["salary_min"])
                self.assertIsNone(result["salary_max"])

    def test_bonus_numbers_not_interpreted_as_base_salary(self):
        result = self.salary("13th month bonus; base salary 2000 USD/month")
        self.assertEqual((result["salary_min"], result["salary_max"]), (2000, 2000))

    def test_explicit_nonnumeric_text_wins_conflicting_jsonld(self):
        for text, status in [("Negotiable, gross", "negotiable"), ("You'll love it", "not_disclosed")]:
            result = self.salary(text, structured={"currency": "USD", "value": {"minValue": 1500, "maxValue": 3000, "unitText": "MONTH"}})
            self.assertEqual(result["salary_status"], status)
            self.assertIsNone(result["salary_min"])
            self.assertIsNone(result["salary_currency"])
            self.assertIsNone(result["salary_period"])

    def test_keep_explicit_qualifiers_on_negotiable_salary(self):
        result = self.salary("Negotiable USD/month, gross")
        self.assertEqual((result["salary_status"], result["salary_currency"], result["salary_period"], result["salary_basis"]), ("negotiable", "USD", "month", "gross"))

    def test_ambiguous_dollar_symbol_not_invented_usd(self):
        result = self.salary("$1500–$3000/month")
        self.assertIsNone(result["salary_currency"])
        result = self.salary("$1500–$3000/month", structured={"currency": "SGD"})
        self.assertEqual(result["salary_currency"], "SGD")

    def test_explicit_equivalent_salary_basis(self):
        for phrase in ["trước thuế", "before tax", "pre-tax", "gross"]:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.salary("20 triệu VND/tháng, " + phrase)["salary_basis"], "gross")
        for phrase in ["sau thuế", "after tax", "post-tax", "take-home", "take home", "net"]:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.salary("20 triệu VND/tháng, " + phrase)["salary_basis"], "net")
        self.assertIsNone(self.salary("20 triệu VND/tháng")["salary_basis"])
        self.assertIsNone(self.salary("20 triệu VND gross/net")["salary_basis"])

    def test_blank_structured_currency_does_not_default(self):
        result = self.salary("20–30 triệu/tháng", structured={"currency": "   ", "value": {"unitText": "MONTH"}})
        self.assertIsNone(result["salary_currency"])

    def test_conflicting_currency_offers_are_not_one_salary(self):
        result = self.salary("2000 USD or 50 triệu VND/month", structured={"currency": "USD", "value": {"value": 2000}})
        self.assertEqual(result["salary_status"], "unknown")
        self.assertIsNone(result["salary_currency"])
        self.assertIsNone(result["salary_min"])
        self.assertIsNone(result["salary_max"])

    def test_different_net_gross_amounts_not_collapsed(self):
        result = self.salary("2000 USD gross / 1800 USD net")
        self.assertEqual(result["salary_status"], "unknown")
        self.assertIsNone(result["salary_min"])
        self.assertIsNone(result["salary_max"])
        self.assertEqual(result["salary_text"], "2000 USD gross / 1800 USD net")

    def test_invalid_displayed_range_does_not_fall_back_to_jsonld(self):
        result = self.salary("3000–2000 USD/month", structured={"currency": "USD", "value": {"minValue": 2000, "maxValue": 3000}})
        self.assertEqual(result["salary_status"], "unknown")
        self.assertIsNone(result["salary_min"])
        self.assertIsNone(result["salary_max"])

    def test_zero_display_does_not_become_structured_placeholder_salary(self):
        result = self.salary("0 VND", structured={"currency": "VND", "value": {"value": 1}})
        self.assertEqual(result["salary_status"], "unknown")
        self.assertIsNone(result["salary_min"])


class ExperienceTests(unittest.TestCase):
    def experience(self, text, **kwargs):
        return normalize_experience({"your_skills_and_experience": text, **kwargs})

    def assert_bounds(self, text, low, high, **kwargs):
        actual = self.experience(text, **kwargs)
        self.assertEqual((actual["experience_min_years"], actual["experience_max_years"]), (low, high))
        return actual

    def test_words_and_greatest_conjunctive_minimum(self):
        text = "At least five years of experience as an SRE.\nAt least one year of experience at senior level."
        actual = self.assert_bounds(text, 5, None)
        self.assertIn("five years", actual["experience_text"])
        self.assert_bounds("Tối thiểu năm năm kinh nghiệm", 5, None)

    def test_ranges(self):
        for text in ["Yêu cầu từ 1 đến 3 năm kinh nghiệm", "2 to 3 years of hands-on experience", "Years of Experience: 2 - 5 years as a BA", "Between one and three years of experience"]:
            with self.subTest(text=text):
                low, high = (2, 5) if "BA" in text else (2, 3) if "hands-on" in text else (1, 3)
                self.assert_bounds(text, low, high)

    def test_months_and_fractional_years(self):
        self.assert_bounds("6–18 months of experience", 0.5, 1.5)
        self.assert_bounds("6 months to 1 year experience", 0.5, 1)
        self.assert_bounds("1 year 6 months of experience", 1.5, None)
        self.assert_bounds("One and a half years of experience", 1.5, None)

    def test_one_sided_bounds(self):
        self.assert_bounds("3+ years’ professional experience", 3, None)
        self.assert_bounds("Up to 3 years of experience", None, 3)
        self.assert_bounds("Có từ 2 năm kinh nghiệm", 2, None)

    def test_missing_not_fresher_or_zero(self):
        for text in [None, "Fresher / Intern welcome", "Sinh viên năm 4, tốt nghiệp dưới 01 năm Đại học", "Company founded 20 years ago", "14 days annual leave", "Familiar with Python"]:
            with self.subTest(text=text):
                self.assert_bounds(text, None, None)

    def test_no_company_history_and_graduation_duration(self):
        self.assert_bounds("Our company has 30 years of experience", None, None)
        self.assert_bounds("Graduated within 1 year, some experience with Python", None, None)
        self.assert_bounds("Our company has 30 years of experience. At least 5 years of development experience required.", 5, None)

    def test_only_requirements_not_company_or_benefits(self):
        raw = {"your_skills_and_experience": "Python", "company_info": {"text": "30 years of experience"}, "why_youll_love_working_here": "13 months salary; 3 years retention bonus", "job_description": "At least 10 years of experience"}
        result = normalize_experience(raw)
        self.assertIsNone(result["experience_min_years"])

    def test_explicit_no_experience(self):
        for text in ["No prior experience required", "Không yêu cầu kinh nghiệm", "0 years of experience required"]:
            with self.subTest(text=text):
                self.assert_bounds(text, 0, None)

    def test_structured_fallback_not_bucket(self):
        for months, expected in [(24, 2), (6, 0.5), (0, 0), (10, None), (37, None)]:
            with self.subTest(months=months):
                result = self.assert_bounds("Python experience", expected, None, experience_requirements_structured={"monthsOfExperience": months})
                self.assertEqual(result["experience_text"], "Python experience")

    def test_narrative_wins_over_website_bucket(self):
        self.assert_bounds("At least 5 years of experience", 5, None, experience_requirements_structured={"monthsOfExperience": 37})

    def test_structured_text_exact_evidence(self):
        result = self.assert_bounds(None, 2, 3, experience_requirements_structured="2–3 years")
        self.assertEqual(result["experience_text"], "2–3 years")
        self.assert_bounds(None, None, None, experience_requirements_structured="No requirements")

    def test_typical_and_preferred_do_not_become_mandatory_minimum(self):
        text = "Our Senior Engineers typically have 8+ years of experience, but we grade based on expertise over years of experience"
        result = self.assert_bounds(text, None, None, experience_requirements_structured={"monthsOfExperience": 37})
        self.assertIn("8+ years", result["experience_text"])
        result = self.assert_bounds("2 years of experience required; 5 years of experience is a plus", 2, None)
        self.assertIn("5 years", result["experience_text"])

    def test_structured_json_fallback_only_without_prose(self):
        result = self.assert_bounds(None, None, None, experience_requirements_structured={"monthsOfExperience": 37})
        self.assertIn("monthsOfExperience", result["experience_text"])
        result = self.assert_bounds("Proven experience with Python", None, None, experience_requirements_structured={"monthsOfExperience": 37})
        self.assertEqual(result["experience_text"], "Proven experience with Python")

    def test_abbreviations_preserve_complete_original_bullet(self):
        text = "- Experience in front-end frameworks (e.g. React, Angular, Vue) is a plus.\nOther Requirements\n- Strong written and spoken English"
        result = self.assert_bounds(text, None, None)
        self.assertEqual(result["experience_text"], text.split("\n")[0])

    def test_wrapped_bullets_and_nested_lists_preserve_original_newlines(self):
        text = "Required Qualifications:\n● Strong hands-on experience with:\n○ Test automation\n○ Designing test matrices across multiple OS versions, hardware\nconfigurations, and storage targets\n○ Regression strategy\n● Good communication skills\nDesired Qualifications:\n● Experience with cloud platforms, backup/storage products, virtualization, or\ninfrastructure-related software\n● Experience using AI tools (ChatGPT, Copilot, Cursor, Claude, etc.) to improve\nQA workflows\nGiven the high volume of CVs applied, we can only manage to respond within 5 working days."
        result = self.assert_bounds(text, None, None)
        evidence = result["experience_text"]
        self.assertIn("○ Designing test matrices across multiple OS versions, hardware\nconfigurations, and storage targets", evidence)
        self.assertIn("virtualization, or\ninfrastructure-related software", evidence)
        self.assertIn("etc.) to improve\nQA workflows", evidence)
        self.assertNotIn("communication skills", evidence)
        self.assertNotIn("Given the high volume", evidence)

    def test_standalone_labels_and_cv_checklist_not_requirements(self):
        text = "Experience\n- Kinh nghiệm làm việc\n- Số điện thoại và Email liên hệ"
        result = self.assert_bounds(text, None, None, experience_requirements_structured="No requirements")
        self.assertEqual(result["experience_text"], "No requirements")

    def test_scoped_waiver_does_not_cancel_general_experience(self):
        for waiver in ["No prior experience required in Kubernetes", "No experience with Kubernetes required", "Không yêu cầu kinh nghiệm Kubernetes", "Không cần kinh nghiệm về Java"]:
            with self.subTest(waiver=waiver):
                self.assert_bounds("At least 3 years of experience. " + waiver, 3, None)

    def test_scoped_waiver_alone_does_not_establish_zero_for_role(self):
        for text in ["No prior experience required in Kubernetes", "Không yêu cầu kinh nghiệm Kubernetes", "0 years of Kubernetes experience required"]:
            with self.subTest(text=text):
                self.assert_bounds(text, None, None)

    def test_separate_lower_upper_bounds_are_intersected(self):
        self.assert_bounds("At least 3 years of experience. Up to 5 years of experience.", 3, 5)
        self.assert_bounds("At least 3 years of experience, up to 5 years of experience.", 3, 5)

    def test_required_preferred_scope_within_same_sentence(self):
        for text in ["2 years of experience required, 5 years of experience preferred.", "2 years of experience required and 5 years of experience preferred.", "2 years of experience required, 5 years preferred."]:
            with self.subTest(text=text):
                self.assert_bounds(text, 2, None)

    def test_preferred_section_does_not_override_required_section(self):
        text = "Required Qualifications:\n- 2 years of experience\nPreferred Qualifications:\n- 5 years of experience"
        self.assert_bounds(text, 2, None)

    def test_preferred_only_numeric_prose_does_not_get_mandatory_jsonld_fallback(self):
        self.assert_bounds("5 years of experience preferred", None, None, experience_requirements_structured={"monthsOfExperience": 24})

    def test_wrapped_duration_keeps_numeric_evidence(self):
        result = self.assert_bounds("- 3+ years of\nexperience building APIs", 3, None)
        self.assertEqual(result["experience_text"], "- 3+ years of\nexperience building APIs")

    def test_alternative_experience_paths_do_not_become_maximum_required_minimum(self):
        self.assert_bounds("3 years of Python experience or 5 years of Java experience", None, None)
        self.assert_bounds("Either 2–3 years of Python experience or 5–7 years of Java experience", None, None)
        self.assert_bounds("3 years of Python experience, or 5 years of Java experience", None, None)
        self.assert_bounds("At least 3 years of work experience. Either 2 years of Python experience or 5 years of Java experience.", 3, None)

    def test_unrelated_duration_does_not_block_structured_experience(self):
        self.assert_bounds("University education for 4 years", 2, None, experience_requirements_structured={"monthsOfExperience": 24})

    def test_role_alternatives_do_not_cancel_conjunctive_duration(self):
        self.assert_bounds("5+ years of experience in cloud architecture, system engineering, or related roles, with at least 3 years of deep, hands-on expertise with GCP.", 5, None)

    def test_required_years_are_preserved_before_preferred_domain_experience(self):
        text = "- Tối thiểu 5 năm kinh nghiệm làm Product Owner ưu tiên có kinh nghiệm thực chiến phát triển các sản phẩm (ưu tiên theo thứ tự 1 - 2 - 3):"
        result = self.assert_bounds(text, 5, None, experience_requirements_structured={"monthsOfExperience": 37})
        self.assertEqual(result["experience_text"], text)
        self.assert_bounds("At least 3 years of software experience preferably with 5 years of fintech experience", 3, None)
        self.assert_bounds("Ưu tiên có 5 năm kinh nghiệm làm Product Owner", None, None)
        self.assert_bounds("5 years of experience preferably", None, None)

    def test_61_month_website_bucket_is_not_a_precise_year_requirement(self):
        result = self.assert_bounds("Proven experience launching technology products", None, None,
                                   experience_requirements_structured={"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 61})
        self.assertEqual(result["experience_text"], "Proven experience launching technology products")
        # Explicit narrative months remain valid evidence; only the known
        # structured UI bucket is ignored.
        self.assert_bounds("61 months of experience required", 5.083333, None)

    def test_contradictory_bounds_are_retained_as_unknown(self):
        for text in ["At least 5 years of experience; up to 3 years of experience", "5–2 years of experience", "No experience required. At least 3 years of experience required."]:
            with self.subTest(text=text):
                self.assert_bounds(text, None, None)


if __name__ == "__main__":
    unittest.main()
