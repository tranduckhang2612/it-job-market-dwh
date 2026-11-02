"""Lớp 2: ánh xạ raw ITviec/TopDev về hợp đồng dữ liệu thống nhất.

Ba nguyên tắc chính của module:

* không bịa giá trị khi nguồn không có bằng chứng;
* ưu tiên trường có cấu trúc trước suy luận từ text;
* trả kèm metadata ngắn để giải thích nguồn, độ tin cậy và xung đột.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

from data_preprocessing.normalization.contracts import (
    ARRAY_FIELDS,
    NORMALIZATION_META_FIELD,
    NORMALIZATION_VERSION,
    NORMALIZE_DATA_VERSION,
    NORMALIZED_FIELDS,
    PERSISTED_NORMALIZED_FIELDS,
    NormalizationError,
    validate_normalized_job,
    validate_normalized_job_with_meta,
    validate_normalized_storage_job,
)
from data_preprocessing.normalization.role_gate import RoleGateDecision


VIETNAM_TZ = timezone(timedelta(hours=7))


def clean_text(value) -> str | None:
    if value is None:
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result or None


def _fold(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold().replace("đ", "d"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9+#.]+", " ", text).strip()


def _canonical_url(value) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit((parsed.scheme.lower(), parsed.hostname.lower(), parsed.path.rstrip("/"), "", ""))


def normalized_datetime(value, *, date_only: bool = False) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min, tzinfo=VIETNAM_TZ)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        # TopDev đôi khi trả Unix timestamp theo giây hoặc mili giây.
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            result = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            if not date_only:
                return None
            text += "T00:00:00+07:00"
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        if not date_only:
            return None
        result = result.replace(tzinfo=VIETNAM_TZ)
    return result.astimezone(VIETNAM_TZ)


def _unique_text(values) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,;|\n]", values)
    elif isinstance(values, dict):
        values = values.values()
    elif not isinstance(values, (list, tuple, set)):
        values = [] if values is None else [values]
    result = []
    seen = set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("name") or value.get("title") or value.get("label") or value.get("value")
        text = clean_text(value)
        identity = text.casefold() if text else None
        if text and identity not in seen:
            seen.add(identity)
            result.append(text)
    return result


def _html_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [_html_text(item) for item in value]
        return "\n".join(part for part in parts if part) or None
    if isinstance(value, dict):
        for key in ("value", "text", "description", "content"):
            if key in value:
                return _html_text(value[key])
        return None
    text = str(value).strip()
    if not text:
        return None
    soup = BeautifulSoup(text, "html.parser")
    for node in soup.select("script, style, svg, noscript"):
        node.decompose()
    lines = [re.sub(r"\s+", " ", line).strip()
             for line in soup.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line) or None


def _description(raw: dict, source: str) -> str:
    parts = []
    if source == "itviec":
        sections = raw.get("sections") or []
        for section in sections if isinstance(sections, list) else []:
            if isinstance(section, dict):
                body = _html_text(section.get("text") or section.get("html"))
                heading = clean_text(section.get("heading"))
                if body:
                    parts.append("\n".join(item for item in (heading, body) if item))
        if not parts:
            for heading, field in (
                ("Job description", "job_description"),
                ("Requirements", "your_skills_and_experience"),
                ("Benefits", "why_youll_love_working_here"),
                ("Reasons to join", "top_reasons_to_join"),
            ):
                body = _html_text(raw.get(field))
                if body:
                    parts.append(f"{heading}\n{body}")
    else:
        for heading, fields in (
            ("Responsibilities", ("responsibilities_original", "responsibilities", "descriptions", "description")),
            ("Requirements", ("requirements_original", "requirements")),
            ("Benefits", ("benefits_original", "benefits")),
        ):
            body = None
            for field in fields:
                body = _html_text(raw.get(field))
                if body:
                    break
            if body:
                parts.append(f"{heading}\n{body}")
    result = "\n\n".join(parts).strip()
    if not result:
        raise NormalizationError("Tin không có description để đưa vào normalize_data.")
    return result


def _short_evidence(value, limit: int = 240) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _meta_field(
    source: str | None,
    confidence: str,
    evidence=(),
    conflicts=(),
) -> dict:
    evidence_values = [item for value in evidence if (item := _short_evidence(value))]
    conflict_values = [item for value in conflicts if (item := _short_evidence(value))]
    return {
        "source": source,
        "confidence": confidence,
        "evidence": _unique_text(evidence_values),
        "conflicts": _unique_text(conflict_values),
    }


_VIETNAM_PLACES = (
    "Hồ Chí Minh", "Hà Nội", "Đà Nẵng", "Hải Phòng", "Cần Thơ",
    "An Giang", "Bà Rịa - Vũng Tàu", "Bắc Giang", "Bắc Kạn", "Bạc Liêu",
    "Bắc Ninh", "Bến Tre", "Bình Định", "Bình Dương", "Bình Phước",
    "Bình Thuận", "Cà Mau", "Cao Bằng", "Đắk Lắk", "Đắk Nông",
    "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang", "Hà Nam",
    "Hà Tĩnh", "Hải Dương", "Hậu Giang", "Hòa Bình", "Hưng Yên",
    "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu", "Lâm Đồng",
    "Lạng Sơn", "Lào Cai", "Long An", "Nam Định", "Nghệ An", "Ninh Bình",
    "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình", "Quảng Nam",
    "Quảng Ngãi", "Quảng Ninh", "Quảng Trị", "Sóc Trăng", "Sơn La",
    "Tây Ninh", "Thái Bình", "Thái Nguyên", "Thanh Hóa", "Thừa Thiên Huế",
    "Tiền Giang", "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc",
    "Yên Bái", "Huế", "Vũng Tàu", "Nha Trang",
)
_CITY_ALIASES = {_fold(place): place for place in _VIETNAM_PLACES}
_CITY_ALIASES.update({
    "ho chi minh city": "Hồ Chí Minh", "hcm": "Hồ Chí Minh",
    "hcmc": "Hồ Chí Minh", "sai gon": "Hồ Chí Minh",
    "hanoi": "Hà Nội", "danang": "Đà Nẵng",
    "ba ria vung tau": "Bà Rịa - Vũng Tàu",
})
_SORTED_CITY_ALIASES = tuple(sorted(_CITY_ALIASES, key=len, reverse=True))


def _extract_cities(values) -> list[str]:
    result = []
    seen = set()
    for value in _unique_text(values):
        folded = _fold(value)
        matches = []
        for alias in _SORTED_CITY_ALIASES:
            match = re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", folded)
            if match:
                matches.append((match.start(), match.end(), -len(alias), _CITY_ALIASES[alias]))
        occupied = []
        for start, end, _, city in sorted(matches):
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            occupied.append((start, end))
            identity = city.casefold()
            if identity not in seen:
                seen.add(identity)
                result.append(city)
    return result


def _locations(raw: dict, source: str) -> tuple[str | None, list[str], dict]:
    full_addresses = []
    structured_regions = []
    source_cities = []
    if source == "itviec":
        for item in raw.get("addresses") or []:
            if isinstance(item, dict):
                full_addresses.extend(_unique_text(item.get("address")))
            else:
                full_addresses.extend(_unique_text(item))
        source_cities.extend(_unique_text(raw.get("city_on_listing")))
        structured = raw.get("locations_structured")
        items = structured if isinstance(structured, list) else [structured]
        for item in items:
            if not isinstance(item, dict):
                continue
            address = item.get("address") or {}
            if isinstance(address, dict):
                structured_regions.extend(_unique_text(
                    address.get("addressRegion") or address.get("addressLocality")
                ))
                full_addresses.extend(_unique_text(address.get("streetAddress")))
    else:
        structured = raw.get("addresses") or {}
        if isinstance(structured, dict):
            full_addresses.extend(_unique_text(structured.get("full_addresses")))
            structured_regions.extend(_unique_text(structured.get("address_region_array")))
        elif isinstance(structured, list):
            for item in structured:
                if isinstance(item, dict):
                    full_addresses.extend(_unique_text(item.get("full_address") or item.get("address")))
                    structured_regions.extend(_unique_text(item.get("city") or item.get("province")))
                else:
                    full_addresses.extend(_unique_text(item))

    candidates = (
        ("structured_address", _extract_cities(structured_regions)),
        ("source_city", _extract_cities(source_cities)),
        ("full_address", _extract_cities(full_addresses)),
        ("location_text", _extract_cities(raw.get("location_text"))),
        ("title", _extract_cities(raw.get("title"))),
    )
    selected_source, cities = next(
        ((name, values) for name, values in candidates if values), (None, [])
    )
    title_cities = candidates[-1][1]
    conflicts = []
    if selected_source != "title" and title_cities and set(title_cities) != set(cities):
        conflicts.append("title: " + ", ".join(title_cities))
    for candidate_source, candidate_values in candidates[:4]:
        if (candidate_source != selected_source and candidate_values and cities
                and set(candidate_values) != set(cities)):
            conflicts.append(candidate_source + ": " + ", ".join(candidate_values))
    location_text = ("\n".join(_unique_text(full_addresses))
                     or clean_text(raw.get("location_text"))
                     or clean_text(raw.get("city_on_listing"))
                     or (", ".join(cities) if cities else None))
    confidence = "high" if selected_source in {"structured_address", "source_city"} else (
        "medium" if selected_source == "full_address" else "low" if selected_source else "none"
    )
    meta = _meta_field(selected_source, confidence, cities, conflicts)
    return location_text, cities, meta


_WORK_MODE_PATTERNS = {
    "remote": (
        r"^remote$", r"\bfully remote\b", r"\bremote (?:work|working|job|position|role|option)\b",
        r"\bwork(?:ing)? remotely\b", r"\bwfh\b", r"\bwork from home\b",
        r"\blam viec tu xa\b",
    ),
    "hybrid": (
        r"^hybrid$", r"\bhybrid (?:work|working|model|mode|position|role)\b",
        r"\bwork(?:ing)? (?:in )?hybrid\b", r"\blinh hoat\b",
    ),
    "onsite": (
        r"^onsite$", r"^on site$", r"\bonsite (?:work|working|job|position|role)\b",
        r"\bon site\b", r"\bat office\b",
        r"\btai van phong\b", r"\btruc tiep\b",
    ),
}


def _work_mode_matches(value) -> list[str]:
    folded = _fold(_html_text(value) or value)
    return [mode for mode, patterns in _WORK_MODE_PATTERNS.items()
            if any(re.search(pattern, folded) for pattern in patterns)]


def _work_mode(raw: dict, source: str, description: str) -> tuple[str | None, dict]:
    explicit = raw.get("working_model") if source == "itviec" else raw.get("job_types_str")
    explicit_modes = _work_mode_matches(explicit)
    fallback_modes = _work_mode_matches(description)
    conflicts = []
    if len(explicit_modes) == 1:
        selected = explicit_modes[0]
        conflicts.extend("description: " + mode for mode in fallback_modes if mode != selected)
        return selected, _meta_field(
            "raw.working_model" if source == "itviec" else "raw.job_types_str",
            "high", [explicit], conflicts,
        )
    if len(explicit_modes) > 1:
        conflicts.append("explicit field contains multiple work modes")
    if len(fallback_modes) == 1:
        return fallback_modes[0], _meta_field(
            "description", "medium", fallback_modes, conflicts,
        )
    if len(fallback_modes) > 1:
        conflicts.append("description: " + ", ".join(fallback_modes))
    return None, _meta_field(None, "none", (), conflicts)


def _raw_skill_names(raw: dict, source: str) -> list[str]:
    values = []
    if source == "itviec":
        values.extend(_unique_text(raw.get("skills")))
    else:
        values.extend(_unique_text(raw.get("skills_arr")))
        values.extend(_unique_text(raw.get("extra_skills")))
        values.extend(_unique_text(raw.get("skills_str")))
    return _unique_text(values)


_SKILL_ALIASES = {
    "reactjs": ("React",), "react js": ("React",), "react.js": ("React",),
    "vuejs": ("Vue.js",), "vue js": ("Vue.js",), "vue.js": ("Vue.js",),
    "vue": ("Vue.js",),
    "nodejs": ("Node.js",), "node js": ("Node.js",), "node.js": ("Node.js",),
    "dotnet": (".NET",), "net": (".NET",), ".net": (".NET",),
    "postgres": ("PostgreSQL",), "postgresql": ("PostgreSQL",),
    "k8s": ("Kubernetes",), "golang": ("Go",),
    "c c++": ("C", "C++"), "c/c++": ("C", "C++"),
    "ci cd": ("CI/CD",), "qa qc": ("QA/QC",),
    "nosql": ("NoSQL",), "pyspark": ("PySpark",),
}
_LANGUAGE_SKILL_KEYS = {
    "english", "tieng anh", "korean", "tieng han", "japanese", "tieng nhat",
    "chinese", "tieng trung",
}


def _is_language_skill(value: str) -> bool:
    key = _fold(value)
    if key in _LANGUAGE_SKILL_KEYS:
        return True
    patterns = (
        r"(?:english|tieng anh)(?: (?:communication|communicative|proficiency|language|skills?|[abc][12]))",
        r"(?:japanese|tieng nhat)(?: (?:communication|proficiency|language|skills?|(?:jlpt )?n[1-5]))",
        r"(?:korean|tieng han)(?: (?:communication|proficiency|language|skills?|topik(?: level)? [1-6]))",
        r"(?:chinese|tieng trung)(?: (?:communication|proficiency|language|skills?|hsk(?: level)? [1-6]))",
    )
    return any(re.fullmatch(pattern, key) for pattern in patterns)
_SKILL_GROUPS = (
    ("programming_language", {
        "python", "java", "javascript", "typescript", "go", "c", "c++", "c#",
        "php", "ruby", "kotlin", "swift",
    }),
    ("database", {
        "sql", "postgresql", "mysql", "sql server", "mongodb", "redis", "oracle",
        "elasticsearch", "nosql", "database", "dba",
    }),
    ("cloud", {"aws", "azure", "gcp", "google cloud", "vmware"}),
    ("devops", {
        "docker", "kubernetes", "terraform", "jenkins", "gitlab ci", "github actions",
        "ci cd", "devops",
    }),
    ("framework_library", {
        "react", "angular", "vue.js", "node.js", "spring", "spring boot", "django",
        "flask", "fastapi", ".net", "mvvm",
    }),
    ("data_ai", {
        "ai", "machine learning", "ml", "etl", "pyspark", "data warehouse",
        "data engineering", "data quality tools", "data warehousing",
    }),
    ("security", {
        "security", "cybersecurity", "siem", "owasp", "pentest", "penetration testing",
        "cloud security", "application security", "data privacy compliance",
    }),
    ("operating_system", {"linux", "windows server", "rtos", "kernel"}),
    ("enterprise", {"sap", "fiori", "salesforce", "magento2", "shopify"}),
    ("networking", {"cisco", "network", "networking"}),
    ("testing", {"tester", "qa", "qa qc", "automation test", "automation tester"}),
    ("design", {"figma", "adobe xd", "sketch", "invision", "ui ux"}),
    ("methodology", {
        "agile", "agile scrum", "scrum", "project management", "team management",
        "strategy planning",
    }),
)


def _canonical_skills(name: str) -> tuple[str, ...]:
    key = _fold(name)
    return _SKILL_ALIASES.get(key, (clean_text(name),))


def _skill_category(name: str) -> str:
    key = _fold(name)
    for category, names in _SKILL_GROUPS:
        if key in names:
            return category
    return "other"


def _skills(raw: dict, source: str) -> tuple[list[dict], dict, list[str]]:
    raw_names = _raw_skill_names(raw, source)
    result = []
    seen = set()
    removed_languages = []
    for raw_name in raw_names:
        if _is_language_skill(raw_name):
            removed_languages.append(raw_name)
            continue
        for name in _canonical_skills(raw_name):
            if not name:
                continue
            identity = name.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            result.append({"name": name, "category": _skill_category(name)})
    conflicts = (["moved to foreign_languages: " + ", ".join(removed_languages)]
                 if removed_languages else [])
    source_field = "raw.skills" if source == "itviec" else "raw.skills_arr/extra_skills"
    return result, _meta_field(source_field, "high", raw_names, conflicts), raw_names


def _pattern_text(value) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or "").casefold().replace("đ", "d"))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9+.,-]+", " ", normalized).strip()


_NUMBER = r"\d+(?:[.,]\d+)?"
_YEAR_UNIT = r"(?:years?|yrs?|nam)"
_MONTH_UNIT = r"(?:months?|thang)"
_TECH_EXPERIENCE_TERMS = {
    "java", "python", "javascript", "typescript", "react", "reactjs", "angular",
    "vue", "nodejs", "golang", "go", "c++", "c#", "php", "ruby", "kotlin",
    "swift", "sql", "aws", "azure", "gcp", "docker", "kubernetes", "sap",
    "oracle", "spring", "net",
}


def _experience_units(text: str | None) -> list[str]:
    if not text:
        return []
    units = []
    for line in text.splitlines():
        for unit in re.split(r"(?<=[.!?])\s+|\s*[;•]\s*", line):
            cleaned = clean_text(unit)
            if cleaned:
                units.append(cleaned)
    return units


def _experience_bounds(text: str) -> tuple[Decimal | None, Decimal | None, str | None]:
    folded = _pattern_text(text)
    range_match = re.search(
        rf"\b({_NUMBER})\s*(?:-|to|den|toi)\s*({_NUMBER})\s*{_YEAR_UNIT}\b", folded,
    )
    if range_match:
        low, high = (Decimal(value.replace(",", ".")) for value in range_match.groups())
        return (min(low, high), max(low, high), "range")
    under_match = re.search(
        rf"\b(?:under|less than|below|duoi)\s*({_NUMBER})\s*{_YEAR_UNIT}\b", folded,
    )
    if under_match:
        return Decimal("0"), Decimal(under_match.group(1).replace(",", ".")), "under"
    lower_match = re.search(
        rf"\b(?:at least|minimum|min|from|more than|over|tu|tren|toi thieu)\s*"
        rf"({_NUMBER})\s*{_YEAR_UNIT}\b", folded,
    )
    plus_match = re.search(rf"\b({_NUMBER})\s*\+\s*{_YEAR_UNIT}\b", folded)
    general_match = re.search(
        rf"\b({_NUMBER})\s*{_YEAR_UNIT}(?:\s+of)?\s+(?:professional\s+)?"
        rf"(?:work\s+)?experience\b", folded,
    )
    vietnamese_match = re.search(
        rf"\b({_NUMBER})\s*nam\s+kinh nghiem(?:\s+lam viec)?\b", folded,
    )
    experience_first_match = re.search(
        rf"\bkinh nghiem(?:\s+[a-z]+){{0,6}}\s+({_NUMBER})\s*nam\b", folded,
    )
    match = lower_match or plus_match or general_match or vietnamese_match or experience_first_match
    if match:
        return Decimal(match.group(1).replace(",", ".")), None, "minimum"
    month_match = re.search(
        rf"\b(?:at least|minimum|min|from|tu|toi thieu)?\s*({_NUMBER})\s*{_MONTH_UNIT}"
        rf"(?:(?:\s+of)?\s+(?:work\s+)?experience|\s+kinh nghiem(?:\s+lam viec)?)\b", folded,
    )
    if month_match:
        months = Decimal(month_match.group(1).replace(",", "."))
        return months / Decimal("12"), None, "minimum"
    return None, None, None


def _is_general_experience(unit: str) -> bool:
    minimum, maximum, relation = _experience_bounds(unit)
    if relation is None:
        return False
    folded = _pattern_text(unit)
    tech_hits = [term for term in _TECH_EXPERIENCE_TERMS
                 if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", folded)]
    if not tech_hits:
        return True
    general_markers = (
        "overall", "total experience", "professional experience", "work experience",
        "software development", "data engineering", "similar role", "similar position",
        "kinh nghiem o vi tri", "kinh nghiem tai vi tri", "kinh nghiem trong linh vuc",
    )
    if any(marker in folded for marker in general_markers):
        return True
    # Tên công nghệ đứng trước hay sau số năm vẫn là bằng chứng theo công nghệ,
    # không phải kinh nghiệm nghề nghiệp tổng quát.
    return False


def _requirements_text(raw: dict, source: str) -> str | None:
    return _html_text(
        raw.get("your_skills_and_experience") if source == "itviec"
        else raw.get("requirements_original") or raw.get("requirements")
    )


def _experience(raw: dict, source: str) -> tuple[dict, dict]:
    structured = raw.get("experience_requirements_structured") if source == "itviec" else None
    if isinstance(structured, dict):
        months_value = structured.get("monthsOfExperience")
        try:
            months = Decimal(str(months_value)) if months_value is not None else None
        except (InvalidOperation, ValueError):
            months = None
        if months is not None and months >= 0:
            years = (months / Decimal("12")).quantize(Decimal("0.01"))
            months_text = format(months.normalize(), "f")
            text = f"{months_text} months of experience"
            values = {
                "experience_text": text,
                "experience_min_years": years,
                "experience_max_years": None,
            }
            meta = _meta_field(
                "raw.experience_requirements_structured.monthsOfExperience",
                "high", [text], (),
            )
            meta["relation"] = "minimum"
            return values, meta

    primary = _html_text(raw.get("experience")) if source == "topdev" else None
    primary_source = "raw.experience" if primary else None
    requirement = _requirements_text(raw, source)
    candidates = [unit for unit in _experience_units(primary or requirement)
                  if _is_general_experience(unit)]
    if not candidates and primary and requirement and primary != requirement:
        candidates = [unit for unit in _experience_units(requirement)
                      if _is_general_experience(unit)]
        primary_source = "requirements" if candidates else primary_source
    elif candidates and not primary_source:
        primary_source = "requirements"
    if not candidates:
        values = {
            "experience_text": None,
            "experience_min_years": None,
            "experience_max_years": None,
        }
        return values, _meta_field(None, "none")
    parsed = [(candidate, *_experience_bounds(candidate)) for candidate in candidates]
    minimums = [minimum for _, minimum, _, _ in parsed if minimum is not None]
    maximums = [maximum for _, _, maximum, _ in parsed if maximum is not None]
    minimum = max(minimums) if minimums else None
    maximum = min(maximums) if maximums else None
    conflicts = []
    if minimum is not None and maximum is not None and minimum > maximum:
        conflicts.append("incompatible experience bounds")
        minimum = maximum = None
        relation = "conflict"
    elif len(parsed) == 1:
        relation = parsed[0][3]
    elif maximum is not None:
        relation = "range"
    else:
        relation = "minimum"
    values = {
        "experience_text": "\n".join(candidates),
        "experience_min_years": minimum,
        "experience_max_years": maximum,
    }
    meta = _meta_field(primary_source, "high", candidates, conflicts)
    meta["relation"] = relation
    return values, meta


def _clear_source_levels(raw: dict, source: str) -> tuple[list[str], list[str]]:
    if source == "itviec":
        values = _unique_text(raw.get("seniority_declared"))
        if not values:
            values = _unique_text(raw.get("accepted_levels"))
    else:
        values = _unique_text(raw.get("job_levels_arr") or raw.get("job_levels_str"))
    levels = []
    evidence = []
    rules = (
        ("All levels", r"\b(all levels?|tat ca cap bac|moi cap do)\b"),
        ("Intern", r"\b(intern|internship|thuc tap|thuc tap sinh)\b"),
        ("Fresher", r"\b(fresher|entry level|moi tot nghiep)\b"),
        ("Junior", r"\b(junior|jr)\b"),
        ("Mid-level", r"\b(middle|mid level|midlevel)\b"),
        ("Senior", r"\b(senior|sr|chuyen vien cap cao|chuyen vien cao cap|cvcc)\b"),
        ("Lead", r"\b(lead|leader|truong nhom|head)\b"),
        ("Principal", r"\bprincipal\b"),
        ("Architect", r"\barchitect\b"),
        ("Director", r"\b(director|giam doc)\b"),
        ("CTO", r"\bcto\b|chief technology officer"),
        ("CIO", r"\bcio\b|chief information officer"),
    )
    for value in values:
        folded = _fold(value)
        for level, pattern in rules:
            if re.search(pattern, folded):
                levels.append(level)
                evidence.append(value)
                break
    levels = _unique_text(levels)
    if "All levels" in levels:
        return ["All levels"], evidence
    return levels, evidence


def _title_level(title: str) -> tuple[list[str], list[str]]:
    folded = _fold(title)
    # Cấp vai trò cụ thể được ưu tiên hơn các từ như Senior xuất hiện cùng title.
    rules = (
        ("All levels", r"\ball levels?\b|\btat ca cap bac\b"),
        ("CTO", r"\bcto\b|chief technology officer"),
        ("CIO", r"\bcio\b|chief information officer"),
        ("Director", r"\bdirector\b|\bgiam doc\b"),
        ("Principal", r"\bprincipal\b"),
        ("Architect", r"\barchitect\b"),
        ("Lead", r"\b(lead|leader|head|truong nhom)\b"),
        ("Senior", r"\b(senior|sr|expert|chuyen vien cap cao|chuyen vien cao cap|cvcc)\b"),
        ("Mid-level", r"\b(middle|mid level|midlevel)\b"),
        ("Junior", r"\b(junior|jr)\b"),
        ("Fresher", r"\b(fresher|entry level|moi tot nghiep)\b"),
        ("Intern", r"\b(intern|internship|thuc tap|thuc tap sinh)\b"),
    )
    for level, pattern in rules:
        if re.search(pattern, folded):
            return [level], [title]
    return [], []


def _experience_level(experience: dict, experience_meta: dict) -> list[str]:
    minimum = experience["experience_min_years"]
    maximum = experience["experience_max_years"]
    relation = experience_meta.get("relation")
    if relation == "under" and maximum is not None and maximum <= 1:
        return ["Fresher"]
    if minimum is None:
        return []
    if maximum is not None:
        reaches = (
            (lambda boundary: maximum > boundary)
            if relation == "under"
            else (lambda boundary: maximum >= boundary)
        )
        overlapping = []
        if minimum < 1 and reaches(0):
            overlapping.append("Fresher")
        if minimum < 3 and reaches(1):
            overlapping.append("Junior")
        if minimum < 5 and reaches(3):
            overlapping.append("Mid-level")
        if reaches(5):
            overlapping.append("Senior")
        return overlapping if len(overlapping) <= 2 else []
    if minimum < 1:
        return ["Fresher"]
    if minimum < 3:
        return ["Junior"]
    if minimum < 5:
        return ["Mid-level"]
    return ["Senior"]


def _seniority(
    raw: dict,
    source: str,
    title: str,
    experience: dict,
    experience_meta: dict,
) -> tuple[list[str], dict]:
    explicit, explicit_evidence = _clear_source_levels(raw, source)
    title_levels, title_evidence = _title_level(title)
    experience_levels = _experience_level(experience, experience_meta)
    experience_evidence = [experience.get("experience_text")] if experience_levels else []
    candidates = {
        "source_explicit": explicit,
        "title": title_levels,
        "general_experience": experience_levels,
    }
    if explicit:
        selected, selected_source, evidence, confidence = (
            explicit, "source_explicit", explicit_evidence, "high"
        )
    elif title_levels:
        selected, selected_source, evidence, confidence = (
            title_levels, "title", title_evidence, "high"
        )
    elif experience_levels:
        selected, selected_source, evidence, confidence = (
            experience_levels, "general_experience", experience_evidence, "medium"
        )
    else:
        selected, selected_source, evidence, confidence = [], None, [], "none"
    conflicts = []
    selected_set = set(selected)
    for candidate_source, values in candidates.items():
        if values and candidate_source != selected_source and set(values) != selected_set:
            conflicts.append(candidate_source + ": " + ", ".join(values))
    meta = _meta_field(selected_source, confidence, evidence, conflicts)
    meta["candidates"] = candidates
    return selected, meta


def _derived_title_roles(title: str) -> list[str]:
    folded = _fold(title)
    rules = (
        ("AI Engineer", r"(?=.*\b(?:ai|artificial intelligence)\b)(?=.*\b(?:engineer|developer)\b)"),
        ("iOS Developer", r"(?=.*\bios\b)(?=.*\b(?:engineer|developer)\b)"),
        ("Android Developer", r"(?=.*\bandroid\b)(?=.*\b(?:engineer|developer)\b)"),
        ("Security Engineer", r"(?=.*\b(?:security|cybersecurity|appsec)\b)(?=.*\bengineer\b)"),
        ("Technical Architect", r"\b(?:technical|enterprise) architect\b"),
        ("SAP Developer", r"(?=.*\bsap\b)(?=.*\bdeveloper\b)"),
        ("Software Developer / Engineer", r"\bsoftware (?:developer|engineer|engineering)\b"),
    )
    return [role for role, pattern in rules if re.search(pattern, folded)]


def _job_roles(role_decision: RoleGateDecision, title: str) -> tuple[list[str], dict]:
    title_matches = [match for match in role_decision.matches if match.source_field == "title"]
    title_roles = _unique_text(
        [match.canonical_role for match in title_matches] + _derived_title_roles(title)
    )
    source_matches = [match for match in role_decision.matches if match.source_field != "title"]
    source_roles = _unique_text([match.canonical_role for match in source_matches])
    if title_roles:
        selected = title_roles
        selected_source = "title"
        evidence = [match.keyword for match in title_matches] or [title]
        confidence = "high"
    else:
        selected = source_roles
        selected_source = "source_role_fields" if source_roles else None
        evidence = [match.keyword for match in source_matches]
        confidence = "high" if source_roles else "none"
    conflicts = []
    extra_source_roles = [role for role in source_roles if role not in selected]
    if title_roles and extra_source_roles:
        conflicts.append("source_role_fields: " + ", ".join(extra_source_roles))
    meta = _meta_field(selected_source, confidence, evidence, conflicts)
    meta["candidates"] = {"title": title_roles, "source_role_fields": source_roles}
    return selected, meta


def _decimal(value) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value).replace(" ", "").replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _salary_number(token: str, multiplier: Decimal) -> Decimal | None:
    value = token.strip().replace(" ", "")
    if not value:
        return None
    if multiplier > 1 and re.fullmatch(r"\d+[.,]\d{1,2}", value):
        value = value.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
        value = re.sub(r"[.,]", "", value)
    else:
        value = value.replace(",", ".")
    try:
        result = Decimal(value) * multiplier
    except InvalidOperation:
        return None
    return result if result.is_finite() and result > 0 else None


def _salary_bounds_from_text(text: str | None) -> tuple[Decimal | None, Decimal | None]:
    if not text:
        return None, None
    folded = _fold(text)
    multiplier = Decimal(1_000_000) if re.search(r"\b(trieu|million|tr)\b", folded) else (
        Decimal(1_000) if re.search(r"(?<!\w)k(?!\w)", folded) else Decimal(1)
    )
    tokens = re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?!\w)", text)
    values = [number for number in (_salary_number(token, multiplier) for token in tokens) if number]
    if not values:
        return None, None
    if re.search(r"\b(up to|max(?:imum)?|toi da)\b", folded):
        return None, values[0]
    if re.search(r"\b(from|min(?:imum)?|tu)\b", folded) or "+" in text:
        return values[0], None
    return (values[0], values[1]) if len(values) > 1 else (values[0], values[0])


def _salary(raw: dict, source: str) -> dict:
    salary = raw.get("salary") or {}
    salary = salary if isinstance(salary, dict) else {"value": salary}
    text = clean_text(salary.get("text") or salary.get("value"))
    folded = _fold(text)
    visibility = str(salary.get("visibility") or "").casefold()
    negotiable = str(salary.get("is_negotiable") or "").casefold() in {"1", "true", "yes"}
    negotiable = negotiable or bool(re.search(r"\b(negotiable|thoa thuan|thuong luong)\b", folded))
    structured = salary.get("structured") or {}
    structured = structured if isinstance(structured, dict) else {}
    value = structured.get("value") or {}
    value = value if isinstance(value, dict) else {}
    low = _decimal(salary.get("min_filter") or salary.get("min") or value.get("minValue"))
    high = _decimal(salary.get("max_filter") or salary.get("max") or value.get("maxValue"))
    if low is None and high is None:
        exact = _decimal(value.get("value"))
        low = high = exact
    if low is None and high is None:
        low, high = _salary_bounds_from_text(text)
    currency = clean_text(salary.get("currency") or structured.get("currency") or structured.get("salaryCurrency"))
    if not currency:
        currency = "VND" if re.search(r"\b(vnd|trieu|triệu)\b", str(text or ""), re.I) else (
            "USD" if re.search(r"\bUSD\b|\$", str(text or ""), re.I) else None
        )
    currency = currency.upper() if currency and re.fullmatch(r"[A-Za-z]{3}", currency) else None
    unit = _fold(salary.get("unit") or value.get("unitText"))
    period = next((name for name, words in (
        ("hour", ("hour", "gio")), ("day", ("day", "ngay")),
        ("month", ("month", "thang")), ("year", ("year", "nam")),
    ) if any(word in unit or re.search(r"\b" + word + r"\b", folded) for word in words)), None)
    basis = "gross" if re.search(r"\bgross\b", folded) else "net" if re.search(r"\bnet\b", folded) else None
    if "login_required" in visibility:
        status, low, high = "unknown", None, None
    elif negotiable:
        status, low, high = "negotiable", None, None
    elif low is not None or high is not None:
        status = "disclosed"
    else:
        status = "not_disclosed" if not text else "unknown"
    if low is not None and high is not None and low > high:
        low, high = high, low
    return {
        "salary_text": text, "salary_min": low, "salary_max": high,
        "salary_currency": currency, "salary_period": period,
        "salary_basis": basis, "salary_status": status,
    }


_FOREIGN_LANGUAGE_PATTERNS = (
    ("English", r"\b(english|tieng anh|toeic|ielts|cefr)\b"),
    ("Japanese", r"\b(japanese|tieng nhat|jlpt)\b"),
    ("Korean", r"\b(korean|tieng han|topik)\b"),
    ("Chinese", r"\b(chinese|mandarin|tieng trung|hsk)\b"),
)


def _language_level_text(language: str, snippets: list[str]) -> str | None:
    text = "\n".join(snippets)
    certifications = {
        "English": (
            r"\bTOEIC\s*(?:score\s*(?:of\s*)?)?\d{3,4}\b",
            r"\bIELTS\s*(?:score\s*(?:of\s*)?)?\d(?:[.,]\d)?\b",
            r"\bCEFR\s*[ABC][12]\b",
            r"\bEnglish\s*(?:level\s*)?[ABC][12]\b",
            r"\b[ABC][12]\s+English\b",
        ),
        "Japanese": (
            r"\bJLPT\s*N[1-5]\b",
            r"\bJapanese\s*(?:level\s*)?N[1-5]\b",
        ),
        "Korean": (r"\bTOPIK\s*(?:level\s*)?[1-6]\b",),
        "Chinese": (r"\bHSK\s*(?:level\s*)?[1-6]\b",),
    }
    for pattern in certifications.get(language, ()):
        match = re.search(pattern, text, re.I)
        if match:
            return clean_text(match.group(0))
    descriptor_patterns = {
        "English": (
            r"\b(?:fluent|proficient|advanced|business)\s+(?:in\s+)?English\b",
            r"\b(?:basic|intermediate|advanced|good|strong)\s+(?:at\s+)?English"
            r"(?:\s+(?:skills?|communication|reading|writing|speaking|comprehension))?\b",
            r"\bEnglish\s+(?:fluency|proficiency|communication|communicative)\b",
            r"\bEnglish\s+(?:reading|writing|speaking)\s+(?:skills?|comprehension)\b",
            r"\b(?:good|strong)\s+(?:communication\s+)?of\s+English\b",
            r"\b(?:able to|can)\s+communicate\s+in\s+English\b",
            r"\b(?:thanh thao|giao tiep tot|giao tiep)\s+(?:bang\s+)?tieng Anh\b",
            r"\btieng Anh\s+(?:thanh thao|giao tiep tot|giao tiep)\b",
            r"(?:thành thạo|giao tiếp tốt|giao tiếp|đọc hiểu)\s+(?:bằng\s+)?tiếng Anh",
            r"tiếng Anh\s+(?:thành thạo|giao tiếp tốt|giao tiếp|đọc hiểu)",
        ),
        "Japanese": (
            r"\b(?:fluent|proficient|business)\s+(?:in\s+)?Japanese\b",
            r"\bJapanese\s+(?:fluency|proficiency|communication)\b",
            r"\b(?:able to|can)\s+communicate\s+in\s+Japanese\b",
        ),
        "Korean": (
            r"\b(?:fluent|proficient|business)\s+(?:in\s+)?Korean\b",
            r"\bKorean\s+(?:fluency|proficiency|communication)\b",
            r"\b(?:able to|can)\s+communicate\s+in\s+Korean\b",
        ),
        "Chinese": (
            r"\b(?:fluent|proficient|business)\s+(?:in\s+)?(?:Chinese|Mandarin)\b",
            r"\b(?:Chinese|Mandarin)\s+(?:fluency|proficiency|communication)\b",
            r"\b(?:able to|can)\s+communicate\s+in\s+(?:Chinese|Mandarin)\b",
        ),
    }
    for pattern in descriptor_patterns.get(language, ()):
        match = re.search(pattern, text, re.I)
        if match:
            return clean_text(match.group(0))
    return None


def _foreign_languages(
    raw: dict,
    source: str,
    raw_skill_names: list[str],
) -> tuple[list[dict], dict]:
    requirement = _requirements_text(raw, source)
    requirement_units = _experience_units(requirement)
    result = []
    all_evidence = []
    sources = set()
    for language, pattern in _FOREIGN_LANGUAGE_PATTERNS:
        snippets = [unit for unit in requirement_units if re.search(pattern, _fold(unit))]
        skill_hits = [skill for skill in raw_skill_names if re.search(pattern, _fold(skill))]
        if not snippets and not skill_hits:
            continue
        if snippets:
            sources.add("requirements")
        if skill_hits:
            sources.add("skills")
        evidence = snippets + skill_hits
        all_evidence.extend(evidence)
        result.append({
            "language": language,
            "level_text": _language_level_text(language, evidence),
        })
    source_name = "+".join(sorted(sources)) if sources else None
    confidence = "high" if result else "none"
    if not result:
        result = [{"language": "unknown", "level_text": None}]
    return result, _meta_field(source_name, confidence, all_evidence)


def _education(raw: dict, source: str) -> list[dict]:
    value = raw.get("education_requirements_structured") if source == "itviec" else None
    text = _html_text(value)
    return [{"degree": clean_text(text), "majors": []}] if text else []


def _description_source(raw: dict, source: str) -> str:
    if source == "itviec":
        if raw.get("sections"):
            return "raw.sections"
        for field in (
            "job_description", "your_skills_and_experience",
            "why_youll_love_working_here", "top_reasons_to_join",
        ):
            if raw.get(field):
                return "raw." + field
    else:
        for field in (
            "responsibilities_original", "responsibilities", "descriptions", "description",
            "requirements_original", "requirements", "benefits_original", "benefits",
        ):
            if raw.get(field):
                return "raw." + field
    return "raw"


def _normalize_raw_record(
    record: dict,
    role_decision: RoleGateDecision,
) -> tuple[dict, dict]:
    if not role_decision.accepted:
        raise NormalizationError("Tin chưa vượt qua role gate.")
    if not isinstance(record, dict) or not isinstance(record.get("raw_payload"), dict):
        raise NormalizationError("Raw row phải chứa raw_payload dạng object.")
    raw = record["raw_payload"]
    source = clean_text(record.get("source") or raw.get("source"))
    if source not in {"itviec", "topdev"}:
        raise NormalizationError("Nguồn raw không được hỗ trợ.")
    job_id = clean_text(record.get("job_id"))
    crawl_run_id = clean_text(record.get("crawl_run_id"))
    job_url = _canonical_url(record.get("job_url") or raw.get("url") or raw.get("detail_url") or raw.get("job_url"))
    observed_at = normalized_datetime(record.get("observed_at") or raw.get("crawled_at") or raw.get("observed_at"))
    title = clean_text(raw.get("title"))
    if not all((job_id, crawl_run_id, job_url, observed_at, title)):
        raise NormalizationError("Thiếu job_id, crawl_run_id, job_url, observed_at hoặc title.")
    company = raw.get("company_name") if source == "itviec" else raw.get("company")
    company_source = "raw.company_name" if source == "itviec" else "raw.company"
    if isinstance(company, dict):
        company = company.get("display_name") or company.get("name")
    company = clean_text(company)
    description = _description(raw, source)
    location_text, cities, cities_meta = _locations(raw, source)
    published_value = raw.get("date_posted") if source == "itviec" else raw.get("published_at") or raw.get("created_at")
    experience, experience_meta = _experience(raw, source)
    seniority_levels, seniority_meta = _seniority(
        raw, source, title, experience, experience_meta,
    )
    job_roles, job_roles_meta = _job_roles(role_decision, title)
    work_mode, work_mode_meta = _work_mode(raw, source, description)
    skills, skills_meta, raw_skill_names = _skills(raw, source)
    foreign_languages, foreign_languages_meta = _foreign_languages(
        raw, source, raw_skill_names,
    )
    salary = _salary(raw, source)
    record_out = {
        "job_id": job_id, "source": source,
        "source_job_id": clean_text(record.get("source_job_id") or raw.get("source_job_id") or raw.get("id")),
        "job_url": job_url, "crawl_run_id": crawl_run_id, "observed_at": observed_at,
        "published_at": normalized_datetime(published_value, date_only=True),
        "title": title, "company_name": company,
        "description": description,
        "job_roles": job_roles,
        "seniority_levels": seniority_levels,
        "location_text": location_text, "cities": cities,
        "work_mode": work_mode, "skills": skills,
        "required_knowledge": [], **experience,
        "education_requirements": _education(raw, source),
        "foreign_languages": foreign_languages, "soft_skills": [],
        **salary,
    }
    record_out = {field: record_out[field] for field in NORMALIZED_FIELDS}
    validate_normalized_job(record_out)
    warnings = []
    if company is None:
        warnings.append("company_name is missing in source")
    if not seniority_levels:
        warnings.append("seniority_levels has no sufficiently clear evidence")
    if not cities:
        warnings.append("cities has no recognized Vietnamese city/province")
    metadata = {
        "posting_language": role_decision.language,
        "fields": {
            "title": _meta_field("raw.title", "high", [title]),
            "company_name": _meta_field(
                company_source if company else None,
                "high" if company else "none",
                [company] if company else (),
            ),
            "description": _meta_field(
                _description_source(raw, source), "high", [description],
            ),
            "job_roles": job_roles_meta,
            "seniority_levels": seniority_meta,
            "cities": cities_meta,
            "work_mode": work_mode_meta,
            "skills": skills_meta,
            "experience_text": experience_meta,
            "foreign_languages": foreign_languages_meta,
        },
        "warnings": warnings,
    }
    return record_out, metadata


def normalize_raw_record(record: dict, role_decision: RoleGateDecision) -> dict:
    """Trả về đúng 30 cột nghiệp vụ để giữ tương thích với staging cũ."""
    normalized, _ = _normalize_raw_record(record, role_decision)
    return normalized


def normalize_raw_record_with_meta(record: dict, role_decision: RoleGateDecision) -> dict:
    """Trả về hợp đồng đầy đủ để kiểm thử và phục vụ staging tương thích."""
    normalized, metadata = _normalize_raw_record(record, role_decision)
    result = {**normalized, NORMALIZATION_META_FIELD: metadata}
    validate_normalized_job_with_meta(result)
    return result


def normalize_raw_record_for_storage(record: dict, role_decision: RoleGateDecision) -> dict:
    """Trả về đúng hợp đồng lưu trữ của normalize_data phiên bản 5.1."""
    full = normalize_raw_record_with_meta(record, role_decision)
    original_meta = full[NORMALIZATION_META_FIELD]
    stored_fields = {
        field: value for field, value in original_meta["fields"].items()
        if field != "experience_text"
    }
    seniority_meta = dict(stored_fields["seniority_levels"])
    if seniority_meta.get("source") == "general_experience":
        seniority_meta["evidence"] = []
    stored_fields["seniority_levels"] = seniority_meta
    stored_meta = {**original_meta, "fields": stored_fields}
    result = {
        field: stored_meta if field == NORMALIZATION_META_FIELD else full[field]
        for field in PERSISTED_NORMALIZED_FIELDS
    }
    validate_normalized_storage_job(result)
    return result
