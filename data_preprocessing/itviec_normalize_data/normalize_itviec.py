#!/usr/bin/env python3
"""Làm sạch dữ liệu ITviec, ánh xạ 30 trường chung và lưu các bảng staging.

Luồng PostgreSQL: raw.job_postings -> làm sạch/ánh xạ -> staging.job_observations
và các bảng staging con; giữ nguyên JSON thô, thời điểm quan sát và crawl_run_id.
Đọc lại/đối chiếu dữ liệu trước khi mở gate staging.dwh_ready_jobs cho DWH.
Chế độ này cần psycopg[binary]>=3.1,<4, không xuất JSON/CSV ra đĩa:
    python normalize_itviec.py --crawl-run-id RUN_ID --db-name DATABASE --db-user USER

Chế độ file độc lập vẫn được hỗ trợ; Python >= 3.10, chỉ dùng thư viện chuẩn:
    python normalize_itviec.py --input itviec_data/raw/itviec_jobs_raw.json --output-dir itviec_data
    python normalize_itviec.py --input old_data/itviec_jobs.json --output-dir itviec_normalized
--normalize-file là tên thay thế cho --input. Toàn bộ tin trong file được xử lý.
Đầu ra file: itviec_jobs.json/csv đúng 30 trường, itviec_jobs_raw.json để đối chiếu,
và runs/<crawl_run_id>/ giữ từng lần quan sát. Không ghi đè file đầu vào.
Trường đơn chưa biết: null; danh sách chưa trích xuất được: [].
Bộ trích xuất theo quy tắc/từ điển; giữ raw để kiểm tra cách viết chưa bao phủ.
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
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LOG = logging.getLogger("itviec.staging")


class CrawlError(RuntimeError):
    """Dữ liệu đầu vào không đủ điều kiện chuẩn hóa."""


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()



def unique(values) -> list:
    return list(dict.fromkeys(v for v in values if v))



def key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower().replace("đ", "d"))
    return clean("".join(c for c in value if not unicodedata.combining(c))).rstrip(":")



def canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))



def is_itviec(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "itviec.com"



def regions_from_locations(locations) -> list[str]:
    """Đọc tỉnh/thành; addressLocality thường là quận nên không dùng làm thành phố."""
    if isinstance(locations, dict):
        locations = [locations]
    if not isinstance(locations, list):
        return []
    regions = []
    for location in locations:
        if not isinstance(location, dict):
            continue
        addresses = location.get("address") or []
        if isinstance(addresses, dict):
            addresses = [addresses]
        if not isinstance(addresses, list):
            continue
        for address in addresses:
            if not isinstance(address, dict):
                continue
            region = address.get("addressRegion")
            if isinstance(region, str) and clean(region) and key(region) not in {"not available", "n/a", "unknown"}:
                regions.append(clean(region))
    return unique(regions)



def infer_seniority(title: str) -> list[str]:
    normalized = key(title)
    patterns = [
        ("All levels", r"\ball\s+levels?\b|\bmoi cap do\b"),
        ("Internship", r"\bintern(?:ship)?\b|\bthuc tap\b"),
        ("Fresher", r"\bfresher\b"),
        ("Junior", r"\bjunior\b|\bjr\b"),
        ("Middle", r"\bmiddle\b|\bmid(?:\s*[- ]\s*level)?\b"),
        ("Senior", r"\bsenior\b|\bsr\b"),
        ("Lead", r"\blead(?:er)?\b"),
        ("Manager", r"\bmanager\b|\bmanagement\b|\btruong phong\b"),
        ("Director", r"\bdirector\b|\bgiam doc\b"),
    ]
    return [name for name, pattern in patterns if re.search(pattern, normalized)]



_SE_CURRENCY = r"(?:VND|VNĐ|USD|EUR|GBP|JPY|AUD|SGD|CAD|đồng|dong|₫|đ|\$|€|£)"
_SE_SCALE = r"(?:triệu|tr\.?|million|millions|nghìn|ngàn|thousand|k)"
_SE_AMOUNT = r"\d+(?:[.,]\d+)*(?:[ \u00a0]+\d{3})*"
_SE_UNIT = r"(?:years?|yrs?|năm|months?|mos?|tháng)"
_SE_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
    "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
    "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
}


def _se_text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def _se_numeric(value, allow_zero=False):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str) and re.fullmatch(r"\s*" + _SE_AMOUNT + r"\s*", value):
        value = re.sub(r"\s+", "", value)
        if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", value):
            value = re.sub(r"[.,]", "", value)
        elif "." in value and "," in value:
            decimal_mark = "." if value.rfind(".") > value.rfind(",") else ","
            value = value.replace("," if decimal_mark == "." else ".", "")
            value = value.replace(decimal_mark, ".")
        else:
            value = value.replace(",", ".")
        try:
            result = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero):
        return None
    return int(result) if result.is_integer() else result


def _se_scale(value):
    if not value:
        return 1
    return 1_000_000 if re.fullmatch(r"triệu|tr\.?|millions?", value, re.I) else 1_000


def _se_currency(text):
    currencies = _se_currencies(text)
    return next(iter(currencies)) if len(currencies) == 1 else None


def _se_currencies(text):
    currencies = set()
    # Unicode letters matter: the 'đ' in 'đến' is not a VND marker.
    for match in re.finditer(r"(?<![^\W\d_])" + _SE_CURRENCY + r"(?![^\W\d_])", text, re.I):
        value = match.group().upper()
        if value in {"VND", "VNĐ", "ĐỒNG", "DONG", "₫", "Đ"}:
            value = "VND"
        # A bare dollar sign does not distinguish USD, SGD, AUD, CAD, etc.
        value = {"$": None, "€": "EUR", "£": "GBP"}.get(value, value)
        if value:
            currencies.add(value)
    return currencies


def _se_period(text):
    text = text.strip()
    for period, pattern in (
        ("month", r"\b(?:month(?:ly)?|tháng)\b"),
        ("year", r"\b(?:year(?:ly)?|annum|annual|năm)\b"),
        ("hour", r"\b(?:hour(?:ly)?|giờ)\b"),
        ("day", r"\b(?:day|daily|ngày)\b"),
        ("week", r"\b(?:week(?:ly)?|tuần)\b"),
    ):
        match = re.search(pattern, text, re.I)
        if match and (match.span() == (0, len(text)) or
                      re.search(r"(?:/|per\s+|a\s+|mỗi\s+|hàng\s+)" + pattern, text, re.I) or
                      re.search(r"\b(?:monthly|yearly|hourly|daily|weekly)\b", text, re.I) or
                      (period == "year" and re.search(r"\bannual\s+salary\b", text, re.I))):
            return period
    return None


def _se_salary_basis(text):
    gross = re.search(r"\bgross\b|trước thuế|\bbefore[- ]tax\b|\bpre[- ]tax\b", text, re.I)
    net = re.search(r"\bnet\b|sau thuế|\bafter[- ]tax\b|\bpost[- ]tax\b|\btake[- ‐‑–]?home\b", text, re.I)
    if bool(gross) == bool(net):
        return None
    return "gross" if gross else "net"


def _se_salary_bounds(text):
    """Parse monetary text only when a currency or monetary scale is explicit."""
    if len(_se_currencies(text)) > 1:
        return None, None
    if not re.search(r"(?<!\w)(?:" + _SE_CURRENCY + "|" + _SE_SCALE + r")(?!\w)", text, re.I):
        # Currency/scale can be directly adjacent to digits, e.g. $2000/20tr.
        if not re.search(r"\$\s*\d|\d\s*(?:" + _SE_CURRENCY + "|" + _SE_SCALE + r")(?!\w)", text, re.I):
            return None, None
    amount = (r"(?:" + _SE_CURRENCY + r"\s*)?(?P<n{index}>" + _SE_AMOUNT
              + r")\s*(?P<s{index}>" + _SE_SCALE + r")?\s*(?:" + _SE_CURRENCY + r")?")
    range_pattern = amount.replace("{index}", "1") + r"\s*(?:[-–—~]|\bto\b|đến|tới)\s*" + amount.replace("{index}", "2")
    match = next((item for item in re.finditer(range_pattern, text, re.I)
                  if item["s1"] or item["s2"] or re.search(_SE_CURRENCY, item.group(), re.I)), None)
    if match:
        remainder = text[:match.start()] + text[match.end():]
        if any(item["s1"] or re.search(_SE_CURRENCY, item.group(), re.I)
               for item in re.finditer(amount.replace("{index}", "1"), remainder, re.I)):
            return None, None
        first, second = _se_numeric(match["n1"]), _se_numeric(match["n2"])
        if first is None or second is None:
            return None, None
        scale1, scale2 = match["s1"] or match["s2"], match["s2"] or match["s1"]
        first, second = first * _se_scale(scale1), second * _se_scale(scale2)
        return (first, second) if first <= second else (None, None)
    amounts = [item for item in re.finditer(amount.replace("{index}", "1"), text, re.I)
               if item["s1"] or re.search(_SE_CURRENCY, item.group(), re.I)]
    if len(amounts) != 1:
        return None, None
    match = amounts[0]
    value = _se_numeric(match["n1"])
    if value is None:
        return None, None
    value *= _se_scale(match["s1"])
    prefix = text[:match.start()].strip()
    suffix = text[match.end():].strip()
    if re.search(r"(?:up\s*to|maximum|max\.?|under|below|tối đa|lên tới|đến|dưới)\s*$", prefix, re.I):
        return None, value
    if re.search(r"(?:from|starting(?:\s+at)?|at\s+least|minimum|min\.?|over|more\s+than|từ|tối thiểu|trên|ít nhất)\s*$", prefix, re.I) or suffix.startswith("+"):
        return value, None
    return value, value


def normalize_salary(raw):
    """Return the seven salary_* schema keys without altering the raw row."""
    salary = raw.get("salary")
    if isinstance(salary, str):
        salary = {"text": salary}
    salary = salary if isinstance(salary, dict) else {}
    text = _se_text(salary.get("text"))
    structured = salary.get("structured")
    structured = structured if isinstance(structured, dict) else {}
    value = structured.get("value")
    quantity = value if isinstance(value, dict) else {"value": value}
    fallback_text = _se_text(quantity.get("value"))
    result = {
        "salary_text": text or fallback_text,
        "salary_min": None, "salary_max": None, "salary_currency": None,
        "salary_period": None, "salary_basis": None, "salary_status": "unknown",
    }
    visibility = str(salary.get("visibility") or "").lower()
    if visibility == "login_required" or re.search(r"sign\s*in.*salary|log\s*in.*salary|đăng nhập.*lương", text or "", re.I):
        return result
    salary_text = text or fallback_text or ""
    result["salary_currency"] = _se_currency(salary_text)
    result["salary_period"] = _se_period(salary_text)
    result["salary_basis"] = _se_salary_basis(salary_text)
    low, high = _se_salary_bounds(salary_text)
    monetary_narrative = bool(re.search(r"\d", salary_text) and
                              re.search(_SE_CURRENCY + "|" + _SE_SCALE, salary_text, re.I))
    if low is None and high is None and monetary_narrative:
        # Do not replace contradictory currencies, separate offers or an
        # invalid displayed range with possibly stale JSON-LD numbers.
        return result
    # Explicit displayed nonnumeric statements also take precedence over
    # JSON-LD, which can contain placeholders or stale/disagreeing numbers.
    if low is None and high is None and re.search(r"negotiab(?:le|ility)|th(?:[oỏ]a|oả)\s*thu[aậ]n", salary_text, re.I):
        result["salary_status"] = "negotiable"
        return result
    if low is None and high is None and re.search(r"you['’]ll love it|competitive|attractive|not disclosed|undisclosed|not published|confidential|không công bố|bảo mật|hấp dẫn|cạnh tranh", salary_text, re.I):
        result["salary_status"] = "not_disclosed"
        return result
    if low is None and high is None:
        low, high = _se_numeric(quantity.get("minValue")), _se_numeric(quantity.get("maxValue"))
        scalar = _se_numeric(quantity.get("value"))
        if low is None and high is None and scalar is not None:
            low = high = scalar
        if low is not None and high is not None and low > high:
            low = high = None
    if low is not None or high is not None:
        result["salary_min"], result["salary_max"] = low, high
        result["salary_status"] = "disclosed"
        if result["salary_currency"] is None:
            currency = _se_text(structured.get("currency"))
            if currency and re.fullmatch(r"[A-Za-z]{3}", currency):
                result["salary_currency"] = currency.upper()
        result["salary_period"] = _se_period(salary_text) or _se_period(str(quantity.get("unitText") or ""))
    return result


def _se_replace_number_words(text):
    # Restrict replacements to words followed by a duration unit.  Vietnamese
    # 'năm' can mean five or year, and must not be replaced indiscriminately.
    text = re.sub(r"\bone and a half(?=\s+" + _SE_UNIT + r"\b)", "1.5", text, flags=re.I)
    word_pattern = "|".join(sorted(map(re.escape, _SE_WORD_NUMBERS), key=len, reverse=True))
    pattern = r"\b(" + word_pattern + r")(?=\s*(?:\+?\s*" + _SE_UNIT + r"\b|[-–—]|\bto\b|\band\b|đến|tới|và))"
    return re.sub(pattern, lambda match: str(_SE_WORD_NUMBERS[match.group(1).lower()]), text, flags=re.I)


def _se_years(number, unit):
    value = _se_numeric(number, allow_zero=True)
    if value is None:
        return None
    years = value / 12 if re.fullmatch(r"months?|mos?|tháng", unit, re.I) else value
    return int(years) if float(years).is_integer() else round(years, 6)


def _se_alternative_experience(text):
    text = _se_replace_number_words(text)
    durations = re.findall(r"\d+(?:[.,]\d+)?\s*\+?\s*" + _SE_UNIT + r"\b", text, re.I)
    if len(durations) < 2:
        return False
    return bool(re.search(r"\b(?:or|hoặc)\s+(?:(?:at least|up to|minimum|maximum|có|tối thiểu|tối đa)\s+)?\d", text, re.I) or
                (re.search(r"\beither\b", text, re.I) and re.search(r"\bor\b", text, re.I)))


def _se_experience_candidates(line):
    text = _se_replace_number_words(line)
    if not re.search(r"\bexperienc\w*\b|kinh nghiệm|\bworking\s+(?:as|in)\b|\bexpertise\b", text, re.I):
        return []
    # A preferred qualification or a descriptive example is evidence, but
    # does not establish a mandatory minimum for this normalized field.
    if re.search(r"\btypically\b|\busually\b|\bon average\b|\bpreferred\b|\bpreferably\b|\bideally\b|nice[- ]to[- ]have|\ba plus\b|\ban advantage\b|lợi thế|ưu tiên|khuyến khích|không bắt buộc|not (?:required|mandatory)", text, re.I):
        return []
    if re.search(r"(?:our company|we have|we bring|chúng tôi|công ty chúng tôi).{0,60}\d+\s*" + _SE_UNIT, text, re.I):
        return []
    number = r"\d+(?:[.,]\d+)?"
    if _se_alternative_experience(text):
        # Alternative qualifications cannot be represented as a single
        # mandatory range without inventing a requirement.
        return []
    range_pattern = (r"(?P<a>" + number + r")\s*(?P<u1>" + _SE_UNIT + r")?\s*"
                     + r"(?:[-–—~]|\bto\b|\band\b|đến|tới|và)\s*(?P<b>" + number + r")\s*(?P<u2>" + _SE_UNIT + r")\b")
    candidates, spans = [], []
    for match in re.finditer(range_pattern, text, re.I):
        # Years since graduation or a person's age are not experience.
        prefix = text[max(0, match.start()-35):match.start()]
        if re.search(r"graduat\w*|tốt nghiệp|sinh viên|age[d]?|tuổi|bonus|insurance|annual leave|founded|established|thành lập|bảo hiểm|nghỉ phép", prefix, re.I):
            continue
        first = _se_years(match["a"], match["u1"] or match["u2"])
        second = _se_years(match["b"], match["u2"])
        if first is not None and second is not None and first > second:
            return []
        if first is not None and second is not None and first <= second:
            candidates.append((first, second))
            spans.append(match.span())
    composite = (r"(?P<years>" + number + r")\s*(?:years?|yrs?|năm)\s*"
                 + r"(?:and\s+|và\s+)?(?P<months>" + number + r")\s*(?:months?|mos?|tháng)\b")
    for match in re.finditer(composite, text, re.I):
        if any(start <= match.start() < end for start, end in spans):
            continue
        years = _se_years(match["years"], "year")
        months = _se_years(match["months"], "month")
        if years is not None and months is not None:
            candidates.append((round(years + months, 6), None))
            spans.append(match.span())
    for match in re.finditer(r"(?P<n>" + number + r")\s*\+?\s*(?P<u>" + _SE_UNIT + r")\b\s*\+?", text, re.I):
        if any(start <= match.start() < end for start, end in spans):
            continue
        prefix = text[max(0, match.start()-45):match.start()]
        if re.search(r"graduat\w*|tốt nghiệp|sinh viên|age[d]?|tuổi|bonus|insurance|annual leave|founded|established|thành lập|bảo hiểm|nghỉ phép", prefix, re.I):
            continue
        value = _se_years(match["n"], match["u"])
        if value is None:
            continue
        if value == 0 and not _se_global_no_experience(text):
            continue
        if re.search(r"(?:up\s*to|maximum|max\.?|under|below|tối đa|dưới|không quá)\s*$", prefix, re.I):
            candidates.append((None, value))
        elif re.search(r"(?:exactly|đúng)\s*$", prefix, re.I):
            candidates.append((value, value))
        else:
            candidates.append((value, None))
    return candidates


def _se_global_no_experience(line):
    """Recognize a waiver for the role, not a waiver for one technology."""
    text = key(line).strip(" .!;:-–—*•●○◦")
    english = (r"no\s+(?:(?:prior|previous|work|professional)\s+)*experience"
               r"\s+(?:is\s+)?(?:required|necessary)")
    vietnamese = r"(?:khong\s+(?:yeu cau|can)\s+(?:co\s+)?kinh nghiem|chua\s+can\s+kinh nghiem)"
    numeric = r"0\s+(?:years?|yrs?|nam|months?|mos?|thang)\s+(?:of\s+)?(?:work\s+)?experience(?:\s+(?:is\s+)?required)?"
    match = re.fullmatch(r"(?:" + english + "|" + vietnamese + "|" + numeric +
                         r")(?:\s+(?:at all|for (?:this|the) (?:role|position)|lam viec))?", text)
    return bool(match)


def _se_experience_clauses(requirements):
    """Join visual line wraps and keep preference scope within each clause."""
    blocks, current = [], []
    preferred = False

    def flush():
        if current:
            blocks.append((" ".join(current), preferred))
            current.clear()

    for original in requirements.splitlines():
        line = original.strip()
        if not line:
            flush()
            continue
        folded = key(line)
        heading = bool(_se_experience_label(line) or re.fullmatch(
            r"(?:required|desired|preferred|minimum|basic|other) (?:qualifications|requirements)|"
            r"nice[- ]to[- ]have|experience|kinh nghiem(?: lam viec)?|"
            r"yeu cau(?: bat buoc)?|uu tien|loi the|technical skills|soft skills|education", folded))
        if heading or (line.endswith(":") and not re.search(r"experienc\w*|kinh nghiệm", line, re.I)):
            flush()
            preferred = bool(re.search(r"desired|preferred|nice[- ]to[- ]have|uu tien|loi the", folded))
            continue
        bullet = re.match(r"^(?:[-*+•●○◦▪–—]|\d+[.)])\s+", line)
        if bullet:
            flush()
            line = line[bullet.end():]
        current.append(line)
    flush()
    for block, preferred in blocks:
        for sentence in re.split(r"(?<=[.!?;])\s+(?=[\wÀ-ỹ])", block):
            has_experience = bool(re.search(r"\bexperienc\w*\b|kinh nghiệm|\bworking\s+(?:as|in)\b|\bexpertise\b", sentence, re.I))
            if has_experience and _se_alternative_experience(sentence):
                yield sentence, sentence, preferred
                continue
            # Commas do not separate a duration range or a decimal, but do
            # separate two independently qualified requirements in prose.
            for clause in re.split(r",\s+", sentence):
                parts = [clause]
                conjunction = re.search(r"\s+(?:and|but|whereas|nhưng|còn)\s+(?=(?:at least\s+|up to\s+)?\d+)", clause, re.I)
                if conjunction and re.search(r"experienc\w*|kinh nghiệm", clause[:conjunction.start()], re.I):
                    parts = [clause[:conjunction.start()], clause[conjunction.end():]]
                for part in parts:
                    secondary_preference = re.search(
                        r"\b(?:ưu tiên|khuyến khích)\s+(?:(?:ứng viên|những người)\s+)?(?:có|với|đã)\b|"
                        r"\b(?:preferably|ideally)\s+(?:with|having)\b|"
                        r"\bpreference\s+(?:is\s+)?given\s+to\b", part, re.I)
                    if secondary_preference:
                        primary = part[:secondary_preference.start()].strip()
                        if (re.search(r"experienc\w*|kinh nghiệm", primary, re.I) and
                                re.search(r"\d+(?:[.,]\d+)?\s*\+?\s*" + _SE_UNIT + r"\b", _se_replace_number_words(primary), re.I)):
                            # A preferred domain/technology appended to a
                            # mandatory duration does not waive that duration.
                            yield primary, primary, preferred
                            secondary = part[secondary_preference.start():].strip()
                            yield secondary, secondary, True
                            continue
                    original_part = part.strip()
                    if not original_part:
                        continue
                    parsed = original_part
                    if has_experience and not re.search(r"experienc\w*|kinh nghiệm", parsed, re.I):
                        parsed += " experience"
                    yield original_part, parsed, preferred


def _se_experience_label(line):
    label = re.sub(r"^(?:[-*•●○◦–—]+|\d+[.)])\s*", "", line.strip()).strip().rstrip(":").strip()
    return bool(re.fullmatch(r"(?:work\s+)?experience|kinh nghiệm(?: làm việc)?", label, re.I))


def _se_experience_evidence(requirements):
    """Preserve original bullet text, abbreviations, and wrapped continuations.

    Evidence grouping is independent of the numeric sentence parser, so e.g.
    does not truncate an original bullet and line wrapping does not drop its
    object.  Headings and adjacent unrelated bullets form separate blocks.
    """
    blocks, current = [], []
    parent_indent, parent_marker = None, None

    def flush():
        if current:
            blocks.append("\n".join(current))
            current.clear()

    for original in requirements.splitlines():
        line = original.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            parent_indent = parent_marker = None
            continue
        bullet = re.match(r"^(\s*)([-*+•●○◦▪–—]|\d+[.)])\s+", line)
        heading = (_se_experience_label(stripped) or
                   bool(re.fullmatch(r"[^.!?]+:", stripped) and
                        not re.search(r"experienc\w*|kinh nghiệm", stripped, re.I)) or
                   bool(re.fullmatch(r"Education|Background|Technical Skills?|Other Requirements|Required Qualifications|Desired Qualifications|Soft Skills|Language Skills", stripped, re.I)))
        if heading:
            flush()
            parent_indent = parent_marker = None
            continue
        if re.match(r"Given the high volume|We appreciate|Please (?:submit|send|apply)|Ứng viên vui lòng|MB Bank yêu cầu", stripped, re.I):
            flush()
            parent_indent = parent_marker = None
        if bullet:
            indent, marker = len(bullet.group(1)), bullet.group(2)
            nested = bool(current and parent_marker is not None and
                          (indent > parent_indent or
                           (marker in {"○", "◦", "+"} and parent_marker not in {"○", "◦", "+"})))
            if not nested:
                flush()
                parent_indent, parent_marker = indent, marker
        current.append(line)
    flush()
    evidence = []
    for block in blocks:
        if re.search(r"\bexperienc\w*\b|kinh nghiệm|\bworking\s+(?:as|in)\b", block, re.I):
            if block not in evidence:
                evidence.append(block)
    return "\n".join(evidence) or None


def normalize_experience(raw):
    """Return original evidence plus numeric duration bounds in years.

    Multiple conjunctive requirements use the strongest minimum, without
    summing overlapping experience.  A numeric narrative wins over JSON-LD.
    Months are divided by 12 (six decimal digits); neither title nor benefits
    nor company history is used to infer experience.
    """
    requirements = _se_text(raw.get("your_skills_and_experience")) or ""
    candidates = []
    explicit_zero = False
    has_numeric_narrative = False
    for original_line, line, preferred in _se_experience_clauses(requirements):
        # A section heading or a CV checklist label is not a requirement.
        if _se_experience_label(line):
            continue
        has_numeric_narrative |= bool(re.search(r"\bexperienc\w*\b|kinh nghiệm|\bworking\s+(?:as|in)\b|\bexpertise\b", line, re.I) and
                                      re.search(r"\d+(?:[.,]\d+)?\s*\+?\s*" + _SE_UNIT + r"\b", _se_replace_number_words(line), re.I))
        if preferred:
            continue
        if _se_global_no_experience(original_line):
            explicit_zero = True
        candidates.extend(_se_experience_candidates(line))
    result = {"experience_text": _se_experience_evidence(requirements),
              "experience_min_years": None, "experience_max_years": None}
    if explicit_zero and not any(minimum is not None and minimum > 0 for minimum, _ in candidates):
        candidates.append((0, None))
    elif explicit_zero:
        # Conflicting global statements are retained for review rather than
        # silently selecting a numerical requirement.
        return result
    if candidates:
        lower = [minimum for minimum, _ in candidates if minimum is not None]
        upper = [maximum for _, maximum in candidates if maximum is not None]
        minimum = max(lower) if lower else None
        maximum = min(upper) if upper else None
        if minimum is None or maximum is None or minimum <= maximum:
            result["experience_min_years"] = minimum
            result["experience_max_years"] = maximum
        return result
    if has_numeric_narrative:
        # Preferred, alternative or contradictory prose must not turn into
        # a mandatory requirement through JSON-LD fallback.
        return result
    structured = raw.get("experience_requirements_structured")
    entries = structured if isinstance(structured, list) else [structured]
    months = [_se_numeric(item.get("monthsOfExperience"), allow_zero=True)
              for item in entries if isinstance(item, dict)]
    # ITviec's 10/37/61 values encode UI experience buckets; they are not exact
    # month requirements and must not become false-precise year estimates.
    months = [value for value in months if value is not None and value not in (10, 37, 61)]
    if months:
        value = max(months) / 12
        result["experience_min_years"] = int(value) if value.is_integer() else round(value, 6)
        original = json.dumps(structured, ensure_ascii=False, sort_keys=True)
        result["experience_text"] = result["experience_text"] or original
    elif isinstance(structured, str) and _se_text(structured):
        result["experience_text"] = result["experience_text"] or structured.strip()
        if re.search(r"\b" + _SE_UNIT + r"\b", structured, re.I):
            parsed = normalize_experience({"your_skills_and_experience": structured + " experience"})
            result["experience_min_years"] = parsed["experience_min_years"]
            result["experience_max_years"] = parsed["experience_max_years"]
    elif isinstance(structured, (dict, list)):
        original = json.dumps(structured, ensure_ascii=False, sort_keys=True)
        result["experience_text"] = result["experience_text"] or original
    return result


def req_fold(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D").lower()


def req_clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


# Aliases are complete technology names, rather than fuzzy substring matches.
REQ_SKILL_VOCABULARY = [
    ("Python", "programming_language", ["python"]),
    ("Java", "programming_language", ["java"]),
    ("JavaScript", "programming_language", ["javascript", "java script", "js"]),
    ("TypeScript", "programming_language", ["typescript", "type script"]),
    ("Go", "programming_language", ["golang", "go"]),
    ("C++", "programming_language", ["c++"]),
    ("C#", "programming_language", ["c#", "c sharp"]),
    ("C", "programming_language", ["c"]),
    ("PHP", "programming_language", ["php"]),
    ("Ruby", "programming_language", ["ruby"]),
    ("R", "programming_language", ["r", "rlang", "r language", "r programming language", "r programming"]),
    ("Kotlin", "programming_language", ["kotlin"]),
    ("Swift", "programming_language", ["swift"]),
    ("Rust", "programming_language", ["rust"]),
    ("Scala", "programming_language", ["scala"]),
    ("Dart", "programming_language", ["dart"]),
    ("SQL", "query_language", ["sql"]),
    ("HTML", "markup_language", ["html", "html5"]),
    ("CSS", "stylesheet_language", ["css", "css3"]),
    ("Bash", "programming_language", ["bash"]),
    ("PowerShell", "programming_language", ["powershell"]),
    ("Spring Boot", "framework", ["spring boot", "springboot"]),
    ("Spring", "framework", ["spring framework", "spring"]),
    ("ASP.NET", "framework", ["asp.net", "asp net"]),
    (".NET", "framework", [".net", "dotnet"]),
    ("React", "framework", ["reactjs", "react.js", "react"]),
    ("Next.js", "framework", ["nextjs", "next.js"]),
    ("Angular", "framework", ["angularjs", "angular.js", "angular"]),
    ("jQuery", "framework", ["jquery"]),
    ("Bootstrap", "framework", ["bootstrap"]),
    ("Vue.js", "framework", ["vuejs", "vue.js", "vue"]),
    ("NestJS", "framework", ["nestjs", "nest.js"]),
    ("Node.js", "platform", ["nodejs", "node.js", "node js"]),
    ("Django", "framework", ["django"]),
    ("Flask", "framework", ["flask"]),
    ("FastAPI", "framework", ["fastapi", "fast api"]),
    ("Laravel", "framework", ["laravel"]),
    ("Ruby on Rails", "framework", ["ruby on rails", "rails"]),
    ("Flutter", "framework", ["flutter"]),
    ("React Native", "framework", ["react native"]),
    ("PyTorch", "framework", ["pytorch"]),
    ("TensorFlow", "framework", ["tensorflow", "tensor flow"]),
    ("scikit-learn", "framework", ["scikit-learn", "sklearn"]),
    ("LangChain", "framework", ["langchain"]),
    ("LangGraph", "framework", ["langgraph"]),
    ("AutoGen", "framework", ["autogen"]),
    ("DGL", "framework", ["dgl"]),
    ("PyG", "framework", ["pyg"]),
    ("PostgreSQL", "database", ["postgresql", "postgres", "pgsql"]),
    ("MySQL", "database", ["mysql"]),
    ("SQL Server", "database", ["microsoft sql server", "sql server", "sqlserver", "mssql"]),
    ("MongoDB", "database", ["mongodb", "mongo db"]),
    ("Oracle", "database", ["oracle database", "oracle db", "oracle"]),
    ("Redis", "database", ["redis"]),
    ("Cassandra", "database", ["cassandra"]),
    ("Elasticsearch", "database", ["elasticsearch", "elastic search"]),
    ("SQLite", "database", ["sqlite"]),
    ("MariaDB", "database", ["mariadb"]),
    ("Milvus", "database", ["milvus"]),
    ("pgvector", "database", ["pgvector"]),
    ("NoSQL", "database", ["nosql", "no sql"]),
    ("Docker", "tool", ["docker"]),
    ("Kubernetes", "platform", ["kubernetes", "kubernete", "k8s"]),
    ("Terraform", "tool", ["terraform"]),
    ("Ansible", "tool", ["ansible"]),
    ("Git", "tool", ["git"]),
    ("GitFlow", "methodology", ["gitflow", "git flow"]),
    ("GitHub Actions", "tool", ["github actions"]),
    ("GitHub", "platform", ["github"]),
    ("GitLab", "platform", ["gitlab"]),
    ("Jenkins", "tool", ["jenkins"]),
    ("Argo CD", "tool", ["argocd", "argo cd"]),
    ("Kafka", "platform", ["apache kafka", "kafka"]),
    ("RabbitMQ", "platform", ["rabbitmq"]),
    ("MLflow", "tool", ["mlflow"]),
    ("Kubeflow", "platform", ["kubeflow"]),
    ("ONNX", "tool", ["onnx"]),
    ("TensorFlow Serving", "tool", ["tensorflow serving"]),
    ("Triton Inference Server", "tool", ["triton inference server", "tritonserver", "triton server"]),
    ("AWS", "platform", ["amazon web services", "aws"]),
    ("GCP", "platform", ["google cloud platform", "google cloud", "gcp"]),
    ("Azure", "platform", ["microsoft azure", "azure"]),
    ("Google Kubernetes Engine", "platform", ["google kubernetes engine", "gke"]),
    ("AWS CloudFormation", "tool", ["aws cloudformation", "cloudformation"]),
    ("AWS CDK", "tool", ["aws cdk"]),
    ("Linux", "platform", ["linux"]),
    ("Ubuntu", "platform", ["ubuntu"]),
    ("CentOS", "platform", ["centos"]),
    ("Windows", "platform", ["windows"]),
    ("Android", "platform", ["android"]),
    ("iOS", "platform", ["ios"]),
    ("Figma", "tool", ["figma"]),
    ("FigJam", "tool", ["figjam"]),
    ("Sketch", "tool", ["sketch"]),
    ("Adobe XD", "tool", ["adobe xd"]),
    ("Jira", "tool", ["jira"]),
    ("Trello", "tool", ["trello"]),
    ("Notion", "tool", ["notion"]),
    ("Confluence", "tool", ["confluence"]),
    ("Lucidchart", "tool", ["lucidchart"]),
    ("GitHub Copilot", "tool", ["github copilot", "copilot"]),
    ("Cursor", "tool", ["cursor"]),
    ("ChatGPT", "tool", ["chatgpt"]),
    ("Claude", "tool", ["claude"]),
    ("Gemini", "tool", ["gemini"]),
    ("REST API", "architecture", ["restful api", "rest apis", "rest api", "restful"]),
    ("gRPC", "architecture", ["grpc"]),
    ("Microservices", "architecture", ["microservices", "microservice", "microservice architecture"]),
    ("Software Architecture", "architecture", ["software architecture"]),
    ("System Architecture", "architecture", ["system architecture"]),
    ("CI/CD", "methodology", ["ci/cd", "ci / cd", "continuous integration", "continuous delivery"]),
    ("Agile", "methodology", ["agile"]),
    ("Scrum", "methodology", ["scrum"]),
    ("Kanban", "methodology", ["kanban"]),
    ("Waterfall", "methodology", ["waterfall"]),
    ("DevOps", "methodology", ["devops"]),
    ("DevSecOps", "methodology", ["devsecops"]),
    ("MLOps", "methodology", ["mlops"]),
    ("Unit Testing", "methodology", ["unit testing", "unit test"]),
    ("Integration Testing", "methodology", ["integration testing", "integration test"]),
    ("Test Automation", "methodology", ["test automation", "automation test", "automated testing"]),
    ("AI", "domain", ["artificial intelligence", "tri tue nhan tao", "ai"]),
    ("Machine Learning", "domain", ["machine learning", "ml"]),
    ("Deep Learning", "domain", ["deep learning"]),
    ("Computer Vision", "domain", ["computer vision"]),
    ("NLP", "domain", ["natural language processing", "nlp"]),
    ("LLM", "domain", ["large language models", "large language model", "llms", "llm"]),
    ("RAG", "architecture", ["retrieval augmented generation", "retrieval-augmented generation", "rag"]),
    ("UI/UX", "domain", ["ui/ux", "ui-ux", "ux/ui"]),
]

REQ_LANGUAGE_ALIASES = [
    ("English", r"\b(?:english|tieng anh|anh ngu|toeic|ielts|toefl)\b"),
    ("Japanese", r"\b(?:japanese|tieng nhat|nhat ngu|jlpt)\b"),
    ("Korean", r"\b(?:korean|tieng han|han ngu|topik)\b"),
    ("Chinese", r"\b(?:chinese|mandarin|tieng trung|trung quoc|hoa ngu|hsk)\b"),
    ("French", r"\b(?:french|tieng phap)\b"),
    ("German", r"\b(?:german|tieng duc)\b"),
    ("Spanish", r"\b(?:spanish|tieng tay ban nha)\b"),
    ("Russian", r"\b(?:russian|tieng nga)\b"),
]

REQ_SOFT_SKILLS = [
    ("Communication", r"\b(?:communication|communicate|giao tiep|truyen dat)\b"),
    ("Teamwork", r"\b(?:teamwork|team work|team player|collaboration|collaborat\w*|lam viec nhom|hop tac|phoi hop)\b"),
    ("Problem Solving", r"\b(?:problem[ -]solving|giai quyet van de)\b"),
    ("Leadership", r"\b(?:leadership|lanh dao|leading (?:a |the )?(?:software development )?teams?|lead teams?)\b"),
    ("Analytical Thinking", r"\b(?:analytical|critical thinking|tu duy phan tich|tu duy phan bien|ky nang phan tich)\b"),
    ("Attention to Detail", r"\b(?:attention to detail|detail[ -]oriented|can than|chu y den chi tiet)\b"),
    ("Adaptability", r"\b(?:adaptability|adaptable|thich nghi|linh hoat)\b"),
    ("Time Management", r"\b(?:time management|quan ly thoi gian)\b"),
    ("Self Learning", r"\b(?:self[ -]learning|self[ -]study|tu hoc|willing(?:ness)? to learn|passion(?:ate)? (?:about|for) learning|ham hoc hoi)\b"),
    ("Mentoring", r"\b(?:mentor(?:ing)?|coaching|huong dan dong nghiep)\b"),
    ("Ownership", r"\b(?:ownership|accountability|tinh than trach nhiem)\b"),
    ("Decision Making", r"\b(?:decision[ -]making|ra quyet dinh)\b"),
    ("Conflict Resolution", r"\b(?:conflict (?:resolution|management)|quan ly xung dot|giai quyet xung dot)\b"),
    ("Stakeholder Management", r"\b(?:stakeholder management|quan ly cac ben lien quan)\b"),
    ("Presentation", r"\b(?:presentation skills?|thuyet trinh)\b"),
    ("Work Planning", r"\b(?:work planning|lap ke hoach)\b"),
]


def req_pattern(alias):
    return re.compile(r"(?<![\w])" + re.escape(req_fold(alias)) + r"(?![\w])")


REQ_SKILL_PATTERNS = [(name, category, [req_pattern(a) for a in aliases]) for name, category, aliases in REQ_SKILL_VOCABULARY]
REQ_PREFERRED = re.compile(r"\b(?:preferred|preferable|nice[ -]to[ -]have|desir(?:ed|able)|advantage(?:ous)?|bonus|a plus|uu tien|loi the|diem cong)\b")
REQ_REQUIRED = re.compile(r"\b(?:required|mandatory|must(?:[ -]have)?|essential|compulsory|bat buoc|yeu cau|can co)\b")
REQ_NEGATED = re.compile(
    r"\b(?:not(?: strictly)? (?:required|mandatory|essential|compulsory|preferred)|"
    r"no [^,;.!?\n]{0,60} required|(?:do(?:es)? not|don['’]t|doesn['’]t) (?:need|require)|"
    r"khong (?:yeu cau|bat buoc|can(?: co)?))\b")
REQ_DEGREE_PATTERNS = [
    ("doctorate", r"\b(?:ph\.?d\.?|doctorate|doctoral|tien si)\b"),
    ("master", r"\b(?:master(?:[’']s)?(?: degree)?|m\.?sc\.?|thac si)\b"),
    ("bachelor", r"\b(?:bachelor(?:[’']s)?(?: degree)?|b\.?sc\.?|dai hoc|cu nhan|ky su)\b"),
    ("associate", r"\b(?:associate(?:[’']s)?(?: degree)?|cao dang)\b"),
    ("high_school", r"\b(?:high school|trung hoc pho thong|thpt)\b"),
]


def req_requirement_type(text, inherited="unspecified"):
    text = req_fold(text)
    if REQ_NEGATED.search(text):
        # An explicit preference can still coexist with "not mandatory".
        positive = REQ_NEGATED.sub(" ", text)
        return "preferred" if REQ_PREFERRED.search(positive) else "unspecified"
    if REQ_PREFERRED.search(text):
        return "preferred"
    if REQ_REQUIRED.search(text):
        return "required"
    return inherited


def req_lines(raw):
    text = raw.get("your_skills_and_experience") or raw.get("requirements")
    if not text:
        text = "\n".join(str(section.get("text", "")) for section in raw.get("sections", [])
                         if isinstance(section, dict) and re.search(r"skills and experience|requirements|qualifications|yeu cau", req_fold(section.get("heading"))))
    result = []
    inherited = "unspecified"
    for original in str(text or "").splitlines():
        line = req_clean(original).lstrip("-+●○• ")
        if not line:
            continue
        folded = req_fold(line)
        # Application instructions and marketing footers are not applicant skills.
        if re.search(r"yeu cau ung vien ung tuyen can cung cap|vi sao ban nen dam bao|given the high volume of cvs", folded):
            break
        heading_text = line[1:-1].strip() if re.fullmatch(r"\[[^\[\]]+\]", line) else line
        is_heading = not re.match(r"\s*[-+●○•]", original) and len(heading_text) < 85 and (
            heading_text.endswith(":") or re.fullmatch(
                r"(?:required|preferred|desired|technical|other|language|soft|education|experience|background|requirements|qualifications|skills|competencies|h[oọ]c v[aấ]n|[uư]u ti[eê]n)[\w /&-]*", heading_text, re.I))
        if is_heading:
            inherited = req_requirement_type(heading_text, "unspecified")
        result.append((line, inherited))
    return result


def req_skill_matches(line):
    folded = req_fold(line)
    found = []
    for name, category, patterns in REQ_SKILL_PATTERNS:
        for pattern in patterns:
            for match in pattern.finditer(folded):
                # Short ordinary words need their actual technical spelling.
                if name == "Go" and match.group() == "go":
                    if not re.search(r"\bGo\b", line) or re.match(r"\s+(?:to|ahead|home)\b", folded[match.end():]):
                        continue
                if name == "C" and (folded[match.end():match.end() + 1] in ("+", "#")
                                    or not re.search(r"(?<!\w)C(?!\w)", line)):
                    continue
                if name == "AI" and match.group() == "ai" and line[match.start():match.end()] != "AI":
                    # Vietnamese 'ai' means 'who'; the acronym itself needs
                    # technical spelling. Full named aliases remain accepted.
                    continue
                if name == "R" and match.group() == "r" and line[match.start():match.end()] != "R":
                    continue
                found.append((match.start(), match.end(), name, category))
    # Keep the most specific term when aliases overlap (Spring Boot vs Spring).
    kept = []
    for match in sorted(set(found), key=lambda row: (-(row[1] - row[0]), row[0])):
        if not any(match[0] < old[1] and old[0] < match[1] for old in kept):
            kept.append(match)
    return sorted(kept)


def req_local_clause(line, start, end):
    """Return the source clause/group which supplies evidence for one item.

    Keep comma-separated lists with a shared prefix/postfix qualifier together,
    but separate independent clauses such as "Python required, Docker preferred".
    Internal dots in Node.js/ASP.NET are not sentence boundaries.
    """
    folded = req_fold(line)
    terminal_label = re.search(r"[.!?;]\s*(\[[^\[\]]+\])\s*$", folded)
    terminal_label_start = (terminal_label.start(1) if terminal_label and (
        REQ_REQUIRED.search(terminal_label.group(1)) or REQ_PREFERRED.search(terminal_label.group(1))) else None)
    # Mask inline qualifiers without changing offsets. req_local_type handles
    # the item's own parenthesis before consulting its wider clause.
    masked = re.sub(r"\([^)]*\)|\[[^\[\]]*\]", lambda match: " " * len(match.group())
                    if (match.start() != terminal_label_start and
                        (REQ_REQUIRED.search(match.group()) or REQ_PREFERRED.search(match.group())))
                    else match.group(), folded)
    breaks = list(re.finditer(
        r"[;!?]|(?<!ph\.d)(?<!b\.sc)(?<!m\.sc)(?<!phd)(?<!bsc)(?<!msc)\.(?=\s|$)|"
        r"\b(?:but|however|whereas|additional|nhung|tuy nhien)\b", masked))
    # A terminal editorial label belongs to the preceding sentence, even when
    # the source puts a full stop before '[Preferred]'.
    if terminal_label_start is not None:
        breaks = [match for match in breaks if match.start() != terminal_label.start()]
    # "Not mandatory but preferred" qualifies the same item, rather than
    # introducing a new requirement. Do not detach the positive qualifier.
    breaks = [match for match in breaks if not (
        match.group() in {"but", "nhung"} and re.fullmatch(
            r"\s*(?:is |are |still |la )?(?:preferred|preferable|a plus|an? advantage|"
            r"nice[ -]to[ -]have|uu tien|loi the)\s*[.!]?\s*", masked[match.end():]))]
    left = max([0] + [match.end() for match in breaks if match.end() <= start])
    right = min([len(masked)] + [match.start() for match in breaks if match.start() >= end])
    group_start = left
    for separator in re.finditer(r",|\b(?:and|or|va|hoac)\b", masked[left:right]):
        boundary_start = left + separator.start()
        boundary_end = left + separator.end()
        prefix = masked[group_start:boundary_start]
        next_separator = re.search(r",|\b(?:and|or|va|hoac)\b", masked[boundary_end:right])
        next_end = boundary_end + next_separator.start() if next_separator else right
        suffix = masked[boundary_end:next_end]
        prefix_cues = list(REQ_REQUIRED.finditer(prefix)) + list(REQ_PREFERRED.finditer(prefix))
        suffix_cue = REQ_REQUIRED.search(suffix) or REQ_PREFERRED.search(suffix)
        # A leading "Required: Python, Java" applies to the complete list;
        # "Python required, Java" only explicitly qualifies the first item.
        anchor_matches = [match for _, _, patterns in REQ_SKILL_PATTERNS
                          for pattern in patterns for match in pattern.finditer(prefix)]
        anchor_matches += [match for _, pattern in REQ_LANGUAGE_ALIASES + REQ_DEGREE_PATTERNS
                           for match in re.finditer(pattern, prefix)]
        anchors = [match.start() for match in anchor_matches]
        prefix_scope = bool(prefix_cues and anchors and
                            min(match.start() for match in prefix_cues) < min(anchors))
        tail = prefix[max((match.end() for match in anchor_matches), default=len(prefix)):]
        independently_described = bool(re.search(
            r"\b(?:experience|expertise|proficien\w*|fluen\w*|knowledge|skills?|"
            r"kinh nghiem|thanh thao|giao tiep|[abc][12]|n[1-5])\b", tail))
        independently_described |= bool(re.search(r"\b(?:fluent|native)\b", prefix) and
                                        any(re.search(pattern, prefix) for _, pattern in REQ_LANGUAGE_ALIASES))
        independently_described |= bool(re.search(r"\b(?:in|nganh|chuyen nganh)\b", tail) and
                                        any(re.search(pattern, prefix) for _, pattern in REQ_DEGREE_PATTERNS) and
                                        any(re.search(pattern, suffix) for _, pattern in REQ_DEGREE_PATTERNS))
        if ((prefix_cues and (suffix_cue or not prefix_scope)) or
                (not prefix_cues and suffix_cue and independently_described)):
            if end <= boundary_start:
                return masked[group_start:boundary_start]
            if start >= boundary_end:
                group_start = boundary_end
    return masked[group_start:right]


def req_item_is_negated(line, start, end):
    inline = re.match(r"\s*[\[(]([^\])]*?)[\])]", req_fold(line)[end:])
    return bool((inline and REQ_NEGATED.search(inline.group(1))) or
                REQ_NEGATED.search(req_local_clause(line, start, end)))


def req_local_type(line, start, end, inherited):
    """An inline qualifier belongs to its item; wider cues use local scope."""
    for enclosure in re.finditer(r"[\[(]([^\])]*?)[\])]", req_fold(line)):
        if enclosure.start() < start and end < enclosure.end():
            return req_requirement_type(enclosure.group(1), inherited)
    inline = re.match(r"\s*[\[(]([^\])]*?)[\])]", req_fold(line)[end:])
    if inline and (REQ_REQUIRED.search(inline.group(1)) or REQ_PREFERRED.search(inline.group(1))):
        return req_requirement_type(inline.group(1), inherited)
    return req_requirement_type(req_local_clause(line, start, end), inherited)


def req_add_skill(result, name, category, requirement):
    # One canonical skill per job; an explicit mandatory occurrence takes priority.
    rank = {"unspecified": 0, "preferred": 1, "required": 2}
    old = result.get(req_fold(name))
    if old is None:
        result[req_fold(name)] = {"name": name, "category": category, "requirement_type": requirement}
    elif rank[requirement] > rank[old["requirement_type"]]:
        old["requirement_type"] = requirement


def req_degree_entries(line, inherited):
    folded = req_fold(line)
    # Academic honors preferences are not independent degree requirements.
    if re.search(r"xep loai|loai gioi|xuat sac|gpa|academic (?:honors|honours)", folded) and not re.search(r"degree|bang cap|bang dai hoc", folded):
        return []
    major_patterns = [
        ("Computer Science", r"\b(?:computer science|khoa hoc may tinh)\b"),
        ("Information Technology", r"\b(?:information technology|cong nghe thong tin|cntt)\b"),
        ("Information Security", r"\b(?:information security|an toan thong tin)\b"),
        ("Software Engineering", r"\b(?:software engineering|cong nghe phan mem|ky thuat phan mem)\b"),
        ("Information Systems", r"\b(?:information systems?|he thong thong tin)\b"),
        ("Electronics and Telecommunications", r"\b(?:electronics? (?:and |& )?telecommunications?|dien tu vien thong)\b"),
        ("Computer Engineering", r"\b(?:computer engineering|ky thuat may tinh)\b"),
        ("Data Science", r"\b(?:data science|khoa hoc du lieu)\b"),
        ("Artificial Intelligence", r"\b(?:artificial intelligence|tri tue nhan tao)\b"),
        ("Automation", r"\b(?:automation|tu dong hoa)\b"),
        ("Mathematics", r"\b(?:mathematics|toan hoc)\b"),
        ("Statistics", r"\b(?:statistics|thong ke)\b"),
    ]
    result = []
    for degree, pattern in REQ_DEGREE_PATTERNS:
        for match in re.finditer(pattern, folded):
            if degree == "associate" and match.group().startswith("associate") and not (
                    re.search(r"degree|diploma|education|qualification", req_local_clause(line, match.start(), match.end()))
                    or re.match(r"(?:[’']s)?\s+(?:in|of)\b", folded[match.end():])):
                # Associate Product Owner/Engineer is a role, not a degree.
                continue
            requirement = req_local_type(line, match.start(), match.end(), inherited)
            # The common schema has no negative-degree representation. Keep
            # "no degree required" in original prose instead of claiming it.
            if req_item_is_negated(line, match.start(), match.end()) and requirement != "preferred":
                continue
            clause = req_local_clause(line, match.start(), match.end())
            # A colon can introduce one degree's semicolon-separated major
            # alternatives. Retain only contiguous major-only continuations;
            # a different degree or explicit qualifier starts a new scope.
            initial_end = folded.find(";", match.end())
            if initial_end != -1 and ":" in folded[match.end():initial_end]:
                for continuation in folded[initial_end + 1:].split(";"):
                    if (not any(re.search(major_pattern, continuation) for _, major_pattern in major_patterns)
                            or any(re.search(degree_pattern, continuation) for _, degree_pattern in REQ_DEGREE_PATTERNS)
                            or REQ_REQUIRED.search(continuation) or REQ_PREFERRED.search(continuation)):
                        break
                    clause += ";" + continuation
            majors = [name for name, major_pattern in major_patterns if re.search(major_pattern, clause)]
            result.append({"degree": degree, "majors": majors, "requirement_type": requirement})
    return result


def req_structured_education(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in req_structured_education(item)]
    if isinstance(value, dict):
        fields = [value.get(key) for key in ("credentialCategory", "name", "description") if value.get(key)]
        return [" ".join(req_structured_education(fields))] if fields else []
    return []


def normalize_requirements(raw):
    lines = req_lines(raw)
    skills = {}
    prose_skill_evidence = {}
    languages = {}
    soft_skills = []
    for tag in raw.get("skills") or []:
        if not isinstance(tag, str) or not req_clean(tag):
            continue
        tag = req_clean(tag)
        folded = req_fold(tag)
        language = next((name for name, pattern in REQ_LANGUAGE_ALIASES if re.fullmatch(pattern, folded)), None)
        soft_skill = next((name for name, pattern in REQ_SOFT_SKILLS if re.fullmatch(pattern, folded)), None)
        if language:
            languages[language] = {"language": language, "level_text": None, "requirement_type": "unspecified"}
        elif soft_skill:
            soft_skills.append(soft_skill)
        else:
            # An unknown compound label must not lose its qualifier, for example
            # 'Agentic AI' must not silently become the broader skill 'AI'.
            canonical = next(((name, category) for name, category, aliases in REQ_SKILL_VOCABULARY
                              if folded in [req_fold(alias) for alias in aliases]), None)
            matches = req_skill_matches(tag)
            residual = folded
            for start, end, _, _ in reversed(matches):
                residual = residual[:start] + " " * (end - start) + residual[end:]
            if canonical:
                req_add_skill(skills, *canonical, "unspecified")
            elif matches and not re.search(r"\w", residual):
                for _, _, name, category in matches:
                    req_add_skill(skills, name, category, "unspecified")
            else:
                req_add_skill(skills, tag, "other", "unspecified")

    education = []
    knowledge = []
    for line, inherited in lines:
        # Technologies taught after hiring are not entry requirements.
        candidate_line = re.split(r"\b(?:we(?:['’]ll| will) (?:upskill|train|teach)|you will (?:learn|be trained))\b", line, flags=re.I)[0]
        folded = req_fold(candidate_line)
        for start, end, name, category in req_skill_matches(candidate_line):
            # AI degrees are education, rather than an additional skill claim.
            if name in {"AI", "Machine Learning"} and re.search(
                    r"(?:degree|dai hoc|cu nhan|chuyen nganh)", req_local_clause(candidate_line, start, end)):
                continue
            requirement = req_local_type(candidate_line, start, end, inherited)
            req_add_skill(skills, name, category, requirement)
            prose_skill_evidence.setdefault(req_fold(name), set()).add(requirement)

        for name, pattern in [
            ("Data Structures and Algorithms", r"\b(?:data structures?|algorithms?|cau truc du lieu|thuat toan)\b"),
            ("Operating Systems", r"\b(?:operating systems?|he dieu hanh)\b"),
            ("Networking", r"\b(?:networking|computer networks?|mang may tinh)\b"),
            ("Database", r"\b(?:databases?|co so du lieu|he quan tri co so du lieu)\b"),
            ("Object-Oriented Programming", r"\b(?:object[ -]oriented programming|oop|lap trinh huong doi tuong)\b"),
            ("Design Patterns", r"\b(?:design patterns?|mau thiet ke)\b"),
            ("Distributed Systems", r"\b(?:distributed systems?|he thong phan tan)\b"),
            ("Computer Architecture", r"\b(?:computer architecture|kien truc may tinh)\b"),
        ]:
            if any(not req_item_is_negated(candidate_line, match.start(), match.end())
                   for match in re.finditer(pattern, folded)) and name not in knowledge:
                knowledge.append(name)
        education.extend(req_degree_entries(candidate_line, inherited))
        for language, pattern in REQ_LANGUAGE_ALIASES:
            matches = [match for match in re.finditer(pattern, folded)
                       if not re.match(r"\s+(?:customers?|clients?|compan(?:y|ies)|markets?|partners?)\b", folded[match.end():])]
            eligible = []
            for match in matches:
                contextual_mix = any(enclosure.start() < match.start() and match.end() < enclosure.end()
                                     and re.search(r"\b(?:mix of|mixture of|nationalit\w*|team members?|colleagues?)\b", enclosure.group())
                                     and not (REQ_REQUIRED.search(enclosure.group()) or REQ_PREFERRED.search(enclosure.group()))
                                     for enclosure in re.finditer(r"\([^)]*\)", folded))
                if contextual_mix:
                    # Languages merely describing the team's composition are
                    # not separate mandatory proficiency requirements.
                    continue
                clause = req_local_clause(candidate_line, match.start(), match.end())
                proficiency = re.search(r"\b(?:language|skills?|proficien\w*|fluen\w*|communicat\w*|read\w*|writ\w*|writt\w*|speak\w*|spoken|verbal|level|toeic|ielts|toefl|jlpt|topik|hsk|n[1-5]|[abc][12]|tieng|ngu|doc|viet|nghe|noi|giao tiep)\b", clause)
                bare_language = re.fullmatch(pattern, req_clean(folded).rstrip(" .:"))
                if (proficiency or REQ_REQUIRED.search(clause) or REQ_PREFERRED.search(clause)
                        or bare_language or inherited != "unspecified"):
                    eligible.append(match)
            if not eligible:
                continue
            rank = {"unspecified": 0, "preferred": 1, "required": 2}
            requirement = max((req_local_type(candidate_line, match.start(), match.end(), inherited)
                               for match in eligible), key=rank.get)
            new = {"language": language, "level_text": line, "requirement_type": requirement}
            old = languages.get(language)
            if old is None or old["level_text"] is None:
                languages[language] = new
            else:
                if line not in old["level_text"].split("\n"):
                    old["level_text"] += "\n" + line
                if rank[new["requirement_type"]] > rank[old["requirement_type"]]:
                    old["requirement_type"] = new["requirement_type"]
        for name, pattern in REQ_SOFT_SKILLS:
            if re.search(pattern, folded) and name not in soft_skills:
                soft_skills.append(name)

    for text in req_structured_education(raw.get("education_requirements_structured")):
        education.extend(req_degree_entries(text, "unspecified"))
    # A title may explicitly declare language proficiency even without a section.
    title = req_clean(raw.get("title"))
    for language, pattern in REQ_LANGUAGE_ALIASES:
        title_match = re.search(pattern, req_fold(title))
        if title_match and re.search(r"\b(?:N[1-5]|[ABC][12]|IELTS|TOEIC|JLPT|fluent|business|native)\b", title, re.I):
            requirement = req_local_type(title, title_match.start(), title_match.end(), "unspecified")
            old = languages.get(language)
            if old is None or old["level_text"] is None:
                languages[language] = {"language": language, "level_text": title, "requirement_type": requirement}
            else:
                if title not in old["level_text"].split("\n"):
                    old["level_text"] += "\n" + title
                rank = {"unspecified": 0, "preferred": 1, "required": 2}
                if rank[requirement] > rank[old["requirement_type"]]:
                    old["requirement_type"] = requirement

    unique_education = []
    seen = set()
    for entry in education:
        signature = (entry["degree"], tuple(sorted(entry["majors"])), entry["requirement_type"])
        if signature not in seen:
            seen.add(signature)
            unique_education.append(entry)
    # A preference for a certification or narrower use does not make a separate,
    # unqualified mention of the same core technology optional. Tags alone have
    # no requirement evidence and therefore do not veto an explicit preference.
    for name, evidence in prose_skill_evidence.items():
        skills[name]["requirement_type"] = (
            "required" if "required" in evidence else
            "unspecified" if "unspecified" in evidence else "preferred")
    return {"skills": list(skills.values()), "required_knowledge": knowledge,
            "education_requirements": unique_education, "foreign_languages": list(languages.values()),
            "soft_skills": list(dict.fromkeys(soft_skills))}



NORMALIZED_FIELDS = (
    "job_id", "source", "source_job_id", "job_url", "crawl_run_id", "observed_at", "published_at",
    "title", "company_name", "description", "job_roles", "seniority_levels", "location_text", "cities", "work_mode",
    "skills", "required_knowledge", "experience_text", "experience_min_years", "experience_max_years",
    "education_requirements", "foreign_languages", "soft_skills", "salary_text", "salary_min", "salary_max",
    "salary_currency", "salary_period", "salary_basis", "salary_status",
)
NORMALIZATION_VERSION = "1.1"
VIETNAM_TZ = timezone(timedelta(hours=7))
NORMALIZATION_NOTES = [
    "Mỗi tin có đúng 30 trường. Trường đơn chưa xác định là null; danh sách chưa trích xuất được là [].",
    "observed_at là thời điểm thu thập nguồn, không phải thời điểm chạy lại bước chuẩn hóa; timezone +07:00.",
    "published_at chỉ có ngày được quy ước 00:00:00+07:00; đây không phải giờ đăng đã được xác minh. date_posted gốc nằm trong raw.",
    "job_id = itviec:<source_job_id>; chỉ dùng hash toàn bộ canonical URL khi website không có ID; không lấy số cuối slug làm ID.",
    "Trích xuất theo từ điển và quy tắc có bằng chứng; tên kỹ năng lạ từ thẻ nguồn được giữ với category=other.",
    "required/preferred chỉ gán khi có ngữ cảnh rõ, còn lại unspecified. Không tự quy đổi mô tả ngoại ngữ thành IELTS/CEFR.",
    "Không suy Fresher từ thiếu kinh nghiệm; badge accepted không tự trở thành cấp độ chính của vị trí.",
    "Không suy kinh nghiệm 0 khi thiếu số năm; không đổi bucket monthsOfExperience 10/37/61 của nguồn thành số năm chính xác.",
    "Lương yêu cầu đăng nhập có status=unknown; không lấy số JSON-LD ẩn để khẳng định mức lương đã được thấy trong phiên.",
    "Không tự đoán tiền tệ, kỳ lương hay gross/net khi nguồn không nêu; chỉ so sánh lương cùng các thuộc tính này.",
    "Đếm job_id phân biệt trong kỳ phân tích; mảng kỹ năng/thành phố không nhân số tin. runs/ giữ từng lần quan sát.",
    "DWH đọc staging.dwh_ready_jobs: chỉ batch hoàn tất kiểm định 1.1, đọc lại đủ 30 trường và khớp raw.",
    "ready_for_dwh phản ánh chất lượng các tin đã thu thập; source_complete phản ánh mức hoàn thành crawl.",
]


def normalized_datetime(value, *, date_only=False) -> str | None:
    """ISO 8601 +07:00; date-only publication dates use documented local midnight."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        if not date_only:
            return None
        value += "T00:00:00"
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A timezone-less timestamp cannot establish an observation instant.
    if result.tzinfo is None:
        if not date_only:
            return None
        result = result.replace(tzinfo=VIETNAM_TZ)
    return result.astimezone(VIETNAM_TZ).isoformat()


