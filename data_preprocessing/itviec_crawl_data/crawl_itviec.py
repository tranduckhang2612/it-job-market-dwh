#!/usr/bin/env python3
"""Crawl dữ liệu gốc ITviec và ghi trực tiếp vào PostgreSQL.

Python >= 3.10. Cài:
    python -m pip install requests beautifulsoup4 "psycopg[binary]>=3.1,<4"
Chạy SQL data_preprocessing/itviec_db/itviec_schema.sql trong database trước.
Kết nối bằng DATABASE_URL, biến PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD,
PGSERVICE/.pgpass hoặc các tùy chọn --db-host/--db-port/--db-name/--db-user.
--db-password-prompt nhập mật khẩu riêng trong terminal nếu cần.
    python crawl_itviec.py --cookie-file cookie.txt --limit 20

raw.crawl_runs lưu metadata/errors/trạng thái mỗi lần chạy.
raw.job_postings.raw_payload lưu riêng JSON gốc mỗi tin, gồm HTML/text/JSON-LD.
Mỗi tin được commit cùng tiến độ của lần chạy trong một transaction.
Khóa (job_id, crawl_run_id) giữ lịch sử; observed_at lấy từ crawled_at gốc.
Mặc định không xuất JSON/CSV ra đĩa. --save-json lưu thêm bản JSON dự phòng;
--save-html lưu thêm HTML; --output-dir chỉ dùng cho các tùy chọn lưu file này.

Cookie lấy từ header Cookie của request trang chi tiết đã đăng nhập trên
Chrome thường; lưu một dòng vào cookie.txt. Không gửi cookie lên chat/Git.
Không tự đọc cookie từ hồ sơ trình duyệt cá nhân.
Chế độ requests + --cookie-file đã được xác nhận hoạt động trên máy của bạn.

--browser/--login/--headed và --auth-file vẫn được hỗ trợ khi cần Playwright:
    python -m pip install playwright
    python -m playwright install chromium
Google có thể từ chối đăng nhập tự động; HTTP 403 dừng ngay và không chứng
minh cookie đã hết hạn. Không vượt CAPTCHA hoặc xác minh truy cập.

Chạy tuần tự, có khoảng nghỉ/timeout/retry giới hạn; khử trùng URL và ID nguồn.
Lấy đủ tin: exit 0; lấy được một phần: exit 2; không có tin: exit 1;
cần đăng nhập lại trong chế độ browser: exit 3; lỗi lưu PostgreSQL: exit 4.
Lỗi DB dừng crawl; các tin đã commit vẫn được giữ. Không tự tạo/chỉnh schema.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

try:
    import requests
    from bs4 import BeautifulSoup, Comment, Tag
except ImportError as exc:
    raise SystemExit("Thiếu thư viện. Chạy: python -m pip install requests beautifulsoup4") from exc

BASE_URL = "https://itviec.com"
START_URL = BASE_URL + "/it-jobs"
USER_AGENT = "ITviecSampleCrawler/1.0 (public job listings)"
LOG = logging.getLogger("itviec")
AUTH_HELP = ("Dùng --browser --login để đăng nhập bằng email/mật khẩu ITviec, "
             "hoặc lấy cookie ITviec mới từ Chrome thường rồi chạy "
             "--browser --cookie-file cookie.txt. Giữ cùng --auth-file nếu dùng đường dẫn riêng.")


class CrawlError(RuntimeError):
    """Không tải/đọc được trang."""


class AccessBlocked(CrawlError):
    """Dừng khi bị chặn hoặc robots.txt không cho phép."""


class AuthenticationRequired(AccessBlocked):
    """Phiên trình duyệt chưa đăng nhập hoặc không còn quyền xem lương."""


class StorageError(RuntimeError):
    """Không ghi được PostgreSQL; dừng crawl, không bỏ qua như lỗi một tin."""


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def text_of(node) -> str | None:
    return clean(node.get_text(" ", strip=True)) or None if node else None


def unique(values) -> list:
    return list(dict.fromkeys(v for v in values if v))


def key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower().replace("đ", "d"))
    return clean("".join(c for c in value if not unicodedata.combining(c))).rstrip(":")


def absolute(value, base=BASE_URL) -> str | None:
    if not value:
        return None
    result = urljoin(base, str(value))
    return result if urlsplit(result).scheme in {"http", "https"} else None


def canonical(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def diagnostic_url(url: str) -> str:
    """Chỉ ghi host/path của request; bỏ userinfo, query và fragment riêng tư."""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.hostname or "", parsed.path, "", ""))


def is_itviec(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname == "itviec.com"


def inline_word_fragment(left: str, right: str) -> bool:
    """Nhận ra một từ đổi định dạng ở giữa, trước khi thêm separator giữa node."""
    if not left or not right or left[-1].isspace() or right[0].isspace():
        return False
    left_word = re.search(r"[\w.+#-]+$", left)
    right_word = re.match(r"[\w.+#-]+", right)
    if left_word and right_word:
        combined = (left_word.group() + right_word.group()).lower()
        if combined in {"javascript", "typescript", "reactjs", "nodejs", "vuejs", "vue.js",
                        "graphql", "postgresql", "mongodb", "github", "gitlab", "devops",
                        "c++", "c#", ".net"}:
            return True
    # Chữ thường nối tiếp thường là phần còn lại của từ (salar + y, attrac + tive).
    # Cụm mới bắt đầu bằng chữ hoa như React/AWS vẫn được tách, trừ tên ở trên.
    return left[-1].isalpha() and right[0].isalpha() and right[0].islower()


def rich_text(html: str) -> str:
    """Giữ đoạn, xuống dòng, bullet và thứ tự danh sách, không cắt nội dung."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.select("script, style, svg, noscript"):
        node.decompose()
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    # Một từ có thể đổi định dạng ở giữa: <strong>Attractive salar</strong>y.
    # Chỉ gộp node có ranh giới từ bị chia; giữ node độc lập để get_text thêm space.
    for node in soup.find_all(["strong", "b", "em", "i", "u", "mark", "s", "small", "font", "span"]):
        if {"itag", "badge", "ilabel"}.intersection(node.get("class", [])):
            continue
        previous, following = node.previous_sibling, node.next_sibling
        previous_text = previous.get_text() if isinstance(previous, Tag) else str(previous or "")
        following_text = following.get_text() if isinstance(following, Tag) else str(following or "")
        own_text = node.get_text()
        join_before = inline_word_fragment(previous_text, own_text)
        join_after = inline_word_fragment(own_text, following_text)
        if join_before or join_after:
            # Sau khi unwrap chỉ nối ranh giới thuộc cùng từ, vẫn tách cụm ở phía kia.
            if previous_text and not join_before and not previous_text[-1].isspace() and own_text:
                if previous_text[-1] not in "([/" and own_text[0] not in "),;:!?":
                    node.insert_before(" ")
            if following_text and not join_after and not following_text[0].isspace() and own_text:
                if following_text[0] not in "),;:!?" and own_text[-1] not in "([/":
                    node.insert_after(" ")
            node.unwrap()
    soup.smooth()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        prefix = "- "
        if li.parent and li.parent.name == "ol":
            start = li.parent.get("start", "1")
            start = int(start) if str(start).isdigit() else 1
            prefix = f"{start + len(li.find_previous_siblings('li'))}. "
        li.insert_before("\n" + prefix)
        li.append("\n")
    for node in soup.find_all(["p", "div", "section", "h1", "h2", "h3", "h4",
                               "h5", "h6", "ul", "ol", "blockquote", "tr"]):
        node.insert_before("\n")
        node.append("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.append(" | ")
    lines = [clean(line) for line in soup.get_text(" ").splitlines()]
    result = "\n".join(line for line in lines if line)
    # Separator không được tạo khoảng trắng thừa quanh dấu câu/ngoặc của inline.
    result = re.sub(r"[ \t]+([,;:!?)\]])", r"\1", result)
    result = re.sub(r"([(\[])[ \t]+", r"\1", result)
    # Hai thuật ngữ này bị nối ngay trong một text node của nguồn. Không dùng
    # quy tắc tách CamelCase chung vì sẽ làm hỏng JavaScript, ReactJS, NodeJS.
    return re.sub(r"(\bSpring\s+Boot)(?=REST\s+API\b)", r"\1 ", result, flags=re.I)


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


def listing_city(node: Tag) -> str | None:
    # Nhãn nghề cũng có .text-truncate[title] nhưng là thẻ A. Ô thành phố là
    # DIV có text-nowrap, thuộc hàng chứa biểu tượng map-pin.
    candidate = node.select_one("div.text-truncate.text-nowrap[title]")
    if candidate:
        return clean(candidate.get("title")) or text_of(candidate)
    for pin in node.select('use[href$="#map-pin"]'):
        svg = pin.find_parent("svg")
        candidate = svg.find_next_sibling(["div", "span"]) if svg else None
        if candidate:
            city = clean(candidate.get("title")) or text_of(candidate)
            if city:
                return city
    return None


def json_ld(soup: BeautifulSoup) -> list[dict]:
    objects = []

    def collect(value):
        if isinstance(value, list):
            for entry in value:
                collect(entry)
        elif isinstance(value, dict):
            objects.append(value)
            collect(value.get("@graph", []))

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            collect(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            LOG.warning("Bỏ qua một khối JSON-LD không hợp lệ.")
    return objects


def labeled_values(container: Tag | None) -> dict[str, list[str]]:
    """Đọc Skills / Job Expertise / Job Domain theo nhãn, trong khối của tin."""
    result = {}
    if container is None:
        return result
    for label in container.find_all(["div", "span", "dt", "strong"]):
        if label.find(True):
            continue
        label_text = text_of(label)
        if not label_text or not label_text.endswith(":"):
            continue
        sibling = label.find_next_sibling()
        if sibling:
            tags = sibling.select(".itag")
            result[key(label_text)] = unique(
                text_of(t) for t in tags
            ) if tags else [text_of(sibling)] if text_of(sibling) else []
    return result


def parse_sections(content: Tag | None, url: str) -> list[dict]:
    result = []
    if content is None:
        return result
    # Nhà tuyển dụng có thể dùng cả H2/H3 bên trong mô tả. Chỉ lấy tiêu đề
    # đầu tiên của từng khối paragraph trực tiếp, giữ các tiêu đề khác trong thân.
    for block in content.find_all(recursive=False):
        if not isinstance(block, Tag):
            continue
        heading = block.find("h2", recursive=False)
        if heading is None:
            continue
        body = "".join(str(s) for s in heading.next_siblings)
        result.append({
            "heading": text_of(heading),
            "text": rich_text(body),
            "html": body.strip(),
            "links": [{"text": text_of(a), "url": absolute(a.get("href"), url)}
                      for a in BeautifulSoup(body, "html.parser").select("a[href]")],
        })
    return result


def section_text(sections: list[dict], *names: str) -> str | None:
    matches = [s["text"] for s in sections if key(s["heading"] or "") in names]
    return "\n\n".join(matches) or None


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


def parse_company(container: Tag | None, posting: dict, url: str) -> dict:
    organization = posting.get("hiringOrganization") or {}
    if not isinstance(organization, dict):
        organization = {}
    values = {}
    if container:
        for row in container.select(".row"):
            columns = row.find_all("div", class_="col", recursive=False)
            if len(columns) >= 2 and text_of(columns[0]):
                values[text_of(columns[0])] = text_of(columns[1])
    normalized = {key(k): v for k, v in values.items()}
    link = container.select_one('h3 a[href*="/companies/"]') if container else None
    image = container.select_one("img") if container else None
    rating = container.select_one('a[href*="/review"] .h4') if container else None
    return {
        "profile_url": absolute(link.get("href"), url) if link else None,
        "summary": text_of(container.select_one("p")) if container else organization.get("description"),
        "rating": text_of(rating),
        "logo_url": absolute(image.get("data-src") or image.get("src"), url)
                    if image else organization.get("logo"),
        "type": normalized.get("company type") or normalized.get("loai hinh cong ty"),
        "industry": normalized.get("company industry") or normalized.get("linh vuc cong ty"),
        "size": normalized.get("company size") or normalized.get("quy mo cong ty"),
        "country": normalized.get("country") or normalized.get("quoc gia"),
        "working_days": normalized.get("working days") or normalized.get("ngay lam viec"),
        "overtime_policy": normalized.get("overtime policy") or normalized.get("chinh sach ot"),
        "attributes": values,
        "text": rich_text(str(container)) if container else None,
        "html": str(container) if container else None,
    }


def parse_job(html: str, url: str, card: dict | None = None) -> dict:
    card = card or {}
    soup = BeautifulSoup(html, "html.parser")
    objects = json_ld(soup)
    posting = next((o for o in objects if "JobPosting" in
                    (o.get("@type") if isinstance(o.get("@type"), list)
                     else [o.get("@type")])), {})
    header = soup.select_one(".job-show-header")
    info = soup.select_one(".job-show-info")
    content = soup.select_one(".job-content")
    company = soup.select_one(".job-show-employer-info")
    if header is None or content is None:
        raise CrawlError("Không thấy khối chi tiết tin. Có thể tin hết hạn hoặc HTML đã đổi.")
    title = text_of(header.select_one("h1")) or posting.get("title")
    organization = posting.get("hiringOrganization") or {}
    organization = organization if isinstance(organization, dict) else {}
    company_name = text_of(header.select_one(".employer-name")) or organization.get("name")
    sections = parse_sections(content, url)
    labels = labeled_values(info)
    description = section_text(sections, "job description", "mo ta cong viec", "the job")
    requirements = section_text(sections, "your skills and experience", "skills and experience",
                                "yeu cau cong viec", "yeu cau ky nang va kinh nghiem")
    benefits = section_text(sections, "why you'll love working here", "why you’ll love working here",
                            "tai sao ban se yeu thich lam viec tai day", "quyen loi")
    reasons = section_text(sections, "top 3 reasons to join us", "3 ly do de gia nhap cong ty",
                           "3 ly do de gia nhap cong ty chung toi")
    required = {"title": title, "company_name": company_name,
                "job_description": description, "your_skills_and_experience": requirements}
    missing = [name for name in ("title",) if not required[name]]
    if not rich_text(str(content)):
        missing.append("description")
    if missing:
        raise CrawlError("Thiếu trường bắt buộc: " + ", ".join(missing))

    salary_node = header.select_one(".salary")
    salary_text = text_of(salary_node)
    login_required = bool(header.select_one(".sign-in-view-salary"))
    declared_levels = labels.get("level") or labels.get("job level") or labels.get("cap do") or []
    inferred_levels = infer_seniority(title)
    badges = unique(text_of(a) for a in info.select('a[href*="fresher-accepted"], '
                    'a[href*="internship-accepted"]')) if info else []
    accepted_levels = [level for level, phrase in [("Fresher", "fresher"), ("Internship", "intern")]
                       if any(phrase in key(badge) for badge in badges)]
    addresses = []
    if info:
        for link in info.select('a[href*="google.com/maps"]'):
            address = text_of(link.parent.select_one("span"))
            if address and address not in [a["address"] for a in addresses]:
                addresses.append({"address": address, "map_url": absolute(link.get("href"), url)})
    working_model = None
    posted_text = None
    if info:
        for span in info.select("span.normal-text"):
            value = text_of(span)
            if value and key(value) in {"at office", "remote", "hybrid", "tai van phong",
                                       "lam viec tu xa", "linh hoat"}:
                working_model = value
            if value and re.match(r"^(Posted\b|Đăng\b)", value, re.I):
                posted_text = value
    skill_values = labels.get("skills") or labels.get("ky nang") or []
    if not skill_values and isinstance(posting.get("skills"), str):
        skill_values = [clean(v) for v in posting["skills"].split(",") if clean(v)]
    apply_link = header.select_one('a[data-jobs--jd-scroll-target="btnApply"]')
    gallery = info.select_one("[data-jobs--jd-photos-url-value]") if info else None
    photo_urls = unique(absolute(img.get("data-src") or img.get("src"), url)
                        for img in info.select(".jd-photos img")) if info else []
    details_html = "\n".join(str(block) for block in [header, info, content] if block)
    city = clean(card.get("city")) or None
    city_source = "listing" if city else "not_published"
    if not city:
        regions = regions_from_locations(posting.get("jobLocation"))
        if regions:
            city = " - ".join(regions)
            city_source = "locations_structured.address.addressRegion"
    warnings = []
    if re.search(r"\bSpring\s+BootREST\s+API\b", content.get_text(), re.I):
        warnings.append("Nguồn ghi Spring BootREST API; text đã tách thành Spring Boot REST API, HTML giữ nguyên.")
    if not declared_levels:
        warnings.append("Cấp độ không được công bố thành trường riêng; xem seniority_source.")
    if login_required:
        warnings.append("Trang trả về yêu cầu đăng nhập để xem lương trong phiên truy cập này.")
    for field, value in [("why_youll_love_working_here", benefits), ("addresses", addresses),
                         ("skills", skill_values), ("working_model", working_model)]:
        if not value:
            warnings.append(f"Không đọc được hoặc trang không công bố trường {field}.")
    return {
        "job_id": card.get("job_id") or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        "job_slug": urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1],
        "url": canonical(url), "fetched_url": url,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
        **required,
        "seniority_levels": declared_levels or inferred_levels,
        "seniority_source": "explicit_job_field" if declared_levels else
                            "inferred_from_title" if inferred_levels else "not_published",
        "seniority_declared": declared_levels, "seniority_inferred": inferred_levels,
        "accepted_levels": accepted_levels, "acceptance_badges": badges,
        "why_youll_love_working_here": benefits,
        "top_reasons_to_join": reasons,
        "skills": unique(skill_values),
        "job_expertise": labels.get("job expertise") or labels.get("chuyen mon") or [],
        "job_domains": labels.get("job domain") or labels.get("linh vuc") or [],
        "addresses": addresses, "locations_structured": posting.get("jobLocation"),
        "city_on_listing": city, "city_source": city_source,
        "working_model": working_model,
        "salary": {"text": salary_text, "visibility": "login_required" if login_required else
                   "visible" if salary_text else "not_published",
                   "structured": posting.get("baseSalary")},
        "posted_text": posted_text, "date_posted": posting.get("datePosted"),
        "valid_through": posting.get("validThrough"),
        "employment_type": posting.get("employmentType"),
        "experience_requirements_structured": posting.get("experienceRequirements"),
        "education_requirements_structured": posting.get("educationRequirements"),
        "application_url": absolute(apply_link.get("href"), url) if apply_link else None,
        "application_structured": posting.get("potentialAction"),
        "company_info": parse_company(company, posting, url),
        "photo_urls": photo_urls,
        "photo_gallery_endpoint": absolute(gallery.get("data-jobs--jd-photos-url-value"), url)
                                  if gallery else None,
        "sections": sections, "detail_attributes": labels,
        "job_details_text": rich_text(details_html), "job_details_html": details_html,
        "json_ld": objects, "listing_card": card, "warnings": warnings,
    }


def parse_listing(html: str, url: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for node in soup.select(".card-jobs-list .job-card, .job-card[data-job-key]"):
        link = node.select_one("h3 a[href], h2 a[href]")
        if link is None:
            continue
        job_url = absolute(link.get("href"), url)
        if not job_url or not is_itviec(job_url) or not re.fullmatch(
            r"/it-jobs/[^/]+-\d+", urlsplit(job_url).path
        ):
            continue
        company_link = node.select_one('span a[href*="/companies/"]')
        cards.append({
            "url": job_url, "job_id": node.get("data-job-key"),
            "title": text_of(link), "company_name": text_of(company_link),
            "city": listing_city(node),
            "text": rich_text(str(node)),
        })
    next_link = soup.select_one('a[rel~="next"][href*="page="]')
    next_url = absolute(next_link.get("href"), url) if next_link else None
    return cards, next_url if next_url and is_itviec(next_url) else None



def new_crawl_run_id() -> str:
    return "itviec_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_") + uuid.uuid4().hex



def raw_job_identity(raw: dict) -> tuple[str, str | None, str]:
    url = raw.get("job_url") or raw.get("url") or raw.get("fetched_url")
    if not isinstance(url, str) or not is_itviec(url):
        raise CrawlError("Không có URL ITviec hợp lệ để nhận diện tin.")
    url = canonical(url)
    source_id = raw.get("source_job_id")
    if source_id is None:
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



def read_cookie_file(path: Path) -> list[tuple[str, str]]:
    """Đọc header Cookie do người dùng cung cấp; không ghi giá trị vào lỗi/log."""
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise CrawlError("Không đọc được --cookie-file. Kiểm tra file văn bản UTF-8.") from None
    raw = re.sub(r"^cookie: *", "", raw, flags=re.I)
    if not raw or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise CrawlError("File cookie phải chứa một dòng Cookie hợp lệ, không chứa ký tự điều khiển.")
    cookies = []
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
            raise CrawlError("Cookie không hợp lệ. Chỉ lưu giá trị header Cookie, không lưu toàn bộ headers.")
        try:
            value.encode("ascii")
        except UnicodeError:
            raise CrawlError("Cookie phải giữ nguyên giá trị đã mã hóa từ Request Headers.") from None
        cookies.append((name, value))
    return cookies


class Client:
    def __init__(self, delay: float, timeout: float, retries: int):
        self.delay, self.timeout, self.retries = delay, timeout, retries
        self.last_request = 0.0
        self.phase = "startup"
        self.last_requested_url = None
        self.last_http_status = None
        self.robots = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
                                     "Accept-Language": "en-US,en;q=0.9,vi;q=0.8"})

    def load_cookie_file(self, path: Path):
        """Nạp header Cookie do người dùng cung cấp, chỉ gửi cookie qua HTTPS ITviec."""
        # Chỉ nạp sau khi toàn bộ file hợp lệ, giữ dấu '=' và percent encoding.
        for name, value in read_cookie_file(path):
            self.session.cookies.set(name, value, domain="itviec.com", path="/", secure=True)
        LOG.info("Đã nạp cookie từ file; không ghi giá trị cookie vào log hoặc dữ liệu.")

    def get(self, url: str) -> tuple[str, str]:
        if not is_itviec(url):
            raise CrawlError("Crawler chỉ tải trang thuộc itviec.com.")
        self.last_requested_url = diagnostic_url(url)
        self.last_http_status = None
        if self.robots and not self.robots.can_fetch(USER_AGENT, url):
            raise AccessBlocked(f"robots.txt không cho phép: {url}")
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                # Kiểm tra từng redirect để không tự tải trang ngoài ITviec.
                response = self.session.get(url, timeout=self.timeout, allow_redirects=False)
                self.last_http_status = response.status_code
                if response.is_redirect:
                    target = absolute(response.headers.get("Location"), url)
                    if target and is_itviec(target) and target != url:
                        raise CrawlError(f"Trang chuyển hướng; không phải nội dung tin mong đợi: {target}")
                    raise CrawlError("Trang chuyển hướng sang địa chỉ không hợp lệ.")
                if response.status_code in {401, 403}:
                    raise AccessBlocked(f"HTTP {response.status_code}: ITviec chặn truy cập hoặc yêu cầu đăng nhập.")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.retries:
                        retry_after = response.headers.get("Retry-After", "")
                        pause = min(60, max(2 ** (attempt + 1), int(retry_after))) if retry_after.isdigit() else 2 ** (attempt + 1)
                        LOG.warning("HTTP %s, thử lại sau %ss.", response.status_code, pause)
                        time.sleep(pause)
                        continue
                response.raise_for_status()
                response.encoding = "utf-8"
                if re.search(r"<title[^>]*>\s*(Just a moment|Access denied|Attention Required)",
                             response.text, re.I):
                    raise AccessBlocked("Nhận trang xác minh/chặn truy cập thay vì nội dung ITviec.")
                return response.text, response.url
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if attempt < self.retries and (status is None or status == 429 or status >= 500):
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise CrawlError(f"Không tải được {url}: {exc}") from exc
        raise CrawlError(f"Hết số lần thử: {url}")

    def check_robots(self):
        self.phase = "robots"
        html, url = self.get(BASE_URL + "/robots.txt")
        parser = RobotFileParser(url)
        parser.parse(html.splitlines())
        self.robots = parser
        crawl_delay = parser.crawl_delay(USER_AGENT)
        if crawl_delay:
            self.delay = max(self.delay, crawl_delay)


    def close(self):
        self.session.close()


