"""Shared, loss-aware finishing rules for the 30-field staging contract.

Schema version stays 1.1; CLEANING_VERSION tracks these semantic rules separately.
Raw text, IDs, URLs and observation timestamps are never rewritten here.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from html import unescape

CLEANING_VERSION = "2026.10.29.1"
_EMPTY = {"n/a", "n.a.", "null", "none", "undefined", "-", "--"}
_INVISIBLE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def clean_text(value, *, multiline=False):
    if value is None:
        return None
    text = str(value)
    while unescape(text) != text:
        text = unescape(text)
    text = unicodedata.normalize("NFC", text)
    text = _INVISIBLE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(c for c in text if not unicodedata.category(c).startswith("C") or c in "\n\t")
    if multiline:
        lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    else:
        text = re.sub(r"\s+", " ", text).strip()
    return None if not text or text.casefold() in _EMPTY else text


def fold(value):
    value = (clean_text(value) or "").casefold().replace("đ", "d")
    return "".join(c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c))


def canonical_city(value):
    text = clean_text(value)
    if not text:
        return None
    text = re.sub(r"^(?:thành phố|thanh pho|tỉnh|tinh|tp\.?|city of)\s*", "", text, flags=re.I).strip(" .,:")
    aliases = {"hanoi": "Hà Nội", "ha noi": "Hà Nội", "hcm": "Hồ Chí Minh",
               "hcmc": "Hồ Chí Minh", "ho chi minh city": "Hồ Chí Minh", "ho chi minh": "Hồ Chí Minh",
               "sai gon": "Hồ Chí Minh", "saigon": "Hồ Chí Minh", "da nang": "Đà Nẵng", "danang": "Đà Nẵng"}
    return aliases.get(fold(text), text) or None


# Curated lexical aliases only: do not conflate Java/JavaScript, C/C++ or .NET/Core.
_SKILLS = {
    "nodejs": ("Node.js", "frameworks_libraries"), "node.js": ("Node.js", "frameworks_libraries"),
    "reactjs": ("React", "frameworks_libraries"), "react.js": ("React", "frameworks_libraries"),
    "react": ("React", "frameworks_libraries"), "vuejs": ("Vue.js", "frameworks_libraries"),
    "vue.js": ("Vue.js", "frameworks_libraries"), "nestjs": ("NestJS", "frameworks_libraries"),
    "postgres": ("PostgreSQL", "databases"), "postgresql": ("PostgreSQL", "databases"),
    "nosql": ("NoSQL", "databases"), "mysql": ("MySQL", "databases"),
    "mongodb": ("MongoDB", "databases"), "redis": ("Redis", "databases"),
    "restful api": ("REST API", "concepts"), "rest api": ("REST API", "concepts"),
    "k8s": ("Kubernetes", "cloud_devops"), "kubernetes": ("Kubernetes", "cloud_devops"),
    "javascript": ("JavaScript", "programming_languages"), "typescript": ("TypeScript", "programming_languages"),
    "python": ("Python", "programming_languages"), "java": ("Java", "programming_languages"),
    "c++": ("C++", "programming_languages"), "c#": ("C#", "programming_languages"),
    ".net": (".NET", "frameworks_libraries"), "docker": ("Docker", "cloud_devops"),
}
_PRIORITY = {"unspecified": 0, "preferred": 1, "required": 2}
_CATEGORIES = {"programming_language": "programming_languages", "database": "databases",
               "framework": "frameworks_libraries", "tool": "tools", "platform": "platforms",
               "query_language": "query_languages", "markup_language": "markup_languages",
               "stylesheet_language": "stylesheet_languages"}


def _unique(values, transform=clean_text):
    result, seen = [], set()
    for value in values:
        text = transform(value)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result


def clean_record(job):
    """Return a deep copy; keep ordered fields, lineage and explicit missingness."""
    result = copy.deepcopy(job)
    for field in ("title", "company_name", "location_text", "experience_text", "salary_text"):
        result[field] = clean_text(result.get(field), multiline=field in {"location_text", "experience_text"})
    result["description"] = clean_text(result.get("description"), multiline=True)
    for field in ("job_roles", "seniority_levels", "required_knowledge", "soft_skills"):
        result[field] = _unique(result.get(field, []))
    result["cities"] = _unique(result.get("cities", []), canonical_city)
    skills = {}
    for item in result.get("skills", []):
        item["name"] = clean_text(item.get("name"))
        item["category"] = clean_text(item.get("category")) or "technical"
        item["category"] = _CATEGORIES.get(item["category"], item["category"])
        if not item["name"]:
            continue
        item["name"], item["category"] = _SKILLS.get(item["name"].casefold(), (item["name"], item["category"]))
        k = item["name"].casefold()
        if k not in skills:
            skills[k] = item
        elif _PRIORITY[item["requirement_type"]] > _PRIORITY[skills[k]["requirement_type"]]:
            skills[k]["requirement_type"] = item["requirement_type"]
    result["skills"] = list(skills.values())
    for item in result.get("education_requirements", []):
        item["degree"] = clean_text(item.get("degree"))
        item["majors"] = _unique(item.get("majors", []))
    languages = {}
    for item in result.get("foreign_languages", []):
        item["language"] = clean_text(item.get("language"))
        item["level_text"] = clean_text(item.get("level_text"))
        if not item["language"]:
            continue
        k = item["language"].casefold()
        if k not in languages:
            languages[k] = item
        else:
            old = languages[k]
            if _PRIORITY[item["requirement_type"]] > _PRIORITY[old["requirement_type"]]:
                old["requirement_type"] = item["requirement_type"]
            levels = _unique([old["level_text"], item["level_text"]])
            old["level_text"] = "; ".join(levels) or None
    result["foreign_languages"] = list(languages.values())
    from data_preprocessing.canonicalization import standardize_record
    return standardize_record(result)


def likely_non_it(job):
    """Review flag only, never an automatic deletion or IT-membership assertion."""
    title = fold(job.get("title"))
    non_it = r"\b(?:hr intern|digital marketing|thu mua|thi cong|ky su xay dung|phien dich|field sales)\b"
    it = r"\b(?:developer|software|devops|data engineer|business analyst|kiem thu|qa|tester)\b"
    return bool(re.search(non_it, title) and not re.search(it, title))