def normalized_job_identity(raw: dict) -> tuple[str, str | None, str]:
    url = raw.get("job_url") or raw.get("url") or raw.get("fetched_url")
    if not isinstance(url, str) or not is_itviec(url):
        raise CrawlError("Không có URL ITviec hợp lệ để nhận diện tin.")
    url = canonical(url)
    source_id = raw.get("source_job_id")
    if source_id is None and set(raw) != set(NORMALIZED_FIELDS):
        candidate = raw.get("job_id")
        # Legacy parse_job used the entire slug as fallback, not a website ID.
        slug = raw.get("job_slug") or urlsplit(url).path.rsplit("/", 1)[-1]
        if candidate is not None and str(candidate) != slug:
            source_id = str(candidate).removeprefix("itviec:")
    if source_id is None:
        for posting in raw.get("json_ld") or []:
            if not isinstance(posting, dict):
                continue
            types = posting.get("@type")
            if "JobPosting" not in (types if isinstance(types, list) else [types]):
                continue
            identifier = posting.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value")
            if isinstance(identifier, (str, int)) and not isinstance(identifier, bool):
                source_id = str(identifier)
                break
    source_id = clean(str(source_id)) if source_id is not None else None
    source_id = source_id or None
    stable_id = source_id or "url_" + hashlib.sha256(url.encode("utf-8")).hexdigest()
    return "itviec:" + stable_id, source_id, url