class BrowserClient(Client):
    """Trình duyệt riêng, tự lưu/nạp cookie và localStorage bằng Playwright."""

    def __init__(self, delay: float, timeout: float, retries: int, auth_file: Path):
        super().__init__(delay, timeout, retries)
        self.auth_file = auth_file.resolve()
        self.playwright = self.browser = self.context = self.page = None
        self.browser_error = RuntimeError
        self.browser_user_agent = USER_AGENT
        self.auth_verified = False
        self.cookie_file_used = False

    def start(self, login=False, headed=False, cookie_file: Path | None = None):
        self.phase = "session_initialization"
        self.last_requested_url = None
        self.last_http_status = None
        if login and cookie_file:
            raise CrawlError("Chọn đăng nhập bằng --login hoặc nạp cookie bằng --browser --cookie-file.")
        cookies = read_cookie_file(cookie_file) if cookie_file else []
        if not login and not cookies and not self.auth_file.is_file():
            raise AuthenticationRequired("Chưa có phiên được lưu. " + AUTH_HELP)
        state = None
        # Cookie do người dùng nhập có ưu tiên cao nhất; không trộn với phiên
        # cũ, kể cả khi file trạng thái cũ bị hỏng.
        if not cookies and self.auth_file.is_file():
            try:
                state = json.loads(self.auth_file.read_text(encoding="utf-8"))
                if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
                    raise ValueError
            except (OSError, UnicodeError, ValueError):
                if not login:
                    raise AuthenticationRequired("Không đọc được trạng thái đăng nhập. " + AUTH_HELP) from None
                state = None
        try:
            from playwright.sync_api import Error, sync_playwright
        except ImportError:
            raise CrawlError("Chế độ --browser cần thư viện: python -m pip install playwright; "
                             "sau đó: python -m playwright install chromium") from None
        self.browser_error = Error
        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(headless=not (login or headed), timeout=self.timeout * 1000)
            self.context = self.browser.new_context(storage_state=state, locale="en-US")
            if cookies:
                # Header không chứa metadata hết hạn; không tự đặt/gia hạn.
                # Chỉ nạp cookie ITviec, không đọc cookie Google hay hồ sơ Chrome.
                self.context.add_cookies([{"name": name, "value": value, "url": BASE_URL + "/",
                                           "secure": True} for name, value in cookies])
                self.cookie_file_used = True
                LOG.info("Đã nạp cookie ITviec vào trình duyệt; đang kiểm tra phiên.")
            self.page = self.context.new_page()
            self.browser_user_agent = self.page.evaluate("navigator.userAgent")
            crawl_delay = self.robots.crawl_delay(self.browser_user_agent) if self.robots else None
            if crawl_delay:
                self.delay = max(self.delay, crawl_delay)
            if login:
                LOG.info("Nếu Google từ chối đăng nhập, dùng Sign In with Email bằng mật khẩu ITviec, "
                         "hoặc dừng và dùng --browser --cookie-file cookie.txt từ Chrome thường.")
                self.phase = "login"
                self.page.goto(BASE_URL + "/sign_in", wait_until="domcontentloaded", timeout=self.timeout * 1000)
                try:
                    input("Đăng nhập ITviec trong cửa sổ vừa mở (ưu tiên Sign In with Email). "
                          "Khi hoàn tất, quay lại terminal nhấn Enter: ")
                except EOFError:
                    raise CrawlError("--login cần terminal tương tác để bạn xác nhận đã đăng nhập.") from None
            if login or cookies:
                # Enter/cookie tồn tại chưa chứng minh đăng nhập thành công; kiểm tra
                # một trang chi tiết mới, do chính máy chủ trả về trong phiên này.
                self.phase = "session_validation_listing"
                listing, fetched = self.get(START_URL)
                cards, _ = parse_listing(listing, fetched)
                if not cards:
                    raise CrawlError("Không tìm được tin để kiểm tra phiên đăng nhập.")
                self.phase = "session_validation_detail"
                self.get(cards[0]["url"])
                LOG.info("Đã xác minh quyền xem lương và lưu phiên đăng nhập cho lần chạy sau.")
        except self.browser_error:
            # Không ghi exception gốc: lỗi thư viện có thể chứa trạng thái riêng tư.
            raise CrawlError("Không khởi động/điều khiển được trình duyệt. Kiểm tra đã chạy "
                             "python -m playwright install chromium; --login/--headed cần môi trường có giao diện.") from None

    def get(self, url: str) -> tuple[str, str]:
        if self.page is None:
            # check_robots chạy trước start: dùng HTTP text gốc, không dùng HTML
            # <pre> do Chromium dựng cho tài liệu text/plain.
            return super().get(url)
        if not is_itviec(url):
            raise CrawlError("Crawler chỉ mở trang thuộc itviec.com.")
        self.last_requested_url = diagnostic_url(url)
        self.last_http_status = None
        if self.robots and not self.robots.can_fetch(self.browser_user_agent, url):
            raise AccessBlocked(f"robots.txt không cho phép: {url}")
        is_detail = bool(re.fullmatch(r"/it-jobs/[^/]+-\d+", urlsplit(url).path.rstrip("/")))
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                response = self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
                if not is_itviec(self.page.url):
                    raise AccessBlocked("Trang chuyển hướng ra ngoài ITviec; dừng crawl.")
                if response is None:
                    raise CrawlError("Trình duyệt không nhận được phản hồi của trang.")
                status = response.status
                self.last_http_status = status
                if status == 401:
                    self.auth_verified = False
                    raise AuthenticationRequired("HTTP 401: phiên cần xác thực lại. " + AUTH_HELP)
                if status == 403:
                    self.auth_verified = False
                    raise AccessBlocked("HTTP 403: ITviec từ chối request; chưa thể kết luận cookie hết hạn "
                                        "hay nguyên nhân chặn từ mã HTTP này.")
                if status == 429 or status >= 500:
                    if attempt < self.retries:
                        retry_after = response.headers.get("retry-after", "")
                        pause = min(60, max(2 ** (attempt + 1), int(retry_after))) if retry_after.isdigit() else 2 ** (attempt + 1)
                        LOG.warning("HTTP %s, thử lại sau %ss.", status, pause)
                        time.sleep(pause)
                        continue
                    raise CrawlError(f"HTTP {status}: hết số lần thử.")
                if status >= 400:
                    raise CrawlError(f"HTTP {status}: không tải được trang.")
                if urlsplit(self.page.url).path.rstrip("/") == "/sign_in":
                    self.auth_verified = False
                    raise AuthenticationRequired("Phiên đăng nhập không còn hiệu lực. " + AUTH_HELP)
                if re.match(r"^(Just a moment|Access denied|Attention Required)", self.page.title(), re.I):
                    raise AccessBlocked("Nhận trang xác minh/chặn truy cập thay vì nội dung ITviec.")
                selector = ".job-show-header h1" if is_detail else ".card-jobs-list .job-card"
                self.page.wait_for_selector(selector, state="attached", timeout=self.timeout * 1000)
                if is_detail:
                    self.page.wait_for_selector(".job-content", state="attached", timeout=self.timeout * 1000)
                html = self.page.content()
                if is_detail:
                    soup = BeautifulSoup(html, "html.parser")
                    if soup.select_one(".job-show-header .sign-in-view-salary"):
                        self.auth_verified = False
                        raise AuthenticationRequired("ITviec vẫn yêu cầu đăng nhập để xem lương. " + AUTH_HELP)
                    if not text_of(soup.select_one(".job-show-header .salary")):
                        raise CrawlError("Không nhận diện được trường lương để xác minh phiên đăng nhập.")
                    self.auth_verified = True
                    self.save_auth_state()
                return html, self.page.url
            except self.browser_error:
                if attempt < self.retries:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise CrawlError("Trình duyệt không tải được trang sau số lần thử đã đặt.") from None
        raise CrawlError("Trình duyệt hết số lần thử.")

    def save_auth_state(self):
        if self.context is None or not self.auth_verified:
            return
        temporary = None
        try:
            state = self.context.storage_state()
            self.auth_file.parent.mkdir(parents=True, exist_ok=True)
            # tempfile tạo file riêng tư (0600 trên POSIX); replace tránh file
            # trạng thái bị ghi dở. Không ghi state vào log hoặc JSON/CSV đầu ra.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.auth_file.parent,
                                             prefix=".itviec_auth_", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(state, stream, ensure_ascii=False)
            if os.name == "posix":
                temporary.chmod(0o600)
            temporary.replace(self.auth_file)
        except (OSError, self.browser_error):
            raise CrawlError("Không lưu được trạng thái đăng nhập. Kiểm tra quyền ghi --auth-file.") from None
        finally:
            if temporary and temporary.exists():
                temporary.unlink()

    def close(self):
        if self.context is not None and self.auth_verified:
            try:
                self.save_auth_state()
            except CrawlError:
                LOG.warning("Không cập nhật được file trạng thái khi đóng trình duyệt.")
        for resource, method in [(self.context, "close"), (self.browser, "close"), (self.playwright, "stop")]:
            if resource is not None:
                try:
                    getattr(resource, method)()
                except self.browser_error:
                    LOG.warning("Một tài nguyên trình duyệt đã đóng hoặc không phản hồi.")
        self.page = self.context = self.browser = self.playwright = None
        super().close()



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



