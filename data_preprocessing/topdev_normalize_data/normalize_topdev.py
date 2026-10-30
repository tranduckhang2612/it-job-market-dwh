#!/usr/bin/env python3
"""Làm sạch dữ liệu TopDev, ánh xạ 30 trường chung và lưu vào các bảng staging.

Luồng PostgreSQL: raw.job_postings -> làm sạch/ánh xạ -> staging.job_observations
và các bảng staging con; giữ nguyên JSON thô, thời điểm quan sát và crawl_run_id.
Đọc lại/đối chiếu dữ liệu trước khi mở gate staging.dwh_ready_jobs cho DWH.
"""

from __future__ import annotations

import argparse
import copy
import csv
import getpass
import hashlib
import json
import logging
import math
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_preprocessing.staging_cleaning import CLEANING_VERSION

LOG = logging.getLogger("topdev.staging")

NORMALIZED_FIELDS = (
    "job_id", "source", "source_job_id", "job_url", "crawl_run_id", "observed_at", "published_at",
    "title", "company_name", "description", "job_roles", "seniority_levels", "location_text", "cities", "work_mode",
    "skills", "required_knowledge", "experience_text", "experience_min_years", "experience_max_years",
    "education_requirements", "foreign_languages", "soft_skills", "salary_text", "salary_min", "salary_max",
    "salary_currency", "salary_period", "salary_basis", "salary_status",
)
NORMALIZATION_VERSION = "1.2"
VIETNAM_TZ = timezone(timedelta(hours=7))

_DB_ARRAY_FIELDS = frozenset({"job_roles", "seniority_levels", "cities", "skills",
                              "required_knowledge", "education_requirements",
                              "foreign_languages", "soft_skills"})
DB_SCALAR_FIELDS = tuple(field for field in NORMALIZED_FIELDS if field not in _DB_ARRAY_FIELDS)
STAGING_CHILD_COLUMNS = {
    "job_roles": ("role",),
    "job_seniority_levels": ("seniority_level",),
    "job_cities": ("city",),
    "job_skills": ("name", "category"),
    "job_required_knowledge": ("knowledge",),
    "job_education_requirements": ("degree",),
    "job_foreign_languages": ("language", "level_text"),
    "job_soft_skills": ("soft_skill",),
}
_DB_INSERT_COLUMNS = DB_SCALAR_FIELDS + ("normalization_version",)
_DB_UPSERT_SQL = (
    "INSERT INTO staging.job_observations (" + ", ".join(_DB_INSERT_COLUMNS) + ") "
    "VALUES (" + ", ".join(["%s"] * len(_DB_INSERT_COLUMNS)) + ") "
    "ON CONFLICT (job_id, crawl_run_id) DO UPDATE SET " +
    ", ".join(field + " = EXCLUDED." + field for field in _DB_INSERT_COLUMNS
              if field not in {"job_id", "crawl_run_id"}) +
    ", normalized_at = CURRENT_TIMESTAMP RETURNING observation_id"
)
_DB_METADATA_SQL = """
    UPDATE raw.crawl_runs
    SET metadata = jsonb_set(metadata, '{staging}',
        CASE WHEN jsonb_typeof(metadata->'staging') = 'object'
             THEN metadata->'staging' ELSE '{}'::jsonb END || %s, true)
    WHERE crawl_run_id = %s
"""


class CrawlError(RuntimeError):
    """Dữ liệu đầu vào không đủ điều kiện chuẩn hóa."""


class DatabaseStagingError(RuntimeError):
    """Lỗi ghi vào database staging."""


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def unique(values) -> list:
    return list(dict.fromkeys(v for v in values if v))


def key(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "").lower().replace("đ", "d"))
    return clean("".join(c for c in value if not unicodedata.combining(c))).rstrip(":")


def canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def is_topdev(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and (parsed.hostname or "").endswith("topdev.vn")


class _HTMLToPlainText(HTMLParser):
    blocks = {"p", "div", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
              "ul", "ol", "li", "table", "tr", "section", "article", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden += 1
        elif not self.hidden and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden and data:
            self.parts.append(data)


def html_to_text(html_content: str) -> str:
    if not html_content or not isinstance(html_content, str):
        return ""
    if "<" not in html_content and ">" not in html_content:
        return unescape(html_content.strip())
    parser = _HTMLToPlainText()
    parser.feed(html_content)
    parser.close()
    lines = [clean(line) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalized_datetime(value, *, date_only=False) -> str | None:
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            if not date_only:
                return None
            dt = dt.replace(tzinfo=VIETNAM_TZ)
        return dt.astimezone(VIETNAM_TZ).isoformat()
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        if not date_only:
            return None
        value += "T00:00:00"
    iso_val = value.replace("Z", "+00:00")
    iso_val = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], iso_val)
    try:
        dt = datetime.fromisoformat(iso_val)
    except ValueError:
        return None
    if dt.tzinfo is None:
        if not date_only:
            return None
        dt = dt.replace(tzinfo=VIETNAM_TZ)
    return dt.astimezone(VIETNAM_TZ).isoformat()


def normalized_job_identity(raw: dict) -> tuple[str, str | None, str]:
    if not isinstance(raw, dict):
        raise CrawlError("Bản ghi tin phải là object.")
    source_id = str(raw.get("id") or raw.get("source_job_id") or "").strip() or None
    url = raw.get("detail_url") or raw.get("job_url") or raw.get("url")
    if not url and source_id:
        slug = raw.get("slug") or f"job-{source_id}"
        url = f"https://topdev.vn/detail-jobs/{slug}-{source_id}"
    if not url or not is_topdev(url):
        raise CrawlError("Không có URL TopDev hợp lệ để nhận diện tin.")
    url = canonical(url)
    if source_id:
        job_id = f"topdev:{source_id}"
    else:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        job_id = f"topdev:url_{h}"
    return job_id, source_id, url


def normalized_description(raw: dict) -> str:
    parts = []
    # Thử lấy các khối chi tiết có cấu trúc
    responsibilities = raw.get("responsibilities_original") or raw.get("responsibilities")
    if isinstance(responsibilities, str) and responsibilities.strip():
        txt = html_to_text(responsibilities)
        if txt:
            parts.append("Mô tả công việc:\n" + txt)

    requirements = raw.get("requirements_original") or raw.get("requirements")
    if isinstance(requirements, str) and requirements.strip():
        txt = html_to_text(requirements)
        if txt:
            parts.append("Yêu cầu công việc:\n" + txt)

    benefits = raw.get("benefits_original") or raw.get("benefits")
    if isinstance(benefits, list):
        ben_texts = []
        for b in benefits:
            if isinstance(b, dict) and b.get("value"):
                ben_texts.append(html_to_text(b["value"]))
            elif isinstance(b, str) and b.strip():
                ben_texts.append(html_to_text(b))
        if ben_texts:
            parts.append("Quyền lợi được hưởng:\n" + "\n".join(ben_texts))
    elif isinstance(benefits, str) and benefits.strip():
        txt = html_to_text(benefits)
        if txt:
            parts.append("Quyền lợi được hưởng:\n" + txt)

    if not parts:
        generic = raw.get("description") or raw.get("descriptions")
        if isinstance(generic, str) and generic.strip():
            parts.append(html_to_text(generic))

    full_desc = "\n\n".join(parts).strip()
    if not full_desc:
        raise CrawlError("Không có nội dung mô tả để chuẩn hóa tin TopDev.")
    return full_desc


def normalize_job_roles(raw: dict) -> list[str]:
    title = str(raw.get("title") or "").strip()
    norm = key(title)
    roles = []
    mapping = [
        ("Backend Developer", r"\b(?:backend|back-end|back\s+end)\b"),
        ("Frontend Developer", r"\b(?:frontend|front-end|front\s+end)\b"),
        ("Fullstack Developer", r"\b(?:fullstack|full-stack|full\s+stack)\b"),
        ("Mobile Developer", r"\b(?:mobile|android|ios|flutter|react\s+native)\b"),
        ("DevOps Engineer", r"\b(?:devops|sre|site\s+reliability|cloud\s+engineer|infrastructure)\b"),
        ("QA/QC Engineer", r"\b(?:qa|qc|tester|test\s+engineer|kiem\s+thu|quality\s+assurance)\b"),
        ("Data Engineer", r"\b(?:data\s+engineer|ky\s+su\s+du\s+lieu)\b"),
        ("Data Analyst", r"\b(?:data\s+analyst|chuyen\s+vien\s+du\s+lieu|bi\s+analyst)\b"),
        ("Data Scientist", r"\b(?:data\s+scientist|khoa\s+hoc\s+du\s+lieu)\b"),
        ("Machine Learning / AI Engineer", r"\b(?:machine\s+learning|ai|tri\s+tue\s+nhan\s+tao|nlp|computer\s+vision)\b"),
        ("System Engineer", r"\b(?:system\s+engineer|quan\s+tri\s+he\s+thong|system\s+admin)\b"),
        ("Network Engineer", r"\b(?:network\s+engineer|quan\s+tri\s+mang)\b"),
        ("Security Engineer", r"\b(?:security|an\s+ninh\s+mang|an\s+toan\s+thong\s+tin|pentest)\b"),
        ("Software Engineer", r"\b(?:software\s+engineer|lap\s+trinh\s+vien|software\s+developer)\b"),
        ("Product Manager", r"\b(?:product\s+manager|quan\s+ly\s+san\s+pham)\b"),
        ("Product Owner", r"\b(?:product\s+owner)\b"),
        ("Project Manager", r"\b(?:project\s+manager|quan\s+ly\s+du\s+an)\b"),
        ("Business Analyst", r"\b(?:business\s+analyst|ba|chuyen\s+vien\s+phan\s+tich\s+nghiep\s+vu)\b"),
        ("UI/UX Designer", r"\b(?:ui/ux|ui\s+designer|ux\s+designer|thiet\s+ke\s+giao\s+dien)\b"),
        ("Scrum Master", r"\b(?:scrum\s+master)\b"),
        ("Solutions Architect", r"\b(?:solution[s]?\s+architect|kien\s+truc\s+su)\b"),
        ("Technical Lead", r"\b(?:tech(?:nical)?\s+lead|truong\s+nhom\s+ky\s+thuat)\b"),
    ]
    for role_name, pattern in mapping:
        if re.search(pattern, norm):
            roles.append(role_name)
    if not roles:
        roles.append("IT Specialist")
    return unique(roles)


def normalize_seniority(raw: dict) -> list[str]:
    title = str(raw.get("title") or "")
    norm_title = key(title)
    levels = []

    patterns = [
        ("Internship", r"\bintern(?:ship)?\b|\bthuc\s+tap\b"),
        ("Fresher", r"\bfresher\b"),
        ("Junior", r"\bjunior\b|\bjr\b"),
        ("Middle", r"\bmiddle\b|\bmid(?:\s*[- ]\s*level)?\b"),
        ("Senior", r"\bsenior\b|\bsr\b"),
        ("Lead", r"\blead(?:er)?\b"),
        ("Manager", r"\bmanager\b|\bmanagement\b|\btruong\s+phong\b"),
        ("Director", r"\bdirector\b|\bgiam\s+doc\b"),
    ]
    for name, pattern in patterns:
        if re.search(pattern, norm_title):
            levels.append(name)

    raw_levels = raw.get("job_levels_arr") or []
    if isinstance(raw_levels, list):
        for lv in raw_levels:
            lv_k = key(str(lv))
            if "thuc tap" in lv_k or "intern" in lv_k:
                levels.append("Internship")
            elif "fresher" in lv_k:
                levels.append("Fresher")
            elif "junior" in lv_k:
                levels.append("Junior")
            elif "middle" in lv_k or "nhan vien" in lv_k:
                levels.append("Middle")
            elif "senior" in lv_k:
                levels.append("Senior")
            elif "truong nhom" in lv_k or "lead" in lv_k:
                levels.append("Lead")
            elif "truong phong" in lv_k or "manager" in lv_k:
                levels.append("Manager")
            elif "giam doc" in lv_k or "director" in lv_k:
                levels.append("Director")

    return unique(levels)


def normalize_locations(raw: dict) -> tuple[str | None, list[str]]:
    addresses = raw.get("addresses") or {}
    cities = []
    location_text = None

    if isinstance(addresses, dict):
        full_list = addresses.get("full_addresses") or []
        if isinstance(full_list, list) and full_list:
            location_text = "\n".join(dict.fromkeys(clean(v) for v in full_list if isinstance(v, str) and clean(v)))

        region_list = addresses.get("address_region_array") or []
        if isinstance(region_list, list):
            for r in region_list:
                cleaned_r = clean(r)
                if cleaned_r:
                    cities.append(cleaned_r)

    if not location_text and isinstance(raw.get("jobLocation"), dict):
        addr = raw["jobLocation"].get("address") or {}
        if isinstance(addr, dict):
            location_text = clean(addr.get("streetAddress") or addr.get("addressLocality"))
            if addr.get("addressRegion"):
                cities.append(clean(addr["addressRegion"]))

    # Chuẩn hoá tên thành phố
    std_cities = []
    for c in cities:
        c_k = key(c)
        if "ha noi" in c_k:
            std_cities.append("Hà Nội")
        elif "ho chi minh" in c_k or "hcm" in c_k or "sai gon" in c_k:
            std_cities.append("Hồ Chí Minh")
        elif "da nang" in c_k:
            std_cities.append("Đà Nẵng")
        elif "hai phong" in c_k:
            std_cities.append("Hải Phòng")
        elif "can tho" in c_k:
            std_cities.append("Cần Thơ")
        elif "binh duong" in c_k:
            std_cities.append("Bình Dương")
        elif "dong nai" in c_k:
            std_cities.append("Đồng Nai")
        else:
            std_cities.append(c)

    return location_text or None, unique(std_cities)


def normalize_work_mode(raw: dict) -> str | None:
    types_str = str(raw.get("job_types_str") or "").lower()
    if "tu xa" in key(types_str) or "remote" in types_str:
        return "remote"
    if "linh hoat" in key(types_str) or "hybrid" in types_str:
        return "hybrid"
    if "truc tiep" in key(types_str) or "onsite" in types_str:
        return "onsite"

    # Tìm trong mô tả
    desc = key(str(raw.get("description") or ""))
    if re.search(r"\b(?:remote|lam viec tu xa|wfh)\b", desc):
        return "remote"
    if re.search(r"\b(?:hybrid|linh hoat)\b", desc):
        return "hybrid"
    if re.search(r"\b(?:onsite|tai van phong)\b", desc):
        return "onsite"
    return None


def _section_text(value):
    if isinstance(value, list):
        return "\n".join(_section_text(item) for item in value)
    if isinstance(value, dict):
        return _section_text(value.get("value") or "")
    return html_to_text(str(value or ""))


def normalize_salary(raw: dict) -> dict:
    # Reuse the tested locale-aware monetary parser; never strip punctuation
    # from numbers or infer MONTH/VND when those units are absent.
    from data_preprocessing.itviec_normalize_data import normalize_itviec as common
    obj = raw.get("salary") or {}
    obj = obj if isinstance(obj, dict) else {"value": str(obj)}
    value = clean(obj.get("value")) or None
    visible = value if value and "*" not in value else None
    currency = clean(obj.get("currency")) or None
    unit = clean(obj.get("unit")) or None
    low = common._se_numeric(obj.get("min_filter")) or common._se_numeric(obj.get("min"))
    high = common._se_numeric(obj.get("max_filter")) or common._se_numeric(obj.get("max"))
    negotiable = str(obj.get("is_negotiable", "0")).lower() in {"1", "true"}
    structured = {"currency": currency, "value": {"unitText": unit}}
    if not negotiable:
        structured["value"].update(minValue=low, maxValue=high)
    result = common.normalize_salary({"salary": {"text": visible, "structured": structured}})
    if result["salary_status"] == "disclosed":
        if result["salary_text"] is None:
            if result["salary_min"] is not None and result["salary_max"] is not None:
                result["salary_text"] = f"{result['salary_min']:,.0f} - {result['salary_max']:,.0f} {currency or ''}".strip()
            else:
                bound = result["salary_min"] or result["salary_max"]
                label = "From" if result["salary_min"] is not None else "Up to"
                result["salary_text"] = f"{label} {bound:,.0f} {currency or ''}".strip()
        return result

    # Only labelled compensation sentences qualify. Meal allowance, budgets,
    # 13th-month bonuses and leave days must not turn into salary bounds.
    candidates = []
    generic = []
    for field in ("benefits_original", "description", "benefits"):
        text = _section_text(raw.get(field))
        for line in re.split(r"\n|(?<=[.!?;])\s+(?=[A-ZĐ])", text):
            if not re.search(r"lương|thu nhập|salary|compensation|trợ cấp thực tập|stipend", line, re.I):
                continue
            if re.search(r"phụ cấp|allowance|bonus|thưởng|learning budget|bảo hiểm|insurance", line, re.I):
                continue
            parsed = common.normalize_salary({"salary": {"text": line, "structured": structured}})
            # API numeric filters are not evidence for a narrative fallback.
            if parsed["salary_status"] == "disclosed" and common._se_salary_bounds(line) != (None, None):
                candidates.append(parsed)
            elif parsed["salary_status"] in {"negotiable", "not_disclosed"}:
                generic.append(parsed)
    signatures = {(p["salary_min"], p["salary_max"], p["salary_currency"], p["salary_period"]) for p in candidates}
    if len(signatures) == 1:
        return candidates[0]
    if len(signatures) > 1:
        return {"salary_text": " | ".join(p["salary_text"] for p in candidates),
                "salary_min": None, "salary_max": None, "salary_currency": None,
                "salary_period": None, "salary_basis": None, "salary_status": "unknown"}
    if negotiable:
        return common.normalize_salary({"salary": {"text": visible or "Thỏa thuận"}})
    if generic:
        return generic[0]
    if result["salary_text"] is None:
        result["salary_status"] = "not_disclosed"
    return result


def normalize_experience(raw: dict) -> dict:
    from data_preprocessing.itviec_normalize_data import normalize_itviec as common
    # Never scan benefits/company history. Description is used only for older
    # input shapes without a requirements section.
    requirements = _section_text(raw.get("requirements_original") or raw.get("requirements") or raw.get("description"))
    result = common.normalize_experience({"your_skills_and_experience": requirements})
    structured = raw.get("experienceRequirements")
    if isinstance(structured, dict) and result["experience_text"] is None:
        months = common._se_numeric(structured.get("monthsOfExperience"), allow_zero=True)
        if months is not None:
            result["experience_min_years"] = round(months / 12, 6)
            result["experience_text"] = f"{months} tháng kinh nghiệm"
    return result


SKILL_CATEGORIES = {
    "programming_languages": {
        "python", "java", "c++", "c#", "c", "javascript", "typescript", "golang", "go",
        "php", "ruby", "rust", "kotlin", "swift", "dart", "scala", "r", "solidity"
    },
    "frameworks_libraries": {
        "react", "reactjs", "react.js", "vue", "vuejs", "vue.js", "angular", "nextjs", "next.js",
        "django", "flask", "fastapi", "spring", "spring boot", ".net", ".net core", "asp.net",
        "nodejs", "node.js", "express", "nest", "nestjs", "laravel", "rails", "flutter"
    },
    "databases": {
        "postgresql", "postgres", "mysql", "mongodb", "redis", "oracle", "sql server",
        "mariadb", "elasticsearch", "cassandra", "dynamodb", "sqlite"
    },
    "cloud_devops": {
        "aws", "azure", "gcp", "docker", "kubernetes", "k8s", "ci/cd", "jenkins",
        "terraform", "ansible", "linux", "git", "gitlab", "github actions"
    },
    "concepts": {
        "oop", "restful api", "rest api", "microservices", "agile", "scrum", "mvc",
        "design patterns", "data structures", "clean architecture"
    }
}


def categorize_skill(skill_name: str) -> str:
    k = key(skill_name)
    for cat, items in SKILL_CATEGORIES.items():
        if k in items:
            return cat
    return "technical"


def normalize_requirements(raw: dict) -> dict:
    skills = []
    seen_skills = set()

    # 1. Thu thập skills_arr & extra_skills
    for s in (raw.get("skills_arr") or []):
        s_clean = clean(s)
        if s_clean and key(s_clean) not in seen_skills:
            seen_skills.add(key(s_clean))
            skills.append({
                "name": s_clean,
                "category": categorize_skill(s_clean),
                "requirement_type": "required"
            })

    for s in (raw.get("extra_skills") or []):
        s_clean = clean(s)
        if s_clean and key(s_clean) not in seen_skills:
            seen_skills.add(key(s_clean))
            skills.append({
                "name": s_clean,
                "category": categorize_skill(s_clean),
                "requirement_type": "preferred"
            })

    # Add explicit requirement-section evidence missing from listing tags.
    # The shared parser handles local negation and required/preferred clauses.
    from data_preprocessing.itviec_normalize_data import normalize_itviec as common
    from data_preprocessing.staging_cleaning import _SKILLS
    inferred = common.normalize_requirements({"your_skills_and_experience": _section_text(
        raw.get("requirements_original") or raw.get("requirements"))})
    canonical_names = {_SKILLS.get(s["name"].casefold(), (s["name"], ""))[0].casefold() for s in skills}
    for skill in inferred["skills"]:
        if skill["requirement_type"] == "unspecified":
            occurrences = [(line, start, end) for line, _ in common.req_lines({"your_skills_and_experience": _section_text(
                raw.get("requirements_original") or raw.get("requirements"))})
                for start, end, name, _ in common.req_skill_matches(line) if name == skill["name"]]
            if occurrences and all(common.req_item_is_negated(line, start, end) for line, start, end in occurrences):
                continue
        name = _SKILLS.get(skill["name"].casefold(), (skill["name"], ""))[0].casefold()
        if name not in canonical_names:
            canonical_names.add(name)
            skills.append(skill)

    # 2. Knowledge
    knowledge = []
    desc = key(str(raw.get("description") or "") + " " + str(raw.get("requirements_original") or ""))
    concepts = [
        ("RESTful API", r"\b(?:restful\s+api|rest\s+api)\b"),
        ("Microservices", r"\bmicroservices?\b"),
        ("OOP", r"\boop\b|\blap\s+trinh\s+huong\s+doi\s+tuong\b"),
        ("CI/CD", r"\bci/cd\b"),
        ("Agile/Scrum", r"\bagile\b|\bscrum\b"),
        ("Design Patterns", r"\bdesign\s+patterns?\b"),
        ("Data Structures & Algorithms", r"\bcau\s+truc\s+du\s+lieu\b|\bdata\s+structures?\b"),
    ]
    for kn_name, kn_pat in concepts:
        if re.search(kn_pat, desc):
            knowledge.append(kn_name)

    # 3. Ngoại ngữ
    foreign_languages = []
    if re.search(r"\b(?:tieng\s+anh|english)\b", desc):
        level = None
        toeic = re.search(r"toeic\s*(\d+)", desc)
        ielts = re.search(r"ielts\s*(\d+(?:\.\d+)?)", desc)
        if toeic:
            level = f"TOEIC {toeic.group(1)}"
        elif ielts:
            level = f"IELTS {ielts.group(1)}"
        elif "giao tiep" in desc:
            level = "Giao tiếp tốt"
        foreign_languages.append({
            "language": "English",
            "level_text": level,
            "requirement_type": "required" if "bat buoc" in desc else "unspecified"
        })
    if re.search(r"\b(?:tieng\s+nhat|japanese)\b", desc):
        level = None
        jlpt = re.search(r"\b(n[1-5])\b", desc)
        if jlpt:
            level = jlpt.group(1).upper()
        foreign_languages.append({
            "language": "Japanese",
            "level_text": level,
            "requirement_type": "preferred"
        })

    for language in inferred["foreign_languages"]:
        if not any(f["language"].casefold() == language["language"].casefold() for f in foreign_languages):
            foreign_languages.append(language)

    # 4. Kỹ năng mềm
    soft_skills = []
    soft_patterns = [
        ("Giao tiếp", r"\b(?:giao\s+tiep|communication)\b"),
        ("Làm việc nhóm", r"\b(?:lam\s+viec\s+nhom|teamwork)\b"),
        ("Giải quyết vấn đề", r"\b(?:giai\s+quyet\s+van\s+de|problem\s+solving)\b"),
        ("Tư duy phản biện", r"\b(?:critical\s+thinking|tu\s+duy\s+phan\s+bien)\b"),
        ("Quản lý thời gian", r"\b(?:quan\s+ly\s+thoi\s+gian|time\s+management)\b"),
    ]
    for ss_name, ss_pat in soft_patterns:
        if re.search(ss_pat, desc):
            soft_skills.append(ss_name)

    # 5. Học vấn
    education_requirements = []
    majors = []
    if re.search(r"\b(?:cntt|cong\s+nghe\s+thong\s+tin|computer\s+science|khoa\s+hoc\s+may\s+tinh)\b", desc):
        majors.append("Công nghệ thông tin")
    degree = None
    if re.search(r"\b(?:dai\s+hoc|bachelor|cu\s+nhan|ky\s+su)\b", desc):
        degree = "Đại học / Cử nhân"
    elif re.search(r"\b(?:cao\s+dang|college)\b", desc):
        degree = "Cao đẳng"
    if degree or majors:
        education_requirements.append({
            "degree": degree,
            "majors": unique(majors),
            "requirement_type": "required" if "yeu cau" in desc else "unspecified"
        })

    return {
        "skills": skills,
        "required_knowledge": unique(knowledge),
        "foreign_languages": foreign_languages,
        "soft_skills": unique(soft_skills),
        "education_requirements": education_requirements,
    }


def normalize_job(raw: dict, crawl_run_id: str, observed_at: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise CrawlError("Bản ghi tin phải là object.")
    if not isinstance(crawl_run_id, str) or not crawl_run_id.strip():
        raise CrawlError("Thiếu crawl_run_id.")
    job_id, source_id, url = normalized_job_identity(raw)
    observed = normalized_datetime(observed_at or raw.get("observed_at") or raw.get("crawled_at"))
    if observed is None:
        raise CrawlError("Thiếu thời điểm thu thập có múi giờ.")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CrawlError("Tin thiếu title.")

    if set(raw) == set(NORMALIZED_FIELDS):
        record = copy.deepcopy(raw)
        record.update(job_id=job_id, source_job_id=source_id, job_url=url,
                      crawl_run_id=crawl_run_id, observed_at=observed)
    else:
        location_text, cities = normalize_locations(raw)
        published = raw.get("published_at") or raw.get("datePosted") or raw.get("created_at")
        record = {
            "job_id": job_id,
            "source": "topdev",
            "source_job_id": source_id,
            "job_url": url,
            "crawl_run_id": crawl_run_id,
            "observed_at": observed,
            "published_at": normalized_datetime(published, date_only=True) if published else None,
            "title": clean(title),
            "company_name": clean(raw.get("company", {}).get("display_name") if isinstance(raw.get("company"), dict) else raw.get("company_name")) or None,
            "description": normalized_description(raw),
            "job_roles": normalize_job_roles(raw),
            "seniority_levels": normalize_seniority(raw),
            "location_text": location_text,
            "cities": cities,
            "work_mode": normalize_work_mode(raw),
            **normalize_requirements(raw),
            **normalize_experience(raw),
            **normalize_salary(raw),
        }

    record = {field: record[field] for field in NORMALIZED_FIELDS}
    from data_preprocessing.staging_cleaning import clean_record
    record = clean_record(record)
    validate_normalized_job(record)
    return record


def validate_normalized_job(job: dict):
    if not isinstance(job, dict) or tuple(job) != NORMALIZED_FIELDS:
        raise CrawlError("Bản ghi không đúng thứ tự/danh sách 30 trường chuẩn hóa.")
    for field in ("job_id", "source", "job_url", "crawl_run_id", "observed_at", "title", "description", "salary_status"):
        if not isinstance(job[field], str) or not job[field].strip():
            raise CrawlError(f"Trường bắt buộc phải là string có nội dung: {field}")
    for field in ("source_job_id", "published_at", "company_name", "location_text", "work_mode", "experience_text",
                  "salary_text", "salary_currency", "salary_period", "salary_basis"):
        if job[field] is not None and (not isinstance(job[field], str) or not job[field].strip()):
            raise CrawlError(f"Trường phải là string hoặc null: {field}")
    for field in ("job_roles", "seniority_levels", "cities", "required_knowledge", "soft_skills"):
        if not isinstance(job[field], list) or not all(isinstance(v, str) and v.strip() for v in job[field]):
            raise CrawlError(f"Trường phải là mảng string: {field}")
        if len(job[field]) != len({key(v) for v in job[field]}):
            raise CrawlError(f"Danh sách chứa giá trị trùng: {field}")
    for field, names in (("skills", {"name", "category"}),
                         ("education_requirements", {"degree", "majors"}),
                         ("foreign_languages", {"language", "level_text"})):
        if not isinstance(job[field], list):
            raise CrawlError(f"Trường phải là mảng object: {field}")
        for item in job[field]:
            if not isinstance(item, dict) or set(item) != names:
                raise CrawlError(f"Object không đúng schema: {field}")
            if field == "skills" and not all(isinstance(item[n], str) and item[n].strip() for n in ("name", "category")):
                raise CrawlError("Kỹ năng thiếu tên hoặc nhóm.")
            if field == "education_requirements":
                if item["degree"] is not None and (not isinstance(item["degree"], str) or not item["degree"].strip()):
                    raise CrawlError("degree phải là string hoặc null.")
                if not isinstance(item["majors"], list) or not all(isinstance(v, str) and v.strip() for v in item["majors"]):
                    raise CrawlError("majors phải là mảng string.")
                if len(item["majors"]) != len({key(v) for v in item["majors"]}):
                    raise CrawlError("majors chứa giá trị trùng.")
            if field == "foreign_languages" and (not isinstance(item["language"], str) or not item["language"].strip() or
                    (item["level_text"] is not None and (not isinstance(item["level_text"], str) or not item["level_text"].strip()))):
                raise CrawlError("Ngoại ngữ không đúng schema.")

    for field in ("experience_min_years", "experience_max_years", "salary_min", "salary_max"):
        val = job[field]
        if val is not None and (not isinstance(val, (int, float)) or isinstance(val, bool) or not math.isfinite(val) or val < 0):
            raise CrawlError(f"Trường phải là số không âm hoặc null: {field}")
    for minimum, maximum in (("experience_min_years", "experience_max_years"), ("salary_min", "salary_max")):
        if job[minimum] is not None and job[maximum] is not None and job[minimum] > job[maximum]:
            raise CrawlError(f"Cận dưới lớn hơn cận trên: {minimum}")

    if job["work_mode"] not in {None, "onsite", "hybrid", "remote"}:
        raise CrawlError("work_mode không thuộc tập giá trị quy định.")
    if job["salary_status"] not in {"disclosed", "negotiable", "not_disclosed", "unknown"}:
        raise CrawlError("salary_status không hợp lệ.")
    if job["salary_basis"] not in {None, "gross", "net"}:
        raise CrawlError("salary_basis không hợp lệ.")
    if job["source"] != "topdev":
        raise CrawlError("Nguồn phải là topdev.")
    if not job["job_id"].startswith("topdev:") or len(job["job_id"]) <= len("topdev:"):
        raise CrawlError("job_id không đúng định dạng topdev:<id>.")


def _database_connection(args):
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError:
        raise DatabaseStagingError('Thiếu thư viện PostgreSQL. Chạy: python -m pip install "psycopg[binary]>=3.1,<4"') from None
    options = {
        "connect_timeout": getattr(args, "db_connect_timeout", 10),
        "application_name": "topdev_staging",
        "autocommit": True
    }
    for field, parameter in (("db_host", "host"), ("db_port", "port"),
                             ("db_name", "dbname"), ("db_user", "user")):
        value = getattr(args, field, None)
        if value is not None:
            options[parameter] = value
    password = getattr(args, "db_password", None)
    if password is not None:
        options["password"] = password
    elif getattr(args, "db_password_prompt", False):
        try:
            options["password"] = getpass.getpass("Mật khẩu PostgreSQL: ")
        except EOFError:
            raise DatabaseStagingError("Không nhập được mật khẩu PostgreSQL.") from None
    try:
        return psycopg.connect(getattr(args, "db_dsn", "") or "", **options)
    except (psycopg.Error, ValueError, TypeError) as exc:
        raise DatabaseStagingError(f"Không kết nối được PostgreSQL: {exc}") from None


def stage_database_run(crawl_run_id: str, args) -> int:
    """Làm sạch/ánh xạ đúng một batch raw của TopDev vào staging."""
    conn = None
    try:
        conn = _database_connection(args)
    except DatabaseStagingError as exc:
        LOG.error("%s", exc)
        return 4

    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError:
        LOG.error('Thiếu thư viện psycopg. Chạy: python -m pip install "psycopg[binary]>=3.1,<4"')
        return 4

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT job_id, raw_payload, observed_at
                FROM raw.job_postings
                WHERE crawl_run_id = %s
                ORDER BY job_id
            """, (crawl_run_id,))
            raw_rows = cur.fetchall()

        if not raw_rows:
            LOG.error("Không tìm thấy tin raw nào cho crawl_run_id=%s.", crawl_run_id)
            return 1

        normalized_jobs = {}
        errors = []

        for row_job_id, payload, observed_at in raw_rows:
            try:
                obs_str = observed_at.isoformat() if isinstance(observed_at, datetime) else observed_at
                job = normalize_job(payload, crawl_run_id, observed_at=obs_str)
                normalized_jobs[job["job_id"]] = job
            except CrawlError as exc:
                errors.append({"job_id": row_job_id, "stage": "normalize", "error": str(exc)})

        # Insert / Update vào staging
        with conn.transaction():
            for job in normalized_jobs.values():
                with conn.cursor() as cur:
                    scalar_values = [job[col] for col in DB_SCALAR_FIELDS] + [NORMALIZATION_VERSION]
                    cur.execute(_DB_UPSERT_SQL, scalar_values)
                    obs_id = cur.fetchone()[0]

                    # Xóa mảng cũ
                    for tbl in STAGING_CHILD_COLUMNS:
                        cur.execute(f"DELETE FROM staging.{tbl} WHERE observation_id = %s", (obs_id,))
                    cur.execute("DELETE FROM staging.job_education_majors WHERE observation_id = %s", (obs_id,))

                    # Thêm mảng mới
                    for i, role in enumerate(job["job_roles"], 1):
                        cur.execute("INSERT INTO staging.job_roles (observation_id, item_order, role) VALUES (%s, %s, %s)",
                                    (obs_id, i, role))
                    for i, lv in enumerate(job["seniority_levels"], 1):
                        cur.execute("INSERT INTO staging.job_seniority_levels (observation_id, item_order, seniority_level) VALUES (%s, %s, %s)",
                                    (obs_id, i, lv))
                    for i, city in enumerate(job["cities"], 1):
                        cur.execute("INSERT INTO staging.job_cities (observation_id, item_order, city) VALUES (%s, %s, %s)",
                                    (obs_id, i, city))
                    for i, sk in enumerate(job["skills"], 1):
                        cur.execute("INSERT INTO staging.job_skills (observation_id, item_order, name, category) VALUES (%s, %s, %s, %s)",
                                    (obs_id, i, sk["name"], sk["category"]))
                    for i, kn in enumerate(job["required_knowledge"], 1):
                        cur.execute("INSERT INTO staging.job_required_knowledge (observation_id, item_order, knowledge) VALUES (%s, %s, %s)",
                                    (obs_id, i, kn))
                    for i, fl in enumerate(job["foreign_languages"], 1):
                        cur.execute("INSERT INTO staging.job_foreign_languages (observation_id, item_order, language, level_text) VALUES (%s, %s, %s, %s)",
                                    (obs_id, i, fl["language"], fl.get("level_text")))
                    for i, ss in enumerate(job["soft_skills"], 1):
                        cur.execute("INSERT INTO staging.job_soft_skills (observation_id, item_order, soft_skill) VALUES (%s, %s, %s)",
                                    (obs_id, i, ss))
                    for i, edu in enumerate(job["education_requirements"], 1):
                        cur.execute("INSERT INTO staging.job_education_requirements (observation_id, item_order, degree) VALUES (%s, %s, %s)",
                                    (obs_id, i, edu.get("degree")))
                        for j, maj in enumerate(edu.get("majors", []), 1):
                            cur.execute("INSERT INTO staging.job_education_majors (observation_id, education_order, item_order, major) VALUES (%s, %s, %s, %s)",
                                        (obs_id, i, j, maj))

        # Đọc lại và kiểm định chất lượng đối chiếu (Read-back validation)
        validated_count = 0
        read_errors = []
        with conn.cursor() as cur:
            cur.execute("""
                SELECT to_jsonb(v), o.normalization_version, r.source_job_id, r.job_url, r.observed_at
                FROM staging.jobs v
                JOIN staging.job_observations o USING (job_id, crawl_run_id)
                JOIN raw.job_postings r USING (job_id, crawl_run_id)
                WHERE v.crawl_run_id = %s ORDER BY v.job_id
            """, (crawl_run_id,))
            rows = cur.fetchall()

        actual_ids = set()
        for payload, ver, src_id, url, obs_at in rows:
            jid = payload.get("job_id") if isinstance(payload, dict) else None
            try:
                if not isinstance(payload, dict) or set(payload) != set(NORMALIZED_FIELDS):
                    raise CrawlError("View staging không đủ 30 trường.")
                job = {f: payload[f] for f in NORMALIZED_FIELDS}
                validate_normalized_job(job)
                actual_ids.add(jid)
                raw_time = obs_at.isoformat() if isinstance(obs_at, datetime) else obs_at
                if (ver != NORMALIZATION_VERSION or job["source_job_id"] != src_id or
                        job["job_url"] != url or job["crawl_run_id"] != crawl_run_id or
                        normalized_datetime(job["observed_at"]) != normalized_datetime(raw_time)):
                    raise CrawlError("Staging không khớp thông tin nhận diện/thời gian trong raw.")
                validated_count += 1
            except Exception as exc:
                read_errors.append({"job_id": jid, "stage": "staging_validation", "error": str(exc)})

        staging_status = "succeeded" if not errors and not read_errors else "failed"
        staging_meta = {
            "status": staging_status,
            "version": NORMALIZATION_VERSION,
            "cleaning_version": CLEANING_VERSION,
            "ready_for_dwh": staging_status == "succeeded",
            "raw_count": len(raw_rows),
            "normalized_count": len(normalized_jobs),
            "quality": {
                "validated_count": validated_count,
                "error_count": len(errors) + len(read_errors),
                "errors": errors + read_errors,
            }
        }
        with conn.cursor() as cur:
            cur.execute(_DB_METADATA_SQL, (Jsonb(staging_meta), crawl_run_id))

        LOG.info("Staging run %s: đã chuẩn hóa %s/%s tin, xác thực %s tin, sẵn sàng DWH: %s",
                 crawl_run_id, len(normalized_jobs), len(raw_rows), validated_count, staging_meta["ready_for_dwh"])
        return 0 if staging_meta["ready_for_dwh"] else (2 if normalized_jobs else 1)

    except Exception as exc:
        LOG.error("Lỗi staging database: %s", exc)
        return 4
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument("input_file", nargs="?", type=Path, help="File JSON raw để chuẩn hóa offline")
    parser.add_argument("--output", type=Path, help="File JSON xuất kết quả chuẩn hóa")
    parser.add_argument("--crawl-run-id", required=False, help="crawl_run_id cần xử lý từ raw vào staging")
    parser.add_argument("--db-host", help="PostgreSQL host")
    parser.add_argument("--db-port", type=int, help="PostgreSQL port")
    parser.add_argument("--db-name", help="PostgreSQL database name")
    parser.add_argument("--db-user", help="PostgreSQL user")
    parser.add_argument("--db-password", help="PostgreSQL password")
    parser.add_argument("--db-password-prompt", action="store_true", help="Nhập mật khẩu DB từ terminal")
    parser.add_argument("--db-dsn", help="PostgreSQL connection string (DSN)")
    parser.add_argument("--db-connect-timeout", type=int, default=10, help="DB connect timeout")
    return parser


def parse_args(argv=None, parser=None) -> argparse.Namespace:
    if parser is None:
        parser = build_parser()
    return parser.parse_args(argv)


def normalize_file(input_path: Path, output_path: Path | None, crawl_run_id: str | None = None) -> int:
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        LOG.error("Không đọc được file input %s: %s", input_path, exc)
        return 1

    jobs_raw = data.get("jobs", data) if isinstance(data, dict) else data
    if not isinstance(jobs_raw, list):
        LOG.error("File JSON không chứa danh sách job hợp lệ.")
        return 1

    run_id = crawl_run_id or (data.get("metadata", {}).get("crawl_run_id") if isinstance(data, dict) else None) or "offline_file_run"
    normalized_list = []
    errors = []
    for item in jobs_raw:
        try:
            norm = normalize_job(item, run_id)
            normalized_list.append(norm)
        except Exception as exc:
            job_id = item.get("id") or item.get("job_id") or "unknown"
            errors.append({"job_id": job_id, "error": str(exc)})

    LOG.info("Đã chuẩn hóa thành công %s/%s tin (lỗi: %s).", len(normalized_list), len(jobs_raw), len(errors))
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"crawl_run_id": run_id, "jobs": normalized_list, "errors": errors}, f, ensure_ascii=False, indent=2)
        LOG.info("Đã lưu dữ liệu chuẩn hóa vào: %s", output_path)
    return 0 if normalized_list else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if getattr(args, "input_file", None):
        return normalize_file(args.input_file, args.output, args.crawl_run_id)
    if not getattr(args, "crawl_run_id", None):
        LOG.error("Cần truyền --crawl-run-id để chạy với database, hoặc truyền đường dẫn file JSON để chuẩn hóa offline.")
        return 2
    return stage_database_run(args.crawl_run_id, args)


if __name__ == "__main__":
    sys.exit(main())