_ROLE_RULES = (
    ("Backend", r"\bback[ -]?end\b|\bserver[ -]?side\b"),
    ("Frontend", r"\bfront[ -]?end\b"),
    ("Fullstack", r"\bfull[ -]?stack\b"),
    ("Mobile", r"\b(?:mobile|android|ios|flutter|react native)(?: application| app)? (?:developer|engineer)\b|\bmobile development\b"),
    ("AI/ML", r"\b(?:ai(?:/ml)?|ml|artificial intelligence|machine learning|deep learning|computer vision|nlp|llm|agentic)(?: software)? (?:engineer|expert|scientist|developer|specialist)\b"),
    ("Data Engineering", r"\bdata engineer\w*\b|\bbig data\b|\bky su du lieu\b"),
    ("Data Science", r"\bdata scien\w*\b|\bkhoa hoc du lieu\b"),
    ("Data Analysis", r"\bdata analy\w*\b|\bphan tich du lieu\b"),
    ("Business Intelligence", r"\bbusiness intelligence\b|\bbi developer\b"),
    ("DevOps", r"\bdevops\b|\bplatform engineer\w*\b"),
    ("SRE", r"\bsre\b|\bsite reliability\b"),
    ("Cloud", r"\bcloud engineer\w*\b|\bcloud architect\w*\b"),
    ("QA", r"\bqa\b|\bqc\b|\bqaqc\b|\btester\b|\btest engineer\w*\b|\btest coordinator\b|\bquality assurance\b"),
    ("Security", r"\bsecurity\b|\bcyber\w*\b|\ban toan thong tin\b|\bbao mat\b"),
    ("Business Analysis", r"\bbusiness analyst\b|\bbusiness analysis\b"),
    ("UI/UX", r"\bux\b|\bui\b|\bproduct designer\b"),
    ("Product Management", r"\bproduct manager\b|\bproduct owner\b|\bproduct management\b"),
    ("Project Management", r"\bproject (?:manager|leader|management)\b|\bquan ly du an\b"),
    ("Bridge Engineer", r"\bbrse\b|\bbridge (?:system )?engineer\b"),
    ("Embedded", r"\bembedded\b|\bfirmware\b|\bhe thong nhung\b"),
    ("Game", r"\bgame (?:developer|engineer|design)\w*\b"),
    ("Blockchain", r"\bblockchain\b|\bweb3\b"),
    ("IT Support", r"\bit support\b|\bhelp[ -]?desk\b"),
    ("Network", r"\bnetwork (?:engineer|administrator)\b"),
    ("System Engineering", r"\bsystem(?:s)? (?:engineer|administrator)\b|\bsysadmin\b"),
    ("Architecture", r"\b(?:enterprise|solution|solutions|chief|software|technical|infrastructure) architect\b"),
    ("Engineering Management", r"\bengineering manager\b|\bmanager of engineering\b"),
    ("IT Audit/Risk", r"\bit auditor\b|\bit audit\b|\bit risk manager\b"),
)