def write_outputs(output_dir: Path, jobs: list[dict], metadata: dict, errors: list[dict]):
    """Write only source records; keep a snapshot per crawl run."""
    distinct = {}
    for job in jobs:
        distinct[raw_job_identity(job)[0]] = job
    jobs = list(distinct.values())
    export_metadata = {**metadata, "collected_count": len(jobs), "distinct_job_count": len(jobs)}
    document = {"metadata": export_metadata, "jobs": jobs, "errors": errors}
    destinations = [output_dir]
    run_id = metadata.get("crawl_run_id")
    if run_id:
        folder = run_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(run_id)) else (
            "run_" + hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:32])
        destinations.append(output_dir / "runs" / folder)
    for destination in destinations:
        destination.mkdir(parents=True, exist_ok=True)
        atomic_json(destination / "itviec_jobs_raw.json", document)


def database_timestamp(value, field: str) -> datetime:
    """Require a source timestamp with a timezone, never fabricate an observation."""
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise StorageError("Thiếu thời gian ISO 8601 có múi giờ cho trường " + field + ".") from None
    return timestamp


def database_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise StorageError("Dữ liệu không mã hóa được thành JSON hợp lệ để lưu PostgreSQL.") from None


class PostgresSink:
    """Store one raw job per run; no database credentials in logs or metadata."""

    RUN_SQL = """
        INSERT INTO raw.crawl_runs
            (crawl_run_id, source, started_at, finished_at, requested_count,
             collected_count, status, metadata, errors)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (crawl_run_id) DO UPDATE SET
            finished_at = EXCLUDED.finished_at,
            collected_count = EXCLUDED.collected_count,
            status = EXCLUDED.status,
            metadata = EXCLUDED.metadata,
            errors = EXCLUDED.errors
    """
    JOB_SQL = """
        INSERT INTO raw.job_postings
            (job_id, crawl_run_id, source_job_id, job_url, observed_at, raw_payload)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (job_id, crawl_run_id) DO NOTHING
        RETURNING job_id
    """

    def __init__(self, args):
        self.conn = None
        try:
            import psycopg
            from psycopg.types.json import Jsonb
        except ImportError:
            raise StorageError('Thiếu thư viện PostgreSQL. Chạy: python -m pip install "psycopg[binary]>=3.1,<4"') from None
        self.db_errors = psycopg.Error
        self.jsonb = Jsonb
        options = {"connect_timeout": args.db_connect_timeout,
                   "application_name": "itviec_crawler", "autocommit": True}
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
                raise StorageError("Không nhập được mật khẩu PostgreSQL; dùng terminal tương tác hoặc cấu hình .pgpass.") from None
        try:
            self.conn = psycopg.connect(args.db_dsn or "", **options)
        except (psycopg.Error, ValueError, TypeError):
            raise StorageError("Không kết nối được PostgreSQL. Kiểm tra DATABASE_URL/PG* hoặc --db-host/--db-port/--db-name/--db-user.") from None

    def _payload(self, value):
        # Validate before creating the lazy Jsonb adapter; never mutate source data.
        database_json(value)
        return self.jsonb(value, dumps=database_json)

    def _error(self, exc):
        if getattr(exc, "sqlstate", None) in {"42P01", "3F000", "42703"}:
            return StorageError("Chưa có bảng/cột raw đúng cấu trúc. Chạy data_preprocessing/itviec_db/itviec_schema.sql trong database đang kết nối.")
        return StorageError("Không ghi được dữ liệu PostgreSQL; crawler đã dừng. Kiểm tra kết nối, quyền ghi và schema raw.")

    def _write_run(self, cursor, metadata, errors, status):
        started = database_timestamp(metadata["started_at"], "started_at")
        finished = database_timestamp(metadata["finished_at"], "finished_at") if metadata.get("finished_at") else None
        cursor.execute(self.RUN_SQL, (
            metadata["crawl_run_id"], "itviec", started, finished,
            metadata["requested_count"], metadata["collected_count"], status,
            self._payload(metadata), self._payload(errors),
        ))

    def start_run(self, metadata, errors):
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT job_id, crawl_run_id, source_job_id, job_url, observed_at, raw_payload
                        FROM raw.job_postings LIMIT 0
                    """)
                    self._write_run(cursor, metadata, errors, "running")
        except self.db_errors as exc:
            raise self._error(exc) from None

    def save_job(self, raw_job, metadata, errors) -> bool:
        job_id, source_job_id, url = raw_job_identity(raw_job)
        observed = database_timestamp(raw_job.get("crawled_at"), "crawled_at")
        payload = self._payload(raw_job)
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cursor:
                    cursor.execute(self.JOB_SQL, (job_id, metadata["crawl_run_id"], source_job_id,
                                                  url, observed, payload))
                    if cursor.fetchone() is None:
                        return False
                    self._write_run(cursor, metadata, errors, "running")
        except self.db_errors as exc:
            raise self._error(exc) from None
        return True

    def finish_run(self, metadata, errors):
        status = ("interrupted" if metadata.get("interrupted") else
                  "failed" if metadata.get("storage_failed") else
                  "succeeded" if metadata.get("complete") else
                  "partial" if metadata["collected_count"] else "failed")
        try:
            with self.conn.transaction():
                with self.conn.cursor() as cursor:
                    self._write_run(cursor, metadata, errors, status)
        except self.db_errors as exc:
            raise self._error(exc) from None

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except self.db_errors:
                LOG.warning("Không đóng được kết nối PostgreSQL.")
            finally:
                self.conn = None


def build_parser(*, default_output_dir=Path("itviec_data/raw"), description=None):
    parser = argparse.ArgumentParser(description=description or __doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=20, help="Số tin hoàn chỉnh cần lấy (mặc định 20).")
    parser.add_argument("--output-dir", type=Path, default=default_output_dir,
                        help="Thư mục dùng khi --save-json hoặc --save-html.")
    parser.add_argument("--delay", type=float, default=1.5, help="Khoảng nghỉ giữa request, giây.")
    parser.add_argument("--timeout", type=float, default=40, help="Timeout request, giây.")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=5, help="Giới hạn trang danh sách để tránh chạy vô hạn.")
    parser.add_argument("--save-html", action="store_true", help="Lưu thêm HTML gốc vào html/.")
    parser.add_argument("--save-json", action="store_true", help="Lưu thêm bản JSON dự phòng; mặc định chỉ ghi PostgreSQL.")
    parser.add_argument("--db-dsn", default=os.environ.get("DATABASE_URL", ""),
                        help="Chuỗi kết nối PostgreSQL; mặc định DATABASE_URL, nếu thiếu dùng PG* hoặc .pgpass.")
    parser.add_argument("--db-host", help="Host PostgreSQL, ghi đè giá trị trong DSN/PGHOST.")
    parser.add_argument("--db-port", type=int, help="Port PostgreSQL, ghi đè DSN/PGPORT.")
    parser.add_argument("--db-name", help="Tên database, ghi đè DSN/PGDATABASE.")
    parser.add_argument("--db-user", help="Tài khoản PostgreSQL, ghi đè DSN/PGUSER.")
    parser.add_argument("--db-password-prompt", action="store_true", help="Nhập mật khẩu DB riêng trong terminal.")
    parser.add_argument("--db-connect-timeout", type=int, default=10, help="Timeout kết nối PostgreSQL, giây.")
    parser.add_argument("--cookie-file", type=Path, help="Header Cookie ITviec; dùng cùng --browser để nạp và lưu phiên cho lần sau.")
    parser.add_argument("--browser", action="store_true", help="Dùng trình duyệt và tự lưu/nạp trạng thái đăng nhập.")
    parser.add_argument("--login", action="store_true", help="Mở trình duyệt để đăng nhập một lần rồi crawl; tự bật --browser.")
    parser.add_argument("--headed", action="store_true", help="Hiện cửa sổ trình duyệt khi dùng --browser.")
    parser.add_argument("--auth-file", type=Path, default=Path(__file__).resolve().with_name(".itviec_auth.json"),
                        help="File trạng thái đăng nhập riêng tư, mặc định cạnh file code.")
    return parser


def parse_args(argv=None, *, default_output_dir=Path("itviec_data/raw"), description=None, parser=None):
    if parser is None:
        parser = build_parser(default_output_dir=default_output_dir, description=description)
    args = parser.parse_args(argv)
    args.browser = args.browser or args.login
    if args.login and args.cookie_file:
        parser.error("Chọn --login hoặc --browser --cookie-file; không dùng cả hai cách khởi tạo phiên.")
    if args.headed and not args.browser:
        parser.error("--headed cần --browser hoặc --login.")
    if args.limit < 1 or args.max_pages < 1 or args.timeout <= 0 or args.delay < 0 or args.retries < 0:
        parser.error("limit/max-pages/timeout phải > 0; delay/retries phải >= 0.")
    if args.db_connect_timeout < 1 or (args.db_port is not None and not 1 <= args.db_port <= 65535):
        parser.error("db-connect-timeout phải > 0; db-port trong khoảng 1..65535.")
    return args


def run(args) -> int:
    jobs, errors = [], []
    seen_jobs, seen_job_ids, visited_pages = set(), set(), set()
    metadata = {"source": START_URL, "started_at": datetime.now(timezone.utc).isoformat(),
                "crawl_run_id": getattr(args, "crawl_run_id", None) or new_crawl_run_id(),
                "stage": "crawl", "data_format": "raw", "storage": "postgresql",
                "requested_count": args.limit, "collected_count": 0, "complete": False,
                "scope": "job detail pages in the current session", "cookie_file_used": False,
                "transport": "browser" if args.browser else "requests", "authentication_required": False,
                "access_blocked": False, "storage_failed": False, "listing_pages": []}
    client = sink = None
    interrupted = False
    run_started = False
    save_json = getattr(args, "save_json", False)
    args.crawl_result = {field: metadata[field] for field in
                         ("crawl_run_id", "collected_count", "complete", "storage_failed")}
    args.crawl_result["interrupted"] = False
    try:
        # Fail before any HTTP access if DB credentials/schema are not ready.
        sink = PostgresSink(args)
        sink.start_run(metadata, errors)
        run_started = True
        LOG.info("Lần crawl: %s; lưu dữ liệu vào raw.job_postings.", metadata["crawl_run_id"])
        client = BrowserClient(args.delay, args.timeout, args.retries, args.auth_file) if args.browser else Client(args.delay, args.timeout, args.retries)
        if args.save_html:
            (args.output_dir / "html").mkdir(parents=True, exist_ok=True)
        client.check_robots()
        if args.browser:
            client.start(login=args.login, headed=args.headed, cookie_file=args.cookie_file)
        elif args.cookie_file:
            client.load_cookie_file(args.cookie_file)
            metadata["cookie_file_used"] = True
        warned_cookie_session = False
        page_url = START_URL
        while page_url and len(jobs) < args.limit and len(visited_pages) < args.max_pages:
            if canonical(page_url) + "?" + urlsplit(page_url).query in visited_pages:
                break
            visited_pages.add(canonical(page_url) + "?" + urlsplit(page_url).query)
            LOG.info("Đọc trang danh sách %s", page_url)
            client.phase = "listing"
            listing_html, fetched_page = client.get(page_url)
            metadata["listing_pages"].append(fetched_page)
            cards, next_url = parse_listing(listing_html, fetched_page)
            if args.save_html:
                (args.output_dir / "html" / f"listing_{len(visited_pages):02d}.html").write_text(
                    listing_html, encoding="utf-8")
            if not cards:
                raise CrawlError("Không thấy tin trong danh sách. Kiểm tra selector hoặc trang xác minh.")
            for card in cards:
                normalized = canonical(card["url"])
                if normalized in seen_jobs:
                    continue
                seen_jobs.add(normalized)
                try:
                    client.phase = "detail"
                    html, fetched_url = client.get(card["url"])
                    if args.save_html:
                        slug = urlsplit(card["url"]).path.rsplit("/", 1)[-1]
                        (args.output_dir / "html" / f"{slug}.html").write_text(html, encoding="utf-8")
                    raw_job = parse_job(html, fetched_url, card)
                    if args.cookie_file and not args.browser and raw_job["salary"]["visibility"] == "login_required" and not warned_cookie_session:
                        LOG.warning("Đã nạp cookie nhưng ITviec vẫn yêu cầu đăng nhập để xem lương. "
                                    "Kiểm tra cookie còn hiệu lực và lấy từ request trang chi tiết đã đăng nhập.")
                        warned_cookie_session = True
                    identity = raw_job_identity(raw_job)[0]
                    if identity in seen_job_ids:
                        continue
                    next_count = len(jobs) + 1
                    progress = {**metadata, "collected_count": next_count, "complete": next_count == args.limit}
                    if not sink.save_job(raw_job, progress, errors):
                        seen_job_ids.add(identity)
                        continue
                    # Count only records whose insert/progress transaction committed.
                    seen_job_ids.add(identity)
                    jobs.append(raw_job)
                    metadata.update(collected_count=next_count, complete=next_count == args.limit)
                    LOG.info("[%s/%s] Đã lưu PostgreSQL: %s — %s", len(jobs), args.limit,
                             raw_job["title"], raw_job["company_name"])
                    if save_json:
                        write_outputs(args.output_dir, jobs, metadata, errors)
                except AccessBlocked:
                    raise
                except CrawlError as exc:
                    LOG.warning("Bỏ qua %s: %s", card["url"], exc)
                    errors.append({"url": card["url"], "error": str(exc)})
                if len(jobs) == args.limit:
                    break
            page_url = next_url
    except KeyboardInterrupt:
        interrupted = True
        errors.append({"error": "Người dùng dừng crawler; các tin đã commit được giữ trong PostgreSQL."})
    except AuthenticationRequired as exc:
        metadata["authentication_required"] = True
        LOG.error("%s", exc)
        errors.append({"error": str(exc), "type": "authentication_required"})
    except AccessBlocked as exc:
        metadata["access_blocked"] = True
        diagnostic = {"error": str(exc), "type": "access_blocked", "transport": metadata["transport"],
                      "phase": client.phase, "url": client.last_requested_url,
                      "http_status": client.last_http_status}
        errors.append(diagnostic)
        LOG.error("%s (transport=%s, phase=%s, url=%s, http_status=%s)", exc,
                  diagnostic["transport"], diagnostic["phase"], diagnostic["url"], diagnostic["http_status"])
    except StorageError as exc:
        metadata["storage_failed"] = True
        metadata["complete"] = False
        errors.append({"error": str(exc), "type": "storage_error", "stage": "postgresql"})
        LOG.error("%s", exc)
    except CrawlError as exc:
        LOG.error("%s", exc)
        errors.append({"error": str(exc)})
    except OSError:
        errors.append({"error": "Không ghi được file dự phòng/HTML đã yêu cầu.", "type": "file_error"})
        LOG.error("Không ghi được file dự phòng/HTML đã yêu cầu.")
    finally:
        try:
            if client is not None:
                if args.browser:
                    metadata["cookie_file_used"] = client.cookie_file_used
                client.close()
        finally:
            metadata.update(collected_count=len(jobs),
                            complete=len(jobs) == args.limit and not metadata["storage_failed"] and not interrupted,
                            finished_at=datetime.now(timezone.utc).isoformat(), interrupted=interrupted)
            if sink is not None:
                try:
                    if run_started:
                        sink.finish_run(metadata, errors)
                except StorageError as exc:
                    metadata["storage_failed"] = True
                    metadata["complete"] = False
                    errors.append({"error": str(exc), "type": "storage_error", "stage": "postgresql"})
                    LOG.error("Không cập nhật được trạng thái cuối trong PostgreSQL. %s", exc)
                finally:
                    sink.close()
            if save_json:
                try:
                    write_outputs(args.output_dir, jobs, metadata, errors)
                except (OSError, ValueError):
                    LOG.error("Không lưu được bản JSON dự phòng; các tin đã commit vẫn nằm trong PostgreSQL.")
    args.crawl_result = {field: metadata[field] for field in
                         ("crawl_run_id", "collected_count", "complete", "storage_failed", "interrupted")}
    LOG.info("Đã lưu %s/%s tin vào PostgreSQL; crawl_run_id=%s.", len(jobs), args.limit, metadata["crawl_run_id"])
    if metadata["storage_failed"]:
        return 4
    if metadata["authentication_required"]:
        return 3
    if len(jobs) < args.limit:
        LOG.error("Chưa đủ số tin yêu cầu; xem raw.crawl_runs để kiểm tra trạng thái và errors.")
    return 0 if metadata["complete"] else 2 if jobs else 1



def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
