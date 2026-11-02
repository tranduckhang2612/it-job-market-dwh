"""Lớp 1: chỉ cho tin có role IT trong ``ds.md`` đi tiếp tới lớp chuẩn hóa.

Module không xóa dữ liệu trong raw. Nó trả về quyết định giữ/loại để loader
loader có thể bỏ qua bản ghi không phù hợp và tiếp tục bản ghi kế tiếp.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Callable, Iterable, Iterator, Mapping


DEFAULT_ROLE_CATALOG = Path(__file__).resolve().parents[2] / "ds.md"

# Chỉ đọc các trường mô tả vai trò. Không dùng skills/description vì chúng dễ
# làm một tin ngoài IT được chấp nhận chỉ do có nhắc tới công nghệ.
SOURCE_ROLE_FIELDS = {
    "itviec": ("title", "job_expertise", "job_domains"),
    "topdev": ("title", "job_types_str", "job_types_arr", "job_types"),
}
FALLBACK_ROLE_FIELDS = ("title", "job_roles", "roles", "job_expertise")

SOURCE_LANGUAGE_FIELDS = {
    "itviec": (
        "title", "job_description", "your_skills_and_experience",
        "why_youll_love_working_here", "top_reasons_to_join",
    ),
    "topdev": (
        "title", "description", "descriptions", "responsibilities_original",
        "requirements_original", "requirements", "benefits_original", "benefits",
    ),
}
FALLBACK_LANGUAGE_FIELDS = ("title", "description", "requirements")

# Alias tiêu đề tuyển dụng thường gặp, được ánh xạ về role có thật trong ds.md.
ROLE_ALIASES = {
    "Software Developer / Engineer": (
        "software developer", "software engineer", "software development engineer",
    ),
    "Front-end Developer": (
        "frontend developer", "front end developer", "frontend engineer", "front end engineer",
    ),
    "Back-end Developer": (
        "backend developer", "back end developer", "backend engineer", "back end engineer",
    ),
    "Full-stack Developer": (
        "fullstack developer", "full stack developer", "fullstack engineer", "full stack engineer",
    ),
    "Mobile Application Developer": (
        "mobile developer", "mobile app developer", "mobile application engineer", "mobile engineer",
    ),
    "Systems Engineer": (
        "system engineer", "systems engineer", "infrastructure engineer",
        "it infrastructure engineer", "network engineer",
    ),
    "Embedded Systems Developer": (
        "embedded developer", "embedded engineer", "embedded software engineer",
    ),
    "Software Architect": ("software architect", "application architect"),
    "Technical Architect": (
        "technical architect", "enterprise architect", "chief enterprise architect",
    ),
    "Machine Learning Engineer": ("machine learning engineer", "ml engineer"),
    "AI Engineer": ("ai engineer", "artificial intelligence engineer"),
    "Database Administrator (DBA)": ("database administrator", "dba"),
    "Data Warehouse Engineer": ("data warehouse engineer", "data warehouse developer"),
    "QC Engineer": ("qc engineer", "quality control engineer"),
    "QA Engineer": (
        "qa engineer", "qa automation engineer", "automation qa engineer",
        "quality assurance engineer",
    ),
    "Automation Test Engineer": (
        "automation test engineer", "automation tester", "test automation engineer",
    ),
    "Manual Test Engineer": ("manual test engineer", "manual tester"),
    "Tester": ("tester", "kiem thu", "chuyen vien kiem thu"),
    "Bridge Software Engineer (BrSE)": (
        "bridge software engineer", "bridge system engineer", "brse",
    ),
    "UI/UX Designer": ("ui ux designer", "ui designer", "ux designer"),
    "Site Reliability Engineer (SRE)": ("site reliability engineer", "sre"),
    "Technical Support Engineer": (
        "technical support engineer", "it support engineer", "application support engineer",
    ),
    "Project Manager": ("project manager", "it project manager", "it pm"),
    "IT Communicator": ("it communicator", "it comtor", "comtor", "it interpreter"),
    "IT Consultant": (
        "it consultant", "technology consultant", "sap consultant",
        "sap sd consultant", "sap mm consultant", "sap fico consultant",
        "sap functional consultant", "sap technical consultant",
    ),
    "IT Helpdesk": ("it helpdesk", "it help desk", "helpdesk", "help desk"),
    "Penetration Tester": ("penetration tester", "pentester", "pen tester"),
}


@dataclass(frozen=True)
class RoleDefinition:
    category: str
    canonical_role: str
    keywords: tuple[str, ...]


@dataclass(frozen=True)
class RoleMatch:
    canonical_role: str
    keyword: str
    source_field: str


@dataclass(frozen=True)
class RoleGateDecision:
    accepted: bool
    reason: str
    matches: tuple[RoleMatch, ...] = ()
    language: str | None = None

    @property
    def matched_roles(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(match.canonical_role for match in self.matches))


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold().replace("đ", "d"))
    value = "".join(char for char in value if not unicodedata.combining(char))
    replacements = {
        "c++": " cplusplus ",
        "c#": " csharp ",
        ".net": " dotnet ",
        "node.js": " nodejs ",
        "react.js": " reactjs ",
    }
    for original, replacement in replacements.items():
        value = value.replace(original, replacement)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def _phrase_variants(role: str) -> set[str]:
    variants = {role}
    without_parentheses = re.sub(r"\s*\([^)]*\)\s*", " ", role).strip()
    if without_parentheses:
        variants.add(without_parentheses)

    parenthetical = re.findall(r"\(([^)]*)\)", role)
    for value in parenthetical:
        # Chỉ lấy acronym như DBA/SRE/BrSE. Phần giải thích dài không phải role.
        if re.fullmatch(r"[A-Za-z]{2,6}", value.strip()):
            variants.add(value.strip())

    if " / " in without_parentheses:
        left, right = (part.strip() for part in without_parentheses.split(" / ", 1))
        variants.add(left)
        if " " not in right and " " in left:
            variants.add(left.rsplit(" ", 1)[0] + " " + right)
        else:
            variants.add(right)

    # Nhiều nguồn dùng Engineer và Developer thay thế nhau trong title.
    for value in tuple(variants):
        if re.search(r"\bDeveloper\b", value, re.I):
            variants.add(re.sub(r"\bDeveloper\b", "Engineer", value, flags=re.I))
        if re.search(r"\bEngineer\b", value, re.I):
            variants.add(re.sub(r"\bEngineer\b", "Developer", value, flags=re.I))
    return {_normalized(value) for value in variants if _normalized(value)}


def load_role_catalog(path: Path | str = DEFAULT_ROLE_CATALOG) -> tuple[RoleDefinition, ...]:
    """Đọc danh sách bullet in đậm trong ds.md thành catalog có thể so khớp."""
    source = Path(path)
    category = "Uncategorized"
    definitions: dict[str, RoleDefinition] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(?:\d+\.\s*)?(.+?)\s*$", line)
        if heading:
            category = heading.group(1).strip()
            continue
        role_line = re.match(r"^\s*-\s+\*\*(.+?)\*\*\s*$", line)
        if not role_line:
            continue
        role = role_line.group(1).strip()
        keywords = _phrase_variants(role)
        keywords.update(_normalized(alias) for alias in ROLE_ALIASES.get(role, ()))
        definitions.setdefault(
            role,
            RoleDefinition(category, role, tuple(sorted(keywords, key=lambda item: (-len(item), item)))),
        )
    if not definitions:
        raise ValueError(f"Không đọc được role nào từ {source}.")
    unknown_aliases = set(ROLE_ALIASES).difference(definitions)
    if unknown_aliases:
        raise ValueError("Alias trỏ tới role không tồn tại trong ds.md: " + ", ".join(sorted(unknown_aliases)))
    return tuple(definitions.values())


def _text_values(value) -> Iterator[str]:
    if isinstance(value, str):
        if value.strip():
            yield value
    elif isinstance(value, Mapping):
        for key in ("name", "title", "label", "value"):
            if key in value:
                yield from _text_values(value[key])
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _text_values(item)


def _explicit_language(raw_payload: Mapping) -> str | None:
    values = []
    for field in ("inLanguage", "in_language", "content_language", "posting_language"):
        values.extend(_text_values(raw_payload.get(field)))
    for item in raw_payload.get("json_ld") or []:
        if isinstance(item, Mapping):
            values.extend(_text_values(item.get("inLanguage")))
    for value in values:
        normalized = _normalized(value)
        if normalized in {"en", "en us", "en gb", "english", "tieng anh"}:
            return "en"
        if normalized in {"vi", "vi vn", "vietnamese", "tieng viet"}:
            return "vi"
        if normalized:
            return "other"
    return None


def detect_job_language(raw_payload: Mapping | object, source: str | None = None) -> str:
    """Nhận diện ngôn ngữ nội dung: vi, en, other hoặc unknown."""
    if not isinstance(raw_payload, Mapping):
        return "unknown"
    explicit = _explicit_language(raw_payload)
    if explicit:
        return explicit
    source_name = str(source or raw_payload.get("source") or "").strip().casefold()
    fields = SOURCE_LANGUAGE_FIELDS.get(source_name, FALLBACK_LANGUAGE_FIELDS)
    parts = []
    for field in fields:
        parts.extend(_text_values(raw_payload.get(field)))
    text = re.sub(r"<[^>]+>", " ", "\n".join(parts))
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return "unknown"

    unsupported = len(re.findall(
        r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\u1100-\u11ff"
        r"\u0400-\u04ff\u0600-\u06ff\u0e00-\u0e7f]",
        text,
    ))
    if unsupported >= 4 and unsupported / len(letters) >= 0.12:
        return "other"

    lowered = text.casefold()
    # Các ký tự đặc trưng của tiếng Việt; không dùng mọi ký tự có dấu vì
    # é/è cũng xuất hiện nhiều trong tiếng Pháp.
    vietnamese_marks = len(re.findall(r"[ăâđêôơư]", lowered))
    normalized = _normalized(text)
    tokens = set(normalized.split())
    vietnamese_words = {
        "va", "voi", "cua", "cho", "cac", "trong", "cong viec", "yeu cau",
        "kinh nghiem", "ky nang", "ung vien", "lam viec", "quyen loi", "mo ta",
        "phat trien", "he thong", "du lieu", "tieng viet",
    }
    english_words = {
        "and", "with", "the", "for", "from", "job", "work", "requirements",
        "experience", "skills", "candidate", "develop", "development", "engineer",
        "developer", "manager", "data", "software", "system", "support", "english",
    }
    other_latin_words = {
        "les", "une", "avec", "pour", "nous", "vous", "travail", "poste",
        "und", "mit", "fur", "erfahrung", "kenntnisse", "arbeit",
        "los", "las", "una", "con", "para", "experiencia", "habilidades", "trabajo",
        "uma", "experiencia", "trabalho", "vaga", "dan", "dengan", "untuk", "pekerjaan",
    }
    vietnamese_score = sum(1 for phrase in vietnamese_words if " " + phrase + " " in " " + normalized + " ")
    english_score = len(tokens.intersection(english_words))
    other_latin_score = len(tokens.intersection(other_latin_words))
    if vietnamese_marks >= 2 or vietnamese_score >= 2 and vietnamese_score > english_score:
        return "vi"
    if other_latin_score >= 3 and other_latin_score > english_score * 2:
        return "other"
    if english_score >= 1 or re.search(r"[A-Za-z]{3,}", text):
        return "en"
    return "unknown"


def evaluate_role_gate(
    raw_payload: Mapping | object,
    source: str | None = None,
    catalog: tuple[RoleDefinition, ...] | None = None,
) -> RoleGateDecision:
    """Chấp nhận tin khi ít nhất một trường role khớp keyword trong ds.md."""
    if not isinstance(raw_payload, Mapping):
        return RoleGateDecision(False, "invalid_raw_payload", language="unknown")
    source_name = str(source or raw_payload.get("source") or "").strip().casefold()
    fields = SOURCE_ROLE_FIELDS.get(source_name, FALLBACK_ROLE_FIELDS)
    role_catalog = catalog if catalog is not None else load_role_catalog()
    matches = []
    seen = set()
    found_role_text = False
    for field in fields:
        for text in _text_values(raw_payload.get(field)):
            normalized_text = " " + _normalized(text) + " "
            if normalized_text.strip():
                found_role_text = True
            for definition in role_catalog:
                for keyword in definition.keywords:
                    if " " + keyword + " " not in normalized_text:
                        continue
                    identity = (definition.canonical_role, keyword, field)
                    if identity not in seen:
                        seen.add(identity)
                        matches.append(RoleMatch(definition.canonical_role, keyword, field))
                    break
    language = detect_job_language(raw_payload, source=source_name)
    if not found_role_text:
        return RoleGateDecision(False, "missing_role_text", tuple(matches), language)
    if language not in {"en", "vi"}:
        reason = "unsupported_language" if language == "other" else "unknown_language"
        return RoleGateDecision(False, reason, tuple(matches), language)
    if matches:
        return RoleGateDecision(True, "matched_role_keyword", tuple(matches), language)
    return RoleGateDecision(
        False,
        "no_matching_role_keyword" if found_role_text else "missing_role_text",
        language=language,
    )


def iter_normalization_candidates(
    raw_records: Iterable[Mapping],
    catalog: tuple[RoleDefinition, ...] | None = None,
    on_reject: Callable[[Mapping, RoleGateDecision], None] | None = None,
) -> Iterator[tuple[Mapping, RoleGateDecision]]:
    """Bỏ qua tin không đạt role gate và tiếp tục duyệt tin kế tiếp."""
    role_catalog = catalog if catalog is not None else load_role_catalog()
    for record in raw_records:
        payload = record.get("raw_payload", record) if isinstance(record, Mapping) else record
        source = record.get("source") if isinstance(record, Mapping) else None
        decision = evaluate_role_gate(payload, source=source, catalog=role_catalog)
        if decision.accepted:
            yield record, decision
        elif on_reject is not None:
            on_reject(record, decision)


def iter_staging_candidates(
    raw_records: Iterable[Mapping],
    catalog: tuple[RoleDefinition, ...] | None = None,
    on_reject: Callable[[Mapping, RoleGateDecision], None] | None = None,
) -> Iterator[tuple[Mapping, RoleGateDecision]]:
    """Alias tương thích; code mới nên dùng iter_normalization_candidates()."""
    yield from iter_normalization_candidates(raw_records, catalog=catalog, on_reject=on_reject)