def normalize_job_roles(raw: dict) -> list[str]:
    expertise = raw.get("job_expertise") or []
    if isinstance(expertise, str):
        expertise = [expertise]
    text = key(" ".join([str(raw.get("title") or ""), *map(str, expertise)]))
    roles = [name for name, pattern in _ROLE_RULES if re.search(pattern, text)]
    if not roles and re.search(r"\bsoftware (?:engineer|developer)\b|\blap trinh vien\b", text):
        roles.append("Software Engineering")
    # No technology-only guess (e.g. Python does not establish Backend).
    return roles


_CITY_ALIASES = {
    "ho chi minh": "Hồ Chí Minh", "hochiminh": "Hồ Chí Minh", "ho chi minh city": "Hồ Chí Minh",
    "hcm": "Hồ Chí Minh", "hcmc": "Hồ Chí Minh", "sai gon": "Hồ Chí Minh", "saigon": "Hồ Chí Minh",
    "ha noi": "Hà Nội", "hanoi": "Hà Nội", "ha noi city": "Hà Nội",
    "da nang": "Đà Nẵng", "danang": "Đà Nẵng", "da nang city": "Đà Nẵng",
    "hai phong": "Hải Phòng", "haiphong": "Hải Phòng", "can tho": "Cần Thơ", "cantho": "Cần Thơ",
    "hue": "Huế", "thua thien hue": "Huế", "binh duong": "Bình Dương", "dong nai": "Đồng Nai",
    "ba ria vung tau": "Bà Rịa - Vũng Tàu", "vung tau": "Vũng Tàu", "nha trang": "Nha Trang",
    "khanh hoa": "Khánh Hòa", "quy nhon": "Quy Nhơn", "binh dinh": "Bình Định",
    "bac ninh": "Bắc Ninh", "hung yen": "Hưng Yên", "quang ninh": "Quảng Ninh",
    "long an": "Long An", "lam dong": "Lâm Đồng", "da lat": "Đà Lạt", "dalat": "Đà Lạt",
}


