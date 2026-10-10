import importlib.util
import json
import os
from pathlib import Path
import unittest


MODULE_PATH = Path(os.environ.get("ITVIEC_NORMALIZER_TEST_MODULE", str(Path(__file__).resolve().parents[1] / "data_preprocessing/itviec_normalize_data/normalize_itviec.py")))
SPEC = importlib.util.spec_from_file_location("normalization_skills", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
normalize = MODULE.normalize_requirements


def by_skill(result):
    return {entry["name"]: entry for entry in result["skills"]}


class NormalizeRequirementsTests(unittest.TestCase):
    def test_missing_lists_are_empty(self):
        result = normalize({})
        self.assertEqual(set(result), {"skills", "required_knowledge", "education_requirements", "foreign_languages", "soft_skills"})
        self.assertTrue(all(value == [] for value in result.values()))

    def test_aliases_unknown_tags_and_deduplication(self):
        result = normalize({"skills": ["Golang", "go", "Go", "ReactJS", "react.js", "Agentic AI", "Mysterious Vendor Platform", "Mysterious Vendor Platform"]})
        skills = by_skill(result)
        self.assertEqual(set(skills), {"Go", "React", "Agentic AI", "Mysterious Vendor Platform"})
        self.assertEqual(skills["Go"], {"name": "Go", "category": "programming_language", "requirement_type": "unspecified"})
        self.assertEqual(skills["Mysterious Vendor Platform"]["category"], "other")
        self.assertEqual(skills["Agentic AI"]["category"], "other")

    def test_explicit_headings_scope_and_reset(self):
        result = normalize({"your_skills_and_experience": "Required Skills\n- Python and SQL.\nNice to have:\n- Docker; Kubernetes.\nEducation\n- Bachelor's degree in Computer Science."})
        skills = by_skill(result)
        self.assertEqual(skills["Python"]["requirement_type"], "required")
        self.assertEqual(skills["SQL"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")
        self.assertEqual(skills["Kubernetes"]["requirement_type"], "preferred")
        self.assertEqual(result["education_requirements"][0]["requirement_type"], "unspecified")

    def test_vietnamese_preferred_section_does_not_default_everything_required(self):
        result = normalize({"your_skills_and_experience": "- Có kinh nghiệm Python.\nƯu tiên:\n+ Có kinh nghiệm Docker, Git và PostgreSQL.\nHọc vấn:\n- Tốt nghiệp Đại học ngành Công nghệ thông tin."})
        skills = by_skill(result)
        self.assertEqual(skills["Python"]["requirement_type"], "unspecified")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")
        self.assertEqual(result["education_requirements"], [{"degree": "bachelor", "majors": ["Information Technology"], "requirement_type": "unspecified"}])

    def test_inline_and_clause_preference_is_scoped(self):
        result = normalize({"your_skills_and_experience": "Required Skills\n- Strong Java, React (preferred), Angular and Vue.js.\n- Google Cloud Platform is mandatory; additional AWS or Azure experience is an advantage."})
        skills = by_skill(result)
        for name in ["Java", "Angular", "Vue.js", "GCP"]:
            self.assertEqual(skills[name]["requirement_type"], "required", name)
        for name in ["React", "AWS", "Azure"]:
            self.assertEqual(skills[name]["requirement_type"], "preferred", name)

    def test_unqualified_prose_and_narrow_preference_stay_unspecified(self):
        result = normalize({"skills": ["Python", "GCP"], "your_skills_and_experience": "- Strong proficiency in Python.\n- Python computer vision experience is a plus.\n- Hands-on experience with Google Cloud Platform.\n- Additional GCP certifications are preferred."})
        skills = by_skill(result)
        self.assertEqual(skills["Python"]["requirement_type"], "unspecified")
        self.assertEqual(skills["GCP"]["requirement_type"], "unspecified")

    def test_tag_without_prose_requirement_does_not_veto_explicit_preference(self):
        result = normalize({"skills": ["Golang", "Docker"], "your_skills_and_experience": "- Go experience is preferred.\nNice to have:\n- Docker experience."})
        self.assertEqual(by_skill(result)["Go"]["requirement_type"], "preferred")
        self.assertEqual(by_skill(result)["Docker"]["requirement_type"], "preferred")

    def test_any_explicit_required_evidence_takes_priority(self):
        result = normalize({"skills": ["Python"], "your_skills_and_experience": "- Python computer vision is a plus.\n- Python is mandatory.\n- Hands-on Python experience."})
        self.assertEqual(by_skill(result)["Python"]["requirement_type"], "required")

    def test_knowledge_is_explicit_and_merged(self):
        result = normalize({"skills": ["MongoDB"], "your_skills_and_experience": "Kiến thức Data Structures và Algorithms; cấu trúc dữ liệu và thuật toán.\nHiểu biết hệ điều hành, mạng máy tính, cơ sở dữ liệu, OOP và Design Patterns."})
        self.assertEqual(result["required_knowledge"], ["Data Structures and Algorithms", "Operating Systems", "Networking", "Database", "Object-Oriented Programming", "Design Patterns"])
        self.assertEqual(normalize({"skills": ["MongoDB"]})["required_knowledge"], [])

    def test_education_majors_and_preference_with_unicode_apostrophe(self):
        result = normalize({"your_skills_and_experience": "Preferred Qualifications\n- Bachelor’s degree in Computer Science or Information Technology.\n- Bachelor’s degree in Computer Science or Information Technology."})
        self.assertEqual(result["education_requirements"], [{"degree": "bachelor", "majors": ["Computer Science", "Information Technology"], "requirement_type": "preferred"}])

    def test_structured_education(self):
        result = normalize({"education_requirements_structured": [{"@type": "EducationalOccupationalCredential", "credentialCategory": "Master's degree", "name": "Software Engineering"}]})
        self.assertEqual(result["education_requirements"], [{"degree": "master", "majors": ["Software Engineering"], "requirement_type": "unspecified"}])

    def test_languages_keep_original_descriptions_not_guessed_scores(self):
        text = "- Bắt buộc có khả năng đọc hiểu tài liệu kỹ thuật bằng tiếng Anh.\n- Tiếng Nhật N2 là lợi thế."
        result = normalize({"skills": ["English", "Japanese"], "your_skills_and_experience": text})
        languages = {x["language"]: x for x in result["foreign_languages"]}
        self.assertEqual(languages["English"]["level_text"], "Bắt buộc có khả năng đọc hiểu tài liệu kỹ thuật bằng tiếng Anh.")
        self.assertEqual(languages["English"]["requirement_type"], "required")
        self.assertEqual(languages["Japanese"]["requirement_type"], "preferred")
        self.assertEqual(result["skills"], [])
        self.assertNotIn("IELTS", json.dumps(result))
        self.assertNotIn("B2", json.dumps(result))

    def test_explicit_test_scores_are_retained(self):
        result = normalize({"your_skills_and_experience": "TOEIC 600 or Relevant\nJapanese proficiency equivalent to JLPT N2 or above."})
        languages = {x["language"]: x for x in result["foreign_languages"]}
        self.assertEqual(languages["English"]["level_text"], "TOEIC 600 or Relevant")
        self.assertIn("JLPT N2", languages["Japanese"]["level_text"])

    def test_explicit_title_language_level_with_language_tag(self):
        result = normalize({"title": "Project Leader (Japanese N2+)", "skills": ["Japanese"]})
        self.assertEqual(result["foreign_languages"], [{"language": "Japanese", "level_text": "Project Leader (Japanese N2+)", "requirement_type": "unspecified"}])

    def test_foreign_customer_nationality_not_language_requirement(self):
        result = normalize({"your_skills_and_experience": "Experience working directly with Japanese customers.\nFrench company founded in 1999."})
        self.assertEqual(result["foreign_languages"], [])

    def test_soft_skills_are_canonical_deduplicated(self):
        result = normalize({"skills": ["Leadership"], "your_skills_and_experience": "Team player with communication and problem-solving skills.\nKỹ năng giao tiếp, làm việc nhóm, giải quyết vấn đề và lãnh đạo."})
        self.assertEqual(set(result["soft_skills"]), {"Leadership", "Communication", "Teamwork", "Problem Solving"})
        self.assertEqual(result["skills"], [])

    def test_short_names_and_word_boundaries_do_not_create_technologies(self):
        result = normalize({"your_skills_and_experience": "Ability to go to work and work under pressure. Go to the office.\nNo communication salary bonus is implied by this section.\nYou must write practical reports on JavaScript and PostgreSQL."})
        skills = by_skill(result)
        self.assertEqual(set(skills), {"JavaScript", "PostgreSQL"})
        self.assertEqual(skills["JavaScript"]["requirement_type"], "required")

    def test_training_and_benefits_not_entry_requirements(self):
        result = normalize({"your_skills_and_experience": "Proficient in Python. We’ll upskill you in Ruby when you join.", "why_youll_love_working_here": "Paid English training, Docker certifications and a master's degree stipend", "job_description": "Our team uses Java and MongoDB."})
        self.assertEqual(set(by_skill(result)), {"Python"})
        self.assertEqual(result["foreign_languages"], [])
        self.assertEqual(result["education_requirements"], [])

    def test_application_footer_and_academic_award_are_not_requirements(self):
        result = normalize({"your_skills_and_experience": "Tốt nghiệp Đại học chuyên ngành Công nghệ thông tin.\nƯu tiên tốt nghiệp loại Giỏi, Xuất sắc, GPA cao tại Đại học.\nMB Bank yêu cầu ứng viên ứng tuyển cần cung cấp chi tiết các thông tin sau:\nPython bắt buộc và tiếng Anh IELTS 9.0."})
        self.assertEqual(len(result["education_requirements"]), 1)
        self.assertEqual(result["foreign_languages"], [])
        self.assertEqual(result["skills"], [])

    def test_negated_mandatory_cue_does_not_become_required(self):
        result = normalize({"your_skills_and_experience": "Python is not required.\nKhông bắt buộc tiếng Anh giao tiếp."})
        self.assertEqual(by_skill(result)["Python"]["requirement_type"], "unspecified")
        self.assertEqual(result["foreign_languages"][0]["requirement_type"], "unspecified")

    def test_fallback_requirement_section(self):
        result = normalize({"sections": [{"heading": "Why you'll love working here", "text": "Python"}, {"heading": "Your skills and experience", "text": "Golang is mandatory."}]})
        self.assertEqual(by_skill(result)["Go"]["requirement_type"], "required")
        self.assertEqual(set(by_skill(result)), {"Go"})

    def test_comma_mandatory_and_preferred_are_independent(self):
        skills = by_skill(normalize({"requirements": "Python is mandatory, Docker preferred."}))
        self.assertEqual(skills["Python"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_coordinated_mandatory_and_preferred_are_independent(self):
        for text in ["Python required and Docker preferred.", "Python bắt buộc và Docker ưu tiên."]:
            with self.subTest(text=text):
                skills = by_skill(normalize({"requirements": text}))
                self.assertEqual(skills["Python"]["requirement_type"], "required")
                self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_shared_prefix_qualifies_complete_list(self):
        skills = by_skill(normalize({"requirements": "Required: Python, Java and SQL."}))
        self.assertEqual({entry["requirement_type"] for entry in skills.values()}, {"required"})

    def test_shared_postfix_qualifies_complete_list(self):
        skills = by_skill(normalize({"requirements": "Python, Java and SQL are required."}))
        self.assertEqual({entry["requirement_type"] for entry in skills.values()}, {"required"})

    def test_shared_prefix_stops_at_independent_preference(self):
        skills = by_skill(normalize({"requirements": "Required: Python, Java, Docker preferred."}))
        self.assertEqual(skills["Python"]["requirement_type"], "required")
        self.assertEqual(skills["Java"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_shared_postfix_stops_at_independent_preference(self):
        skills = by_skill(normalize({"requirements": "Python, Java required, Docker preferred."}))
        self.assertEqual(skills["Python"]["requirement_type"], "required")
        self.assertEqual(skills["Java"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_postfix_does_not_require_next_unqualified_item(self):
        skills = by_skill(normalize({"requirements": "Python required, hands-on Docker experience."}))
        self.assertEqual(skills["Python"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "unspecified")

    def test_preference_does_not_reclassify_independent_unqualified_experience(self):
        skills = by_skill(normalize({"requirements": "Strong Python experience, Docker preferred."}))
        self.assertEqual(skills["Python"]["requirement_type"], "unspecified")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_unqualified_common_prefix_can_share_postfix_preference(self):
        skills = by_skill(normalize({"requirements": "Experience in Python and Docker is preferred."}))
        self.assertEqual(skills["Python"]["requirement_type"], "preferred")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_internal_technology_dots_are_not_sentence_boundaries(self):
        skills = by_skill(normalize({"requirements": "Required Node.js and ASP.NET, Docker preferred."}))
        self.assertEqual(skills["Node.js"]["requirement_type"], "required")
        self.assertEqual(skills["ASP.NET"]["requirement_type"], "required")
        self.assertEqual(skills["Docker"]["requirement_type"], "preferred")

    def test_short_language_requirements_and_preferences(self):
        text = "English required, Japanese preferred."
        languages = {entry["language"]: entry for entry in normalize({"requirements": text})["foreign_languages"]}
        self.assertEqual(languages["English"]["requirement_type"], "required")
        self.assertEqual(languages["Japanese"]["requirement_type"], "preferred")
        self.assertEqual(languages["English"]["level_text"], text)
        self.assertEqual(languages["Japanese"]["level_text"], text)

    def test_bare_language_does_not_invent_proficiency(self):
        languages = normalize({"requirements": "English"})["foreign_languages"]
        self.assertEqual(languages, [{"language": "English", "level_text": "English", "requirement_type": "unspecified"}])

    def test_language_heading_supplies_requirement_type(self):
        languages = normalize({"requirements": "Required languages:\nEnglish\nJapanese"})["foreign_languages"]
        self.assertEqual({entry["language"] for entry in languages}, {"English", "Japanese"})
        self.assertTrue(all(entry["requirement_type"] == "required" for entry in languages))

    def test_language_negation_is_local(self):
        languages = {entry["language"]: entry for entry in normalize({"requirements": "English required, Japanese not required."})["foreign_languages"]}
        self.assertEqual(languages["English"]["requirement_type"], "required")
        self.assertEqual(languages["Japanese"]["requirement_type"], "unspecified")

    def test_language_preference_does_not_change_another_explicit_proficiency(self):
        for text in ["English fluent, Japanese preferred.", "Fluent English, Japanese preferred.", "English C1, Japanese preferred."]:
            with self.subTest(text=text):
                languages = {entry["language"]: entry for entry in normalize({"requirements": text})["foreign_languages"]}
                self.assertEqual(languages["English"]["requirement_type"], "unspecified")
                self.assertEqual(languages["Japanese"]["requirement_type"], "preferred")

    def test_customer_nationality_does_not_borrow_other_language_cues(self):
        languages = normalize({"requirements": "Experience working with Japanese customers is required. English required."})["foreign_languages"]
        self.assertEqual([entry["language"] for entry in languages], ["English"])

    def test_degrees_keep_independent_majors_and_requirement_types(self):
        entries = normalize({"requirements": "Bachelor's degree in Computer Science required, Master's degree in Data Science preferred."})["education_requirements"]
        by_degree = {entry["degree"]: entry for entry in entries}
        self.assertEqual(by_degree["bachelor"], {"degree": "bachelor", "majors": ["Computer Science"], "requirement_type": "required"})
        self.assertEqual(by_degree["master"], {"degree": "master", "majors": ["Data Science"], "requirement_type": "preferred"})

    def test_alternative_degrees_share_explicit_major(self):
        entries = normalize({"requirements": "Bachelor's or Master's degree in Computer Science required."})["education_requirements"]
        self.assertEqual({entry["degree"] for entry in entries}, {"bachelor", "master"})
        self.assertTrue(all(entry["majors"] == ["Computer Science"] and entry["requirement_type"] == "required" for entry in entries))

    def test_degree_preference_does_not_change_independent_degree(self):
        entries = normalize({"requirements": "Bachelor's degree in Computer Science, Master's preferred."})["education_requirements"]
        by_degree = {entry["degree"]: entry for entry in entries}
        self.assertEqual(by_degree["bachelor"]["requirement_type"], "unspecified")
        self.assertEqual(by_degree["bachelor"]["majors"], ["Computer Science"])
        self.assertEqual(by_degree["master"]["requirement_type"], "preferred")
        self.assertEqual(by_degree["master"]["majors"], [])

    def test_negated_degree_is_not_a_positive_requirement(self):
        entries = normalize({"requirements": "No Bachelor's degree is required; Master's degree in Computer Science preferred."})["education_requirements"]
        self.assertEqual(entries, [{"degree": "master", "majors": ["Computer Science"], "requirement_type": "preferred"}])

    def test_optional_but_explicitly_preferred_degree(self):
        entries = normalize({"requirements": "Bachelor's degree not mandatory but preferred."})["education_requirements"]
        self.assertEqual(entries, [{"degree": "bachelor", "majors": [], "requirement_type": "preferred"}])

    def test_degree_abbreviation_retains_major(self):
        entries = normalize({"requirements": "B.Sc. in Computer Science required."})["education_requirements"]
        self.assertEqual(entries, [{"degree": "bachelor", "majors": ["Computer Science"], "requirement_type": "required"}])

    def test_negated_knowledge_not_claimed_as_required(self):
        result = normalize({"requirements": "No Networking knowledge required; Database knowledge is mandatory."})
        self.assertEqual(result["required_knowledge"], ["Database"])

    def test_training_exclusion_applies_to_every_requirement_group(self):
        result = normalize({"requirements": "Python required. You will learn English, Networking and obtain a master degree after joining."})
        self.assertEqual(set(by_skill(result)), {"Python"})
        self.assertEqual(result["foreign_languages"], [])
        self.assertEqual(result["required_knowledge"], [])
        self.assertEqual(result["education_requirements"], [])

    def test_ai_skill_in_another_clause_not_hidden_by_degree(self):
        result = normalize({"requirements": "Bachelor's degree in Information Technology required; AI experience preferred."})
        self.assertEqual(by_skill(result)["AI"]["requirement_type"], "preferred")
        self.assertEqual(result["education_requirements"][0]["degree"], "bachelor")

    def test_duplicate_language_keeps_source_and_strongest_requirement(self):
        result = normalize({"requirements": "English preferred.\nEnglish required."})
        self.assertEqual(result["foreign_languages"], [{"language": "English", "level_text": "English preferred.\nEnglish required.", "requirement_type": "required"}])

    def test_actual_vietnamese_semicolon_major_alternatives(self):
        text = "Tốt nghiệp Đại học chuyên ngành (hoặc tương đương kinh nghiệm thực tế đã được chứng minh): Công nghệ Thông tin; Khoa học Máy tính hoặc các ngành liên quan;"
        entries = normalize({"requirements": text})["education_requirements"]
        self.assertEqual(entries, [{"degree": "bachelor", "majors": ["Computer Science", "Information Technology"], "requirement_type": "unspecified"}])

    def test_bracket_headings_and_terminal_editorial_preference(self):
        text = "English communication skills sufficient for team collaboration. [Preferred]"
        result = normalize({"requirements": "[Required]\nPython\n" + text + "\nDocker\n[Preferred]\nJava\n[Education]\nBachelor's degree."})
        self.assertEqual(by_skill(result)["Python"]["requirement_type"], "required")
        self.assertEqual(by_skill(result)["Docker"]["requirement_type"], "required")
        self.assertEqual(by_skill(result)["Java"]["requirement_type"], "preferred")
        self.assertEqual(result["foreign_languages"], [{"language": "English", "level_text": text, "requirement_type": "preferred"}])
        self.assertEqual(result["education_requirements"][0]["requirement_type"], "unspecified")

    def test_associate_product_owner_is_not_education(self):
        text = "Have experience as an IT Comtor, Business Analyst, Associate Product Owner, Project Manager, or similar role."
        self.assertEqual(normalize({"requirements": text})["education_requirements"], [])
        self.assertEqual(normalize({"requirements": "Associate degree in Computer Science preferred."})["education_requirements"],
                         [{"degree": "associate", "majors": ["Computer Science"], "requirement_type": "preferred"}])

    def test_vietnamese_pronoun_ai_is_not_artificial_intelligence(self):
        text = "Có khả năng biến nghiệp vụ phức tạp thành state machine rõ ràng: ai làm gì, khi nào, evidence nào bắt buộc..."
        self.assertNotIn("AI", by_skill(normalize({"requirements": text})))
        for explicit in ["AI is mandatory.", "Trí tuệ nhân tạo là lợi thế.", "Artificial intelligence required."]:
            with self.subTest(explicit=explicit):
                self.assertIn("AI", by_skill(normalize({"requirements": explicit})))

    def test_actual_language_team_mix_not_mandatory_proficiency(self):
        english = "Fluent business-level communication skills in English (cross-team collaboration is a dynamic mix of English and Japanese)."
        japanese = "Business-level Japanese language proficiency is highly advantageous."
        result = normalize({"requirements": "Core Requirements (Must Have):\n" + english + "\nPreferred Qualifications (Nice to Have):\n" + japanese})
        languages = {entry["language"]: entry for entry in result["foreign_languages"]}
        self.assertEqual(languages["English"]["requirement_type"], "required")
        self.assertEqual(languages["Japanese"]["requirement_type"], "preferred")
        self.assertEqual(languages["Japanese"]["level_text"], japanese)

    def test_title_required_jlpt_merges_with_unspecified_body(self):
        title = "IT Helpdesk / Technical Support (JLPT N3 Required)"
        body = "Japanese proficiency sufficient to communicate with customers."
        result = normalize({"title": title, "requirements": body})
        self.assertEqual(result["foreign_languages"], [{"language": "Japanese", "level_text": body + "\n" + title, "requirement_type": "required"}])

    def test_actual_devsecops_preference_keeps_tag_and_prose_evidence(self):
        text = "Hiểu biết về DevSecOps, giám sát hệ thống là lợi thế."
        result = normalize({"skills": ["DevSecOps"], "requirements": text})
        self.assertEqual(by_skill(result)["DevSecOps"], {"name": "DevSecOps", "category": "methodology", "requirement_type": "preferred"})

    def test_r_technical_aliases_require_clear_spelling_in_prose(self):
        for text in ["R required.", "rlang required.", "r programming language required."]:
            with self.subTest(text=text):
                self.assertEqual(by_skill(normalize({"requirements": text}))["R"]["category"], "programming_language")
        self.assertNotIn("R", by_skill(normalize({"requirements": "Use the value r in the formula."})))
        self.assertEqual(by_skill(normalize({"skills": ["R", "rlang", "r"]}))["R"]["name"], "R")

    def test_jquery_and_bootstrap_explicit_frameworks(self):
        result = normalize({"requirements": "jQuery required, Bootstrap preferred."})
        skills = by_skill(result)
        self.assertEqual(skills["jQuery"], {"name": "jQuery", "category": "framework", "requirement_type": "required"})
        self.assertEqual(skills["Bootstrap"], {"name": "Bootstrap", "category": "framework", "requirement_type": "preferred"})

    def test_actual_information_security_major_not_lost(self):
        result = normalize({"requirements": "Bachelor's degree in Computer Science, Information Security or Software Engineering required."})
        self.assertEqual(result["education_requirements"], [{"degree": "bachelor", "majors": ["Computer Science", "Information Security", "Software Engineering"], "requirement_type": "required"}])
        vietnamese = normalize({"requirements": "Tốt nghiệp Đại học chuyên ngành An toàn thông tin."})
        self.assertEqual(vietnamese["education_requirements"][0]["majors"], ["Information Security"])



if __name__ == "__main__":
    unittest.main()
