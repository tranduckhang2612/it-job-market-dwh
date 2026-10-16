#!/usr/bin/env python3
"""Crawl dữ liệu tuyển dụng TopDev (topdev.vn) và lưu vào PostgreSQL schema raw.

Python >= 3.10. Cài dependency:
    python -m pip install requests beautifulsoup4 "psycopg[binary]>=3.1,<4"

Chạy SQL data_preprocessing/topdev_db/topdev_schema.sql trong database trước khi lưu DB.
Mặc định lấy 20 tin từ TopDev API (td/v2/jobs) kết hợp chi tiết tin:
    python crawl_topdev.py --limit 20

raw.crawl_runs lưu metadata/errors/trạng thái mỗi lần chạy (source='topdev').
raw.job_postings.raw_payload lưu JSON gốc mỗi tin (topdev:<id>).
Khóa (job_id, crawl_run_id) giữ lịch sử quan sát; observed_at lưu thời điểm crawl.
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
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit, quote

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


try:
    import requests
except ImportError:
    requests = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

BASE_URL = "https://topdev.vn"
API_BASE_URL = "https://api.topdev.vn/td/v2"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 TopDevCrawler/1.0"
LOG = logging.getLogger("topdev")
VIETNAM_TZ = timezone(timedelta(hours=7))

TOPDEV_LIST_FIELDS = (
    "id,slug,title,salary,company,extra_skills,skills_str,skills_arr,skills_ids,"
    "job_types_str,job_levels_str,job_levels_arr,addresses,detail_url,job_url,"
    "descriptions,benefits,requirements,experience,contracts,industries,published_at,created_at,status"
)

TOPDEV_DETAIL_FIELDS = (
    "id,title,salary,company,extra_skills,skills_str,skills_arr,skills_ids,"
    "job_types_str,job_levels_str,job_levels_arr,addresses,detail_url,slug,description,"
    "descriptions,benefits,requirements,experience,contracts,industries,published_at,created_at,"
    "status,responsibilities_original,requirements_original,benefits_original,recruitment_process_original"
)


class CrawlError(RuntimeError):
    """Không tải hoặc phân tích được dữ liệu TopDev."""


class AccessBlocked(CrawlError):
    """Dừng khi bị chặn kết nối hoặc rate-limit."""


class StorageError(RuntimeError):
    """Lỗi lưu trữ PostgreSQL."""


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


def new_crawl_run_id() -> str:
    now = datetime.now(timezone.utc)
    random_hex = uuid.uuid4().hex[:8]
    return f"topdev_{now.strftime('%Y%m%d_%H%M%S')}_{random_hex}"


def raw_job_identity(job: dict) -> tuple[str, str | None, str]:
    source_id = str(job.get("id") or job.get("source_job_id") or "").strip() or None
    url = job.get("detail_url") or job.get("job_url") or ""
    if not url and source_id:
        slug = job.get("slug") or f"job-{source_id}"
        url = f"https://topdev.vn/detail-jobs/{slug}-{source_id}"
    url = canonical(url) if url else ""
    if source_id:
        job_id = f"topdev:{source_id}"
    elif url:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        job_id = f"topdev:url_{h}"
    else:
        raise CrawlError("Bản ghi thiếu cả ID lẫn URL để nhận diện tin TopDev.")
    return job_id, source_id, url


def atomic_json(path: Path, payload: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as temp:
        json.dump(payload, temp, ensure_ascii=False, indent=2)
        temp_name = temp.name
    os.replace(temp_name, path)


def write_outputs(output_dir: Path, jobs: list[dict], metadata: dict, errors: list[dict]):
    distinct = {}
    for job in jobs:
        try:
            identity = raw_job_identity(job)[0]
            distinct[identity] = job
        except CrawlError:
            continue
    jobs = list(distinct.values())
    export_metadata = {**metadata, "collected_count": len(jobs), "distinct_job_count": len(jobs)}
    document = {"metadata": export_metadata, "jobs": jobs, "errors": errors}
    destinations = [output_dir]
    run_id = metadata.get("crawl_run_id")
    if run_id:
        folder = run_id if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", str(run_id)) else (
            "run_" + hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()[:32])
        destinations.append(output_dir / "runs" / folder)
    for dest in destinations:
        dest.mkdir(parents=True, exist_ok=True)
        atomic_json(dest / "topdev_jobs_raw.json", document)


class TopDevHttpClient:
    """Client gửi request tới TopDev API và Web, hỗ trợ cả requests lẫn urllib fallback."""

    def __init__(self, cookie_file: Path | None = None, cookie: str | None = None,
                 token: str | None = None, timeout: int = 15):
        self.timeout = timeout
        self.cookie_file_used = None
        self.headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://topdev.vn/it-jobs",
        }

        raw_auth = None
        if cookie and str(cookie).strip():
            raw_auth = str(cookie).strip()
        elif cookie_file and Path(cookie_file).is_file():
            try:
                raw_auth = Path(cookie_file).read_text(encoding="utf-8").strip()
                self.cookie_file_used = str(cookie_file)
            except OSError:
                LOG.warning("Không đọc được file cookie: %s", cookie_file)

        if raw_auth:
            # Bóc tách nếu người dùng copy kèm tiền tố "Cookie: " hoặc "Authorization: "
            if raw_auth.lower().startswith("cookie:"):
                raw_auth = raw_auth[7:].strip()
            elif raw_auth.lower().startswith("authorization:"):
                raw_auth = raw_auth[14:].strip()

            if raw_auth.startswith("Bearer ") or (raw_auth.startswith("eyJ") and "." in raw_auth):
                token_val = raw_auth if raw_auth.startswith("Bearer ") else f"Bearer {raw_auth}"
                self.headers["Authorization"] = token_val
                LOG.info("Đã áp dụng Bearer token xác thực TopDev.")
            else:
                self.headers["Cookie"] = raw_auth
                LOG.info("Đã áp dụng Cookie đăng nhập TopDev.")

        if token and str(token).strip():
            t = str(token).strip()
            if t.lower().startswith("authorization:"):
                t = t[14:].strip()
            token_val = t if t.startswith("Bearer ") else f"Bearer {t}"
            self.headers["Authorization"] = token_val
            LOG.info("Đã áp dụng Authorization token cho TopDev.")

        if requests is not None:
            self.session = requests.Session()
            self.session.headers.update(self.headers)
        else:
            self.session = None

    def get_json(self, url: str, params: dict | None = None) -> dict:
        if self.session is not None:
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 429:
                    raise AccessBlocked(f"TopDev trả về mã 429 Too Many Requests: {url}")
                if resp.status_code == 403:
                    raise AccessBlocked(f"Bị chặn truy cập TopDev HTTP 403: {url}")
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as exc:
                raise CrawlError(f"Lỗi request TopDev {url}: {exc}") from exc
        else:
            import urllib.request
            import urllib.parse
            if params:
                query_str = urllib.parse.urlencode(params)
                full_url = f"{url}?{query_str}" if "?" not in url else f"{url}&{query_str}"
            else:
                full_url = url
            req = urllib.request.Request(full_url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    content = response.read().decode("utf-8")
                    return json.loads(content)
            except Exception as exc:
                raise CrawlError(f"Lỗi urllib request TopDev {full_url}: {exc}") from exc

    def get_text(self, url: str) -> str:
        if self.session is not None:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                raise CrawlError(f"Lỗi request web TopDev {url}: {exc}") from exc
        else:
            import urllib.request
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return response.read().decode("utf-8", errors="replace")
            except Exception as exc:
                raise CrawlError(f"Lỗi urllib request {url}: {exc}") from exc

    def close(self):
        if self.session is not None:
            self.session.close()


def database_timestamp(value, field: str) -> datetime:
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise StorageError(f"Thiếu thời gian ISO 8601 có múi giờ cho trường {field}.") from None
    return ts


class PostgresSink:
    """Ghi trực tiếp bản ghi raw vào bảng raw.crawl_runs và raw.job_postings."""

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
        options = {
            "connect_timeout": getattr(args, "db_connect_timeout", 10),
            "application_name": "topdev_crawler",
            "autocommit": True,
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
                raise StorageError("Không nhập được mật khẩu PostgreSQL.") from None
        try:
            self.conn = psycopg.connect(getattr(args, "db_dsn", "") or "", **options)
        except (psycopg.Error, ValueError, TypeError) as exc:
            raise StorageError(f"Không kết nối được PostgreSQL: {exc}") from None

    def start_run(self, metadata: dict, errors: list):
        started_at = database_timestamp(metadata["started_at"], "started_at")
        with self.conn.cursor() as cur:
            cur.execute(self.RUN_SQL, (
                metadata["crawl_run_id"], "topdev", started_at, None,
                metadata["requested_count"], 0, "running",
                self.jsonb(metadata), self.jsonb(errors)
            ))

    def save_job(self, job: dict, progress: dict, errors: list) -> bool:
        job_id, source_id, url = raw_job_identity(job)
        observed_at = database_timestamp(job.get("observed_at"), "observed_at")
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                cur.execute(self.JOB_SQL, (
                    job_id, progress["crawl_run_id"], source_id, url,
                    observed_at, self.jsonb(job)
                ))
                inserted = bool(cur.fetchone())
                status = "succeeded" if progress.get("complete") else "partial"
                finished = database_timestamp(progress["finished_at"], "finished_at") if progress.get("finished_at") else None
                cur.execute(self.RUN_SQL, (
                    progress["crawl_run_id"], "topdev",
                    database_timestamp(progress["started_at"], "started_at"),
                    finished, progress["requested_count"], progress["collected_count"],
                    status, self.jsonb(progress), self.jsonb(errors)
                ))
        return inserted

    def finish_run(self, metadata: dict, errors: list):
        if self.conn is None:
            return
        status = "failed" if metadata.get("storage_failed") else (
            "interrupted" if metadata.get("interrupted") else (
                "succeeded" if metadata.get("complete") else "partial"
            )
        )
        started_at = database_timestamp(metadata["started_at"], "started_at")
        finished_at = database_timestamp(metadata["finished_at"], "finished_at") if metadata.get("finished_at") else None
        with self.conn.cursor() as cur:
            cur.execute(self.RUN_SQL, (
                metadata["crawl_run_id"], "topdev", started_at, finished_at,
                metadata["requested_count"], metadata["collected_count"], status,
                self.jsonb(metadata), self.jsonb(errors)
            ))

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None


def fetch_job_details(client: TopDevHttpClient, job: dict) -> dict:
    """Bổ sung nội dung chi tiết (responsibilities, requirements, benefits) từ API chi tiết."""
    job_id = job.get("id")
    if not job_id:
        return job
    try:
        url = f"{API_BASE_URL}/jobs/{job_id}"
        detail_data = client.get_json(url, params={"fields[job]": TOPDEV_DETAIL_FIELDS})
        if isinstance(detail_data, dict) and "data" in detail_data and isinstance(detail_data["data"], dict):
            enriched = {**job, **detail_data["data"]}
            return enriched
    except Exception as exc:
        LOG.debug("Không lấy được API chi tiết cho job %s: %s", job_id, exc)
    return job


def run(args) -> int:
    crawl_run_id = getattr(args, "crawl_run_id", None) or new_crawl_run_id()
    args.crawl_run_id = crawl_run_id
    limit = max(1, getattr(args, "limit", 20))
    started_at = datetime.now(timezone.utc)
    metadata = {
        "crawl_run_id": crawl_run_id,
        "source": "topdev",
        "started_at": started_at.isoformat(),
        "requested_count": limit,
        "collected_count": 0,
        "complete": False,
        "storage_failed": False,
        "interrupted": False,
    }
    errors = []
    jobs = []
    seen_ids = set()

    client = None
    sink = None
    run_started = False
    save_json = getattr(args, "save_json", False)

    use_db = not getattr(args, "no_db", False) and not (getattr(args, "output", None) and getattr(args, "no_db", False))
    if getattr(args, "output", None) and not any(getattr(args, f, None) is not None for f in ("db_host", "db_port", "db_name", "db_user", "db_dsn")):
        use_db = False

    try:
        client = TopDevHttpClient(
            cookie_file=getattr(args, "cookie_file", None),
            cookie=getattr(args, "cookie", None),
            token=getattr(args, "token", None),
            timeout=getattr(args, "timeout", 15)
        )
        if use_db:
            try:
                sink = PostgresSink(args)
                sink.start_run(metadata, errors)
                run_started = True
                LOG.info("Khởi tạo phiên crawl TopDev trong PostgreSQL: crawl_run_id=%s, target=%s", crawl_run_id, limit)
            except StorageError as exc:
                LOG.error("Lỗi kết nối PostgreSQL: %s", exc)
                metadata["storage_failed"] = True
                return 4
        else:
            LOG.info("Chạy chế độ file offline (không kết nối PostgreSQL).")

        page = 1
        page_size = min(50, limit)
        while len(jobs) < limit:
            params = {
                "page": page,
                "page_size": page_size,
                "fields[job]": TOPDEV_LIST_FIELDS
            }
            url = f"{API_BASE_URL}/jobs"
            LOG.info("Đang tải danh sách tin TopDev trang %s...", page)
            try:
                res = client.get_json(url, params=params)
            except AccessBlocked as exc:
                errors.append({"stage": "list_fetch", "page": page, "error": str(exc)})
                LOG.error("Bị chặn khi lấy danh sách trang %s: %s", page, exc)
                break
            except CrawlError as exc:
                errors.append({"stage": "list_fetch", "page": page, "error": str(exc)})
                LOG.warning("Lỗi khi tải trang %s: %s", page, exc)
                break

            items = res.get("data", []) if isinstance(res, dict) else []
            if not items:
                LOG.info("Hết tin tại trang %s.", page)
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                observed_time = datetime.now(VIETNAM_TZ).isoformat()
                item_with_meta = {
                    **item,
                    "observed_at": observed_time,
                    "crawl_run_id": crawl_run_id,
                    "source": "topdev",
                }
                # Lấy thêm chi tiết trách nhiệm, yêu cầu, phúc lợi
                full_job = fetch_job_details(client, item_with_meta)
                try:
                    job_id, _, _ = raw_job_identity(full_job)
                except CrawlError as exc:
                    errors.append({"error": str(exc), "stage": "identity"})
                    continue

                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                next_count = len(jobs) + 1
                progress = {
                    **metadata,
                    "collected_count": next_count,
                    "complete": next_count == limit
                }
                if sink is not None:
                    try:
                        sink.save_job(full_job, progress, errors)
                    except StorageError as exc:
                        metadata["storage_failed"] = True
                        errors.append({"error": str(exc), "stage": "postgresql"})
                        LOG.error("Lỗi ghi PostgreSQL job %s: %s", job_id, exc)
                        raise

                jobs.append(full_job)
                metadata["collected_count"] = len(jobs)
                metadata["complete"] = len(jobs) == limit
                title = full_job.get("title") or "Unknown"
                company = full_job.get("company", {}).get("display_name") if isinstance(full_job.get("company"), dict) else "N/A"
                if sink is not None:
                    LOG.info("[%s/%s] Đã lưu PostgreSQL: %s — %s (%s)", len(jobs), limit, title, company, job_id)
                else:
                    LOG.info("[%s/%s] Đã thu thập: %s — %s (%s)", len(jobs), limit, title, company, job_id)

                if save_json and getattr(args, "output_dir", None):
                    write_outputs(Path(args.output_dir), jobs, metadata, errors)

                if len(jobs) >= limit:
                    break
                time.sleep(0.3)

            meta_data = res.get("meta", {})
            last_page = meta_data.get("last_page", 1) if isinstance(meta_data, dict) else 1
            if page >= last_page:
                break
            page += 1

    except KeyboardInterrupt:
        metadata["interrupted"] = True
        LOG.warning("Crawl bị ngắt bởi người dùng.")
    except StorageError:
        metadata["storage_failed"] = True
    finally:
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        metadata["complete"] = len(jobs) == limit and not metadata["storage_failed"] and not metadata["interrupted"]
        if client is not None:
            client.close()
        if sink is not None and run_started:
            try:
                sink.finish_run(metadata, errors)
            except Exception as exc:
                LOG.warning("Không cập nhật được trạng thái cuối trong DB: %s", exc)
            sink.close()
        if save_json and getattr(args, "output_dir", None):
            try:
                write_outputs(Path(args.output_dir), jobs, metadata, errors)
            except Exception:
                pass
        if getattr(args, "output", None):
            try:
                out_path = Path(args.output)
                out_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(out_path, {"metadata": metadata, "jobs": jobs, "errors": errors})
                LOG.info("Đã lưu kết quả crawl vào file: %s", out_path)
            except Exception as exc:
                LOG.warning("Lỗi ghi file output %s: %s", args.output, exc)

    args.crawl_result = {
        "crawl_run_id": crawl_run_id,
        "collected_count": len(jobs),
        "complete": metadata["complete"],
        "storage_failed": metadata["storage_failed"],
        "interrupted": metadata["interrupted"],
    }
    LOG.info("Hoàn tất crawl TopDev: %s/%s tin đã lưu (crawl_run_id=%s)", len(jobs), limit, crawl_run_id)
    if metadata["storage_failed"]:
        return 4
    return 0 if metadata["complete"] else (2 if jobs else 1)


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description or __doc__)
    parser.add_argument("--limit", type=int, default=20, help="Số tin cần crawl (mặc định: 20)")
    parser.add_argument("--cookie", help="Chuỗi cookie đăng nhập TopDev (trực tiếp từ DevTools)")
    parser.add_argument("--cookie-file", type=Path, default=None, help="Đường dẫn file chứa cookie đăng nhập")
    parser.add_argument("--token", help="Bearer token xác thực nếu có (hoặc Authorization header)")
    parser.add_argument("--output", type=Path, default=None, help="Đường dẫn file JSON xuất kết quả trực tiếp")
    parser.add_argument("--output-dir", type=Path, default=None, help="Thư mục xuất file JSON dự phòng")
    parser.add_argument("--save-json", action="store_true", help="Lưu thêm bản JSON thô ra đĩa")
    parser.add_argument("--no-db", action="store_true", help="Chạy không kết nối PostgreSQL (chỉ crawl xuất JSON)")
    parser.add_argument("--timeout", type=int, default=15, help="Thời gian chờ request (giây)")

    # DB arguments
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


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