def normalized_city(value: str) -> str | None:
    normalized = key(value)
    normalized = re.sub(r"^(?:thanh pho|tinh|tp\.?|city of)\s*", "", normalized).strip(" .,")
    return _CITY_ALIASES.get(normalized)


def normalize_locations(raw: dict) -> tuple[str | None, list[str]]:
    addresses = raw.get("addresses") or []
    text_addresses = [a.get("address") for a in addresses if isinstance(a, dict) and a.get("address")]
    listing = raw.get("location_text") or raw.get("city_on_listing")
    location_text = "\n".join(text_addresses) if text_addresses else (listing if isinstance(listing, str) else None)
    regions = regions_from_locations(raw.get("locations_structured"))
    cities = []
    for region in regions:
        city = normalized_city(region)
        if city:
            cities.append(city)
    if not cities and isinstance(listing, str):
        direct = normalized_city(listing)
        if direct:
            cities.append(direct)
        else:
            for part in re.split(r"\s+-\s+|[,;/|]", listing):
                city = normalized_city(part)
                if city:
                    cities.append(city)
    if not cities:
        # Match named cities in address strings; never use districts as cities.
        for address in text_addresses:
            normalized = key(address)
            for alias, city in _CITY_ALIASES.items():
                if re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", normalized):
                    cities.append(city)
    return location_text or None, unique(cities)


def normalize_work_mode(value) -> str | None:
    normalized = key(value or "")
    return {"at office": "onsite", "onsite": "onsite", "on-site": "onsite", "on site": "onsite",
            "tai van phong": "onsite", "remote": "remote", "lam viec tu xa": "remote",
            "hybrid": "hybrid", "linh hoat": "hybrid"}.get(normalized)


def normalize_seniority(raw: dict) -> list[str]:
    # Accepted badges indicate eligibility, not the advertised position's level.
    values = raw.get("seniority_declared") or raw.get("seniority_levels") or []
    if isinstance(values, str):
        values = [values]
    text = " ".join(map(str, values)) + " " + str(raw.get("title") or "")
    levels = infer_seniority(text)
    for name in ("Staff", "Principal"):
        if re.search(r"\b" + name.lower() + r"\b", key(text)):
            levels.append(name)
    return unique("Intern" if level == "Internship" else level for level in levels)


_HTML_TAG = re.compile(r"</?(?:html|head|body|p|div|span|br|hr|h[1-6]|ul|ol|li|strong|b|em|i|a|table|thead|tbody|tr|td|th|section|article|script|style|noscript|svg|code|pre|blockquote|dl|dt|dd|u|small|sub|sup|mark|s|font|img|figure|figcaption|header|footer|nav|aside)(?:\s[^<>]*|\s*/?)>", re.I)


class _DescriptionText(HTMLParser):
    """Retain section/list boundaries and prevent adjacent inline tags joining words."""
    blocks = {"p", "div", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "table", "tr", "section", "article", "pre", "blockquote", "dl", "dt", "dd"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.boundary = False
        self.force_space = False
        self.tags = []

    def handle_starttag(self, tag, attrs):
        force_space = bool(set((dict(attrs).get('class') or '').split()) & {'itag', 'badge', 'ilabel'})
        if tag not in {'br', 'hr', 'img', 'meta', 'link', 'input'}:
            self.tags.append((tag, force_space))
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden += 1
        if not self.hidden:
            if tag in self.blocks:
                self.parts.append("\n")
            else:
                self.boundary = True
                self.force_space |= force_space

    def handle_endtag(self, tag):
        force_space = False
        if self.tags and self.tags[-1][0] == tag:
            _, force_space = self.tags.pop()
        if tag in {"script", "style", "svg", "noscript"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden:
            if tag in self.blocks:
                self.parts.append("\n")
            else:
                self.boundary = True
                self.force_space |= force_space

    def handle_data(self, data):
        if not self.hidden:
            if self.boundary and self.parts and data:
                left = self.parts[-1]
                if left and not left[-1].isspace() and not data[0].isspace():
                    a, b = re.search(r'[\w.+#-]+$', left), re.match(r'[\w.+#-]+', data)
                    word = (a.group() + b.group()).lower() if a and b else ''
                    fragment = word in {'javascript', 'typescript', 'reactjs', 'nodejs', 'vuejs', 'vue.js',
                                        'graphql', 'postgresql', 'mongodb', 'github', 'gitlab', 'devops', 'c++', 'c#', '.net'}
                    fragment |= left[-1].isalpha() and data[0].isalpha() and data[0].islower()
                    if (self.force_space or not fragment) and data[0] not in ',;:!?)]' and left[-1] not in '([/':
                        self.parts.append(' ')
            self.parts.append(data)
            self.boundary = self.force_space = False


def description_text(value: str) -> str:
    if not _HTML_TAG.search(value):
        return value.strip()
    parser = _DescriptionText()
    parser.feed(value)
    parser.close()
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalized_description(raw: dict) -> str:
    blocks = []
    for section in raw.get("sections") or []:
        if not isinstance(section, dict) or not section.get("text"):
            continue
        blocks.append("\n".join(v for v in [section.get("heading"), section["text"]] if isinstance(v, str) and v))
    if not blocks:
        for heading, field in [("Job description", "job_description"),
                               ("Your skills and experience", "your_skills_and_experience"),
                               ("Why you'll love working here", "why_youll_love_working_here"),
                               ("Top reasons to join", "top_reasons_to_join")]:
            if isinstance(raw.get(field), str) and raw[field].strip():
                blocks.append(heading + "\n" + raw[field].strip())
    result = "\n\n".join(blocks) or raw.get("description") or raw.get("job_details_text")
    if not isinstance(result, str) or not result.strip():
        raise CrawlError("Không có nội dung mô tả để chuẩn hóa tin.")
    # Also handle legacy raw HTML; the source object itself remains untouched.
    result = description_text(result)
    if not result:
        raise CrawlError("Nội dung mô tả rỗng sau khi loại bỏ HTML.")
    return result


def normalize_job(raw: dict, crawl_run_id: str, observed_at: str | None = None) -> dict:
    if not isinstance(raw, dict):
        raise CrawlError("Bản ghi tin phải là object.")
    if not isinstance(crawl_run_id, str) or not crawl_run_id.strip():
        raise CrawlError("Thiếu crawl_run_id.")
    identity, source_id, url = normalized_job_identity(raw)
    observed = normalized_datetime(observed_at or raw.get("crawled_at") or raw.get("observed_at"))
    if observed is None:
        raise CrawlError("Thiếu thời điểm thu thập có múi giờ; không tự gán thời gian chuẩn hóa thành observed_at.")
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise CrawlError("Tin thiếu title.")
    if set(raw) == set(NORMALIZED_FIELDS):
        # Already normalized input can be re-exported without duplicating IDs or losing arrays.
        record = copy.deepcopy(raw)
        record.update(job_id=identity, source_job_id=source_id, job_url=url,
                      crawl_run_id=crawl_run_id, observed_at=observed)
    else:
        location_text, cities = normalize_locations(raw)
        record = {
            "job_id": identity, "source": "itviec", "source_job_id": source_id, "job_url": url,
            "crawl_run_id": crawl_run_id, "observed_at": observed,
            "published_at": normalized_datetime(raw.get("date_posted"), date_only=True),
            "title": title, "company_name": clean(raw.get("company_name")) or None,
            "description": normalized_description(raw), "job_roles": normalize_job_roles(raw),
            "seniority_levels": normalize_seniority(raw), "location_text": location_text,
            "cities": cities, "work_mode": normalize_work_mode(raw.get("working_model")),
            **normalize_requirements(raw), **normalize_experience(raw), **normalize_salary(raw),
        }
    record = {field: record[field] for field in NORMALIZED_FIELDS}
    validate_normalized_job(record)
    return record


def validate_normalized_job(job: dict):
    if not isinstance(job, dict) or tuple(job) != NORMALIZED_FIELDS:
        raise CrawlError("Bản ghi không đúng thứ tự/danh sách 30 trường chuẩn hóa.")
    for field in ("job_id", "source", "job_url", "crawl_run_id", "observed_at", "title", "description", "salary_status"):
        if not isinstance(job[field], str) or not job[field].strip():
            raise CrawlError("Trường bắt buộc phải là string có nội dung: " + field)
    for field in ("source_job_id", "published_at", "company_name", "location_text", "work_mode", "experience_text",
                  "salary_text", "salary_currency", "salary_period", "salary_basis"):
        if job[field] is not None and (not isinstance(job[field], str) or not job[field].strip()):
            raise CrawlError("Trường phải là string hoặc null: " + field)
    for field in ("job_roles", "seniority_levels", "cities", "required_knowledge", "soft_skills"):
        if not isinstance(job[field], list) or not all(isinstance(v, str) and v.strip() for v in job[field]):
            raise CrawlError("Trường phải là mảng string: " + field)
        if len(job[field]) != len({key(v) for v in job[field]}):
            raise CrawlError("Danh sách chứa giá trị trùng: " + field)
    for field, names in (("skills", {"name", "category", "requirement_type"}),
                         ("education_requirements", {"degree", "majors", "requirement_type"}),
                         ("foreign_languages", {"language", "level_text", "requirement_type"})):
        if not isinstance(job[field], list):
            raise CrawlError("Trường phải là mảng object: " + field)
        for item in job[field]:
            if not isinstance(item, dict) or set(item) != names or item["requirement_type"] not in {"required", "preferred", "unspecified"}:
                raise CrawlError("Object không đúng schema: " + field)
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
        if field == "education_requirements":
            signatures = [(item['degree'], tuple(sorted(key(v) for v in item['majors'])), item['requirement_type']) for item in job[field]]
        else:
            name = "name" if field == "skills" else "language"
            signatures = [key(item[name]) for item in job[field]]
        if len(signatures) != len(set(signatures)):
            raise CrawlError("Danh sách object chứa giá trị trùng: " + field)
    for field in ("experience_min_years", "experience_max_years", "salary_min", "salary_max"):
        value = job[field]
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0):
            raise CrawlError("Trường phải là số không âm hoặc null: " + field)
    for minimum, maximum in (("experience_min_years", "experience_max_years"), ("salary_min", "salary_max")):
        if job[minimum] is not None and job[maximum] is not None and job[minimum] > job[maximum]:
            raise CrawlError("Cận dưới lớn hơn cận trên: " + minimum)
    if job["work_mode"] not in {None, "onsite", "hybrid", "remote"}:
        raise CrawlError("work_mode không thuộc tập giá trị quy định.")
    if job["salary_status"] not in {"disclosed", "negotiable", "not_disclosed", "unknown"}:
        raise CrawlError("salary_status không hợp lệ.")
    if job["salary_basis"] not in {None, "gross", "net"}:
        raise CrawlError("salary_basis không hợp lệ.")
    if job["source"] != "itviec":
        raise CrawlError("Nguồn phải là itviec.")
    if not is_itviec(job["job_url"]) or not urlsplit(job["job_url"]).path.startswith("/it-jobs/"):
        raise CrawlError("job_url phải trỏ đến một tin ITviec.")
    if not job['job_id'].startswith('itviec:') or len(job['job_id']) <= len('itviec:'):
        raise CrawlError("job_id không đúng định dạng nguồn.")
    if job['source_job_id'] is not None and job['job_id'] != 'itviec:' + job['source_job_id']:
        raise CrawlError("job_id và source_job_id không nhất quán.")
    for field in ('observed_at', 'published_at'):
        if job[field] is not None and normalized_datetime(job[field]) is None:
            raise CrawlError("Datetime phải hợp lệ và có múi giờ: " + field)
    if _HTML_TAG.search(job['description']):
        raise CrawlError("description còn chứa HTML.")
    if job['salary_currency'] is not None and not re.fullmatch(r'[A-Z]{3}', job['salary_currency']):
        raise CrawlError("salary_currency phải là mã tiền tệ viết hoa gồm 3 ký tự.")
    disclosed = job['salary_min'] is not None or job['salary_max'] is not None
    if (job['salary_status'] == 'disclosed') != disclosed:
        raise CrawlError("salary_status không nhất quán với mức lương số.")
    if any(job[field] is not None and job[field] <= 0 for field in ('salary_min', 'salary_max')):
        raise CrawlError("Không dùng lương 0 thay cho lương chưa xác định.")


def atomic_json(path: Path, document: dict):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.stem + "_", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(document, stream, ensure_ascii=False, indent=2, allow_nan=False)
        temporary.replace(path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()



def run_directory_name(run_id: str) -> str:
    return run_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id) else (
        "run_" + hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32])


def write_outputs(output_dir: Path, jobs: list[dict], metadata: dict, errors: list[dict],
                  raw_jobs: list[dict] | None = None):
    """Latest result plus one snapshot per run; never fan out jobs by skill/city."""
    # Checkpoints for one run replace that run's snapshot. A different run has
    # a distinct folder, so collecting the same job again retains both observations.
    for job in jobs:
        validate_normalized_job(job)
    jobs = list({job["job_id"]: job for job in jobs}.values())
    export_metadata = dict(metadata)
    export_metadata["collected_count"] = len(jobs)
    export_metadata["distinct_job_count"] = len(jobs)
    source_records = raw_jobs if raw_jobs is not None else jobs
    source_by_id = {}
    for raw in source_records:
        try:
            source_by_id[normalized_job_identity(raw)[0]] = raw
        except CrawlError:
            source_by_id[raw.get("job_id")] = raw
        # Compatibility for legacy internal calls exporting raw records directly.
        source_by_id.setdefault(raw.get("job_id"), raw)
    raw_records = [{"job_id": job["job_id"], "crawl_run_id": job.get("crawl_run_id") or metadata.get("crawl_run_id"),
                    "observed_at": job.get("observed_at") or normalized_datetime(job.get("crawled_at")),
                    "raw": source_by_id.get(job["job_id"], job)} for job in jobs]
    document = {"metadata": export_metadata, "jobs": jobs, "errors": errors}
    raw_document = {"metadata": export_metadata, "jobs": raw_records, "errors": errors}
    destinations = [output_dir]
    run_id = metadata.get("crawl_run_id")
    if run_id:
        # All generated run IDs are Windows-safe; hash external/legacy IDs if needed.
        destinations.append(output_dir / "runs" / run_directory_name(run_id))
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        atomic_json(destination / "itviec_jobs.json", document)
        atomic_json(destination / "itviec_jobs_raw.json", raw_document)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=destination,
                                             prefix=".itviec_jobs_", suffix=".csv.tmp", delete=False) as stream:
                temporary = Path(stream.name)
                fields = list(NORMALIZED_FIELDS)
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for job in jobs:
                    writer.writerow({field: json.dumps(value, ensure_ascii=False, allow_nan=False)
                                     if isinstance(value, (list, dict)) else value for field, value in job.items()})
            temporary.replace(destination / "itviec_jobs.csv")
        finally:
            if temporary and temporary.exists():
                temporary.unlink()



def normalize_existing_file(source: Path, output_dir: Path) -> int:
    """Convert saved crawler JSON without a new network observation or login."""
    source = Path(source)
    output_dir = Path(output_dir)
    latest_paths = {output_dir / name for name in ("itviec_jobs.json", "itviec_jobs_raw.json", "itviec_jobs.csv")}
    if source.resolve() in {path.resolve() for path in latest_paths}:
        LOG.error("File đầu vào trùng file đầu ra. Chọn --output-dir khác để giữ nguyên nguồn.")
        return 1
    try:
        data = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        LOG.error("Không đọc được JSON đầu vào --normalize-file.")
        return 1
    if isinstance(data, list):
        rows, original_metadata = data, {}
    elif isinstance(data, dict) and isinstance(data.get("jobs"), list):
        rows = data["jobs"]
        original_metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    else:
        LOG.error("JSON phải là mảng tin hoặc object có mảng jobs.")
        return 1
    # Legacy files lacked run IDs. Deterministically identify the original batch,
    # so repeating normalization does not fabricate extra observations/history.
    marker = original_metadata.get("started_at") or json.dumps(rows, ensure_ascii=False, sort_keys=True)
    run_id = original_metadata.get("crawl_run_id") or (
        "itviec_legacy_" + hashlib.sha256(str(marker).encode("utf-8")).hexdigest()[:24])
    if not isinstance(run_id, str):
        LOG.error("crawl_run_id của JSON đầu vào phải là string.")
        return 1
    history_dir = output_dir / "runs" / run_directory_name(run_id)
    history_paths = {history_dir / name for name in ("itviec_jobs.json", "itviec_jobs_raw.json", "itviec_jobs.csv")}
    if source.resolve() in {path.resolve() for path in history_paths}:
        LOG.error("File đầu vào trùng snapshot lịch sử đầu ra. Chọn --output-dir khác để giữ nguyên nguồn.")
        return 1
    source_errors = data.get("errors", []) if isinstance(data, dict) else []
    errors = list(source_errors) if isinstance(source_errors, list) else []
    jobs, raw_jobs, normalization_errors = [], [], []
    seen = set()
    for index, row in enumerate(rows):
        # Raw exports wrap the untouched source row with observation identifiers.
        raw = row.get("raw") if isinstance(row, dict) and isinstance(row.get("raw"), dict) else row
        try:
            # Raw wrappers may store observation time outside the source record.
            observation = row.get("observed_at") if raw is not row else None
            job = normalize_job(raw, run_id, observed_at=observation)
            if job["job_id"] in seen:
                continue
            seen.add(job["job_id"])
            jobs.append(job)
            raw_jobs.append(raw)
        except CrawlError as exc:
            normalization_errors.append({"row": index, "stage": "normalize", "error": str(exc)})
    errors.extend(normalization_errors)
    metadata = {**original_metadata, "crawl_run_id": run_id, "schema_version": NORMALIZATION_VERSION,
                "mode": "normalize_existing", "stage": "normalize", "data_format": "normalized",
                "requested_count": original_metadata.get("requested_count", len(rows)), "collected_count": len(jobs),
                "raw_count": len(rows), "normalization_complete": not normalization_errors,
                "normalization_error_count": len(normalization_errors),
                "complete": original_metadata.get("complete", True) is not False and not normalization_errors,
                "normalization_finished_at": datetime.now(VIETNAM_TZ).isoformat(),
                "timezone": "+07:00", "normalization_notes": NORMALIZATION_NOTES}
    write_outputs(output_dir, jobs, metadata, errors, raw_jobs=raw_jobs)
    LOG.info("Đã chuẩn hóa %s tin phân biệt, %s dòng lỗi vào %s.", len(jobs), len(normalization_errors), output_dir.resolve())
    return 0 if jobs and not normalization_errors else 2 if jobs else 1



_DB_ARRAY_FIELDS = frozenset({"job_roles", "seniority_levels", "cities", "skills",
                              "required_knowledge", "education_requirements",
                              "foreign_languages", "soft_skills"})
DB_SCALAR_FIELDS = tuple(field for field in NORMALIZED_FIELDS if field not in _DB_ARRAY_FIELDS)
STAGING_CHILD_COLUMNS = {
    "job_roles": ("role",),
    "job_seniority_levels": ("seniority_level",),
    "job_cities": ("city",),
    "job_skills": ("name", "category", "requirement_type"),
    "job_required_knowledge": ("knowledge",),
    "job_education_requirements": ("degree", "requirement_type"),
    "job_foreign_languages": ("language", "level_text", "requirement_type"),
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


class DatabaseStagingError(RuntimeError):
    """Sanitized storage error; never include connection strings/passwords."""


def _database_connection(args):
    """Import psycopg only for database mode; libpq still supports PG*/.pgpass."""
    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError:
        raise DatabaseStagingError(
            'Thiếu thư viện PostgreSQL. Chạy: python -m pip install "psycopg[binary]>=3.1,<4"') from None
    options = {"connect_timeout": getattr(args, "db_connect_timeout", 10),
               "application_name": "itviec_staging", "autocommit": True}
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
            raise DatabaseStagingError(
                "Không nhập được mật khẩu PostgreSQL; dùng terminal tương tác hoặc cấu hình .pgpass.") from None
    try:
        connection = psycopg.connect(getattr(args, "db_dsn", "") or "", **options)
    except (psycopg.Error, ValueError, TypeError):
        raise DatabaseStagingError(
            "Không kết nối được PostgreSQL. Kiểm tra DATABASE_URL/PG* hoặc các tham số --db-*. ") from None
    return connection, psycopg.Error, Jsonb


def _database_error(exc):
    if getattr(exc, "sqlstate", None) in {"42P01", "3F000", "42703"}:
        return "Chưa có bảng/cột đúng cấu trúc. Chạy data_preprocessing/itviec_db/itviec_schema.sql trong database đang kết nối."
    return "Không đọc/ghi được PostgreSQL; kiểm tra kết nối, quyền truy cập và schema raw/staging."


def _database_child_rows(job, observation_id):
    """All SQL identifiers come from constants; all source values are parameters."""
    result = {}
    for field, table in (("job_roles", "job_roles"), ("seniority_levels", "job_seniority_levels"),
                         ("cities", "job_cities"), ("required_knowledge", "job_required_knowledge"),
                         ("soft_skills", "job_soft_skills")):
        result[table] = [(observation_id, position, value)
                         for position, value in enumerate(job[field], 1)]
    for field, table in (("skills", "job_skills"),
                         ("education_requirements", "job_education_requirements"),
                         ("foreign_languages", "job_foreign_languages")):
        result[table] = [(observation_id, position, *(item[column] for column in STAGING_CHILD_COLUMNS[table]))
                         for position, item in enumerate(job[field], 1)]
    result["job_education_majors"] = [
        (observation_id, education_order, major_order, major)
        for education_order, item in enumerate(job["education_requirements"], 1)
        for major_order, major in enumerate(item["majors"], 1)
    ]
    return result


def _database_write_job(connection, job):
    """One atomic transaction per observation: parent UPSERT and complete child replacement."""
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(_DB_UPSERT_SQL,
                           tuple(job[field] for field in DB_SCALAR_FIELDS) + (NORMALIZATION_VERSION,))
            observation_id = cursor.fetchone()[0]
            # Removing the education parent also removes its majors through ON DELETE CASCADE.
            for table in STAGING_CHILD_COLUMNS:
                cursor.execute("DELETE FROM staging." + table + " WHERE observation_id = %s", (observation_id,))
            rows = _database_child_rows(job, observation_id)
            for table in STAGING_CHILD_COLUMNS:
                if rows[table]:
                    columns = ("observation_id", "item_order") + STAGING_CHILD_COLUMNS[table]
                    cursor.executemany("INSERT INTO staging." + table + " (" + ", ".join(columns) +
                                       ") VALUES (" + ", ".join(["%s"] * len(columns)) + ")", rows[table])
            if rows["job_education_majors"]:
                cursor.executemany("""
                    INSERT INTO staging.job_education_majors
                        (observation_id, education_order, item_order, major) VALUES (%s, %s, %s, %s)
                """, rows["job_education_majors"])


def _database_reject_job(connection, job_id, crawl_run_id):
    """A rejected replay cannot leave an older representation eligible in staging."""
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM staging.job_observations WHERE job_id = %s AND crawl_run_id = %s",
                           (job_id, crawl_run_id))


def _database_validate_run(connection, crawl_run_id, expected_jobs):
    """Read back all 30 fields and compare with the mapped records and raw lineage."""
    errors, validated_count = [], 0
    missing = {field: 0 for field in ('company_name', 'published_at', 'work_mode',
               'experience_min_years', 'salary_currency', 'salary_period', 'salary_basis')}
    salary_comparable = 0
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT to_jsonb(v), o.normalization_version, r.source_job_id, r.job_url, r.observed_at
            FROM staging.jobs v
            JOIN staging.job_observations o USING (job_id, crawl_run_id)
            JOIN raw.job_postings r USING (job_id, crawl_run_id)
            WHERE v.crawl_run_id = %s ORDER BY v.job_id
        """, (crawl_run_id,))
        rows = cursor.fetchall()
    actual_ids = set()
    for payload, version, source_id, url, observed_at in rows:
        job_id = payload.get('job_id') if isinstance(payload, dict) else None
        try:
            if not isinstance(payload, dict) or set(payload) != set(NORMALIZED_FIELDS):
                raise CrawlError("View staging không đúng 30 trường.")
            job = {field: payload[field] for field in NORMALIZED_FIELDS}
            validate_normalized_job(job)
            actual_ids.add(job_id)
            raw_time = observed_at.isoformat() if isinstance(observed_at, datetime) else observed_at
            if (version != NORMALIZATION_VERSION or job['source_job_id'] != source_id or
                    job['job_url'] != url or job['crawl_run_id'] != crawl_run_id or
                    normalized_datetime(job['observed_at']) != normalized_datetime(raw_time)):
                raise CrawlError("Staging không khớp version hoặc thông tin nhận diện/thời gian trong raw.")
            for field in ('observed_at', 'published_at'):
                if job[field] is not None:
                    job[field] = normalized_datetime(job[field])
            if job_id not in expected_jobs or job != expected_jobs[job_id]:
                raise CrawlError("Dữ liệu đọc lại từ staging khác kết quả ánh xạ đã kiểm định.")
            validated_count += 1
            for field in missing:
                missing[field] += job[field] is None
            salary_comparable += (job['salary_status'] == 'disclosed' and
                                  all(job[field] is not None for field in ('salary_currency', 'salary_period', 'salary_basis')))
        except (CrawlError, KeyError, ValueError, TypeError) as exc:
            errors.append({'job_id': job_id, 'stage': 'staging_quality', 'error': str(exc)})
    if actual_ids != set(expected_jobs):
        errors.append({'stage': 'staging_quality', 'error': 'Tập job_id đọc lại không khớp các tin đã nạp thành công.'})
    return {'validated_count': validated_count, 'error_count': len(errors), 'errors': errors,
            'missing_optional_counts': missing, 'salary_comparable_count': salary_comparable}


def stage_database_run(crawl_run_id: str, args) -> int:
    """Clean/map exactly one raw batch into staging; preserve raw JSON and emit no files.

    Return 0 for all rows, 2 for some rejected rows, 1 for no valid rows/missing run,
    and 4 for a connection/dependency/schema failure. Re-running the same run replaces
    staging observations and child arrays without creating duplicate observations.
    """
    if not isinstance(crawl_run_id, str) or not crawl_run_id.strip():
        LOG.error("Thiếu crawl_run_id cần làm sạch và lưu staging.")
        return 1
    connection = None
    try:
        connection, db_error, jsonb = _database_connection(args)
    except DatabaseStagingError as exc:
        LOG.error("%s", exc)
        return 4
    started = datetime.now(VIETNAM_TZ).isoformat()
    staged_count, errors, row_count = 0, [], 0
    expected_jobs = {}
    quality = {'validated_count': 0, 'error_count': 0, 'errors': []}
    source_complete = False
    run_found, storage_failed = False, False
    status = "failed"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended(%s, 0))", ('itviec:staging:' + crawl_run_id,))
            if cursor.fetchone()[0] is not True:
                LOG.error("Batch này đang được một tiến trình khác xử lý; thử lại sau.")
                return 2
            cursor.execute("SELECT crawl_run_id, status, requested_count, collected_count FROM raw.crawl_runs WHERE crawl_run_id = %s", (crawl_run_id,))
            source_run = cursor.fetchone()
            if source_run is None:
                LOG.error("Không tìm thấy crawl_run_id trong raw.crawl_runs.")
                return 1
            if source_run[1] == 'running':
                LOG.error("Batch raw đang crawl; chờ crawler kết thúc trước khi xử lý staging.")
                return 2
            source_complete = source_run[1] == 'succeeded'
            run_found = True
            # Check the complete table contract before changing any staging observations.
            cursor.execute("SELECT observation_id, normalized_at, " + ", ".join(_DB_INSERT_COLUMNS) +
                           " FROM staging.job_observations LIMIT 0")
            for table, child_columns in STAGING_CHILD_COLUMNS.items():
                cursor.execute("SELECT observation_id, item_order, " + ", ".join(child_columns) +
                               " FROM staging." + table + " LIMIT 0")
            cursor.execute("SELECT observation_id, education_order, item_order, major "
                           "FROM staging.job_education_majors LIMIT 0")
            cursor.execute("""
                SELECT job_id, source_job_id, job_url, observed_at, raw_payload
                FROM raw.job_postings WHERE crawl_run_id = %s ORDER BY observed_at, job_id
            """, (crawl_run_id,))
            rows = cursor.fetchall()
        row_count = len(rows)
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(_DB_METADATA_SQL, (jsonb({
                    "status": "running", "started_at": started, "finished_at": None,
                    "version": NORMALIZATION_VERSION, "raw_count": row_count,
                    "staged_count": 0, "error_count": 0, "errors": [],
                    "ready_for_dwh": False, "source_complete": source_complete,
                    "quality": quality,
                }), crawl_run_id))
        for job_id, source_job_id, job_url, observed_at, payload in rows:
            try:
                if not isinstance(payload, dict):
                    raise CrawlError("raw_payload phải là JSON object.")
                observed = observed_at.isoformat() if isinstance(observed_at, datetime) else observed_at
                mapping_input = copy.deepcopy(payload)
                mapping_input["job_url"] = job_url
                job = normalize_job(mapping_input, crawl_run_id, observed_at=observed)
                # The raw table identity is authoritative and is the composite foreign key.
                job.update(job_id=job_id, source_job_id=source_job_id, job_url=job_url,
                           crawl_run_id=crawl_run_id)
                validate_normalized_job(job)
                _database_write_job(connection, job)
                expected_jobs[job_id] = job
                staged_count += 1
            except (CrawlError, ValueError, TypeError, OverflowError) as exc:
                errors.append({"job_id": job_id, "stage": "clean_map", "error": str(exc)})
                _database_reject_job(connection, job_id, crawl_run_id)
            except db_error as exc:
                sqlstate = getattr(exc, "sqlstate", None)
                # Integrity/type errors reject just this transaction; connection/schema
                # failures abort this stage while keeping earlier committed observations.
                if isinstance(sqlstate, str) and sqlstate[:2] in {"22", "23"}:
                    errors.append({"job_id": job_id, "stage": "staging_write",
                                   "error": "Bản ghi không đáp ứng ràng buộc PostgreSQL.", "sqlstate": sqlstate})
                    _database_reject_job(connection, job_id, crawl_run_id)
                else:
                    raise
        quality = _database_validate_run(connection, crawl_run_id, expected_jobs)
        errors.extend(quality['errors'])
        if row_count != source_run[3]:
            errors.append({'stage': 'raw_quality', 'error': 'Số bản ghi raw không khớp collected_count của batch.'})
        status = "succeeded" if staged_count and not errors else "partial" if staged_count else "failed"
    except db_error as exc:
        storage_failed = True
        status = "failed"
        errors.append({"stage": "staging_storage", "error": _database_error(exc)})
        LOG.error("%s", _database_error(exc))
    finally:
        if run_found:
            summary = {
                "status": status, "started_at": started, "finished_at": datetime.now(VIETNAM_TZ).isoformat(),
                "version": NORMALIZATION_VERSION, "raw_count": row_count,
                "staged_count": staged_count, "error_count": len(errors), "errors": errors,
                "ready_for_dwh": (not storage_failed and status == 'succeeded' and staged_count == row_count
                                  and row_count > 0 and quality['validated_count'] == row_count),
                "source_complete": source_complete, "quality": quality,
                "mapping_notes": NORMALIZATION_NOTES,
            }
            try:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(_DB_METADATA_SQL, (jsonb(summary), crawl_run_id))
            except db_error:
                storage_failed = True
                LOG.error("Không lưu được trạng thái staging vào raw.crawl_runs.metadata.")
        try:
            connection.close()
        except db_error:
            LOG.warning("Không đóng được kết nối PostgreSQL.")
    LOG.info("Đã nạp %s/%s tin; kiểm định đọc lại %s tin, %s lỗi; ready_for_dwh=%s.",
             staged_count, row_count, quality['validated_count'], len(errors),
             not storage_failed and status == 'succeeded' and staged_count == row_count and row_count > 0
             and quality['validated_count'] == row_count)
    return 4 if storage_failed else 0 if staged_count and not errors else 2 if staged_count else 1



# Compatibility for callers that used the previous database loader name.
# All database writes now target staging; existing normalized data is untouched.
def normalize_database_run(crawl_run_id: str, args) -> int:
    return stage_database_run(crawl_run_id, args)


_DB_CHILD_COLUMNS = STAGING_CHILD_COLUMNS
DatabaseNormalizationError = DatabaseStagingError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", "--normalize-file", dest="input_file", type=Path,
                        help="JSON gốc từ crawler hoặc JSON đã lưu trước đây.")
    mode.add_argument("--crawl-run-id", help="Làm sạch và ánh xạ đúng batch này từ raw sang các bảng staging trong PostgreSQL.")
    parser.add_argument("--output-dir", type=Path, default=Path("itviec_data"))
    parser.add_argument("--db-dsn", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-password-prompt", action="store_true")
    parser.add_argument("--db-connect-timeout", type=int, default=10)
    args = parser.parse_args(argv)
    if args.db_connect_timeout < 1 or (args.db_port is not None and not 1 <= args.db_port <= 65535):
        parser.error("db-connect-timeout phải > 0; db-port trong khoảng 1..65535.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.crawl_run_id is not None:
        return stage_database_run(args.crawl_run_id, args)
    return normalize_existing_file(args.input_file, args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
