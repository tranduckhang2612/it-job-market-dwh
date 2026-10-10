#!/usr/bin/env python3
"""Entry point: crawl ITviec → raw PostgreSQL → làm sạch/ánh xạ → staging.

Python >= 3.10. Cài dependency trong requirements.txt trước khi chạy.
Nếu chưa có cấu hình DB riêng, dùng PostgreSQL trong docker/docker-compose.yml.
Chạy từ thư mục gốc project:
    docker compose -f docker/docker-compose.yml up -d postgres
    python main_itviec.py
    python main_itviec.py --check-db
    python main_itviec.py --process-run-id CRAWL_RUN_ID

Mặc định lấy 20 tin bằng requests và tự dùng
data_preprocessing/itviec_crawl_data/cookie_itviec.txt.
--cookie-file và --limit cho phép thay file cookie và số tin cần lấy.

Bước 1 lưu JSON gốc vào raw.job_postings, ghi tiến độ vào raw.crawl_runs.
Bước 2 đọc đúng crawl_run_id, ánh xạ 30 trường, ghi staging và kiểm định đọc lại.
DWH đọc staging.dwh_ready_jobs: batch lỗi/đang xử lý chưa được công bố.
--process-run-id xử lý lại một batch raw đã lưu mà không crawl lại.
Mặc định không xuất JSON/CSV ra đĩa. Các tùy chọn --db-* áp dụng cho cả hai bước.
CLI/DATABASE_URL/PG* được ưu tiên; --no-compose-db tắt cấu hình từ Compose.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from data_preprocessing.itviec_crawl_data import crawl_itviec
from data_preprocessing.itviec_normalize_data import normalize_itviec


LOG = logging.getLogger("itviec.pipeline")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_COOKIE_FILE = PROJECT_ROOT / "data_preprocessing" / "itviec_crawl_data" / "cookie_itviec.txt"
_CONNECTION_ENV = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
                   "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE")


def _configure_compose_database(args) -> str:
    """Use Compose only as a fallback; never expose its resolved credentials."""
    if (getattr(args, "db_dsn", "") or
            any(getattr(args, field, None) is not None
                for field in ("db_host", "db_port", "db_name", "db_user")) or
            any(name in os.environ for name in _CONNECTION_ENV)):
        return "external"
    compose = PROJECT_ROOT / "docker" / "docker-compose.yml"
    if getattr(args, "no_compose_db", False) or not compose.is_file():
        return "libpq"
    error = crawl_itviec.StorageError(
        "Không đọc được cấu hình PostgreSQL từ Docker Compose. Kiểm tra service postgres, "
        "POSTGRES_USER/POSTGRES_DB/POSTGRES_PASSWORD và cổng TCP được publish; "
        "hoặc dùng --db-* / DATABASE_URL / --no-compose-db.")
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "config", "--format", "json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise error
        service = json.loads(result.stdout)["services"]["postgres"]
        environment = service["environment"]
        user, database, password = (environment[name] for name in
                                    ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD"))
        if not all(isinstance(value, str) and value for value in (user, database, password)):
            raise error
        mapping = next(port for port in service.get("ports", [])
                       if str(port.get("target")) == "5432" and port.get("protocol", "tcp") == "tcp")
        published = str(mapping["published"])
        if not published.isdecimal() or not 1 <= int(published) <= 65535:
            raise error
        host = mapping.get("host_ip") or "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if not isinstance(host, str):
            raise error
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, StopIteration, AttributeError):
        raise error from None
    args.db_host, args.db_port, args.db_name, args.db_user = host, int(published), database, user
    args.db_password = password
    return "compose"


def check_database(args) -> int:
    """Check the connection, full schema and required write privileges without writing."""
    sink = None
    try:
        sink = crawl_itviec.PostgresSink(args)
        tables = {
            "raw.crawl_runs": (("crawl_run_id", "source", "started_at", "finished_at", "requested_count",
                                "collected_count", "status", "metadata", "errors", "ingested_at"),
                               ("SELECT", "INSERT", "UPDATE")),
            "raw.job_postings": (("job_id", "crawl_run_id", "source_job_id", "job_url", "observed_at",
                                  "raw_payload", "ingested_at"), ("SELECT", "INSERT")),
            "staging.job_observations": (("observation_id", "normalized_at", "normalization_version") +
                                            normalize_itviec.DB_SCALAR_FIELDS, ("SELECT", "INSERT", "UPDATE", "DELETE")),
        }
        for table, columns in normalize_itviec.STAGING_CHILD_COLUMNS.items():
            tables["staging." + table] = (("observation_id", "item_order") + columns,
                                             ("SELECT", "INSERT", "DELETE"))
        tables["staging.job_education_majors"] = (
            ("observation_id", "education_order", "item_order", "major"), ("SELECT", "INSERT"))
        for view in ("staging.jobs", "staging.latest_jobs", "staging.dwh_ready_jobs", "staging.dwh_latest_jobs"):
            tables[view] = (normalize_itviec.NORMALIZED_FIELDS, ("SELECT",))
        try:
            with sink.conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                for table, (columns, privileges) in tables.items():
                    cursor.execute("SELECT " + ", ".join(columns) + " FROM " + table + " LIMIT 0")
                    for privilege in privileges:
                        cursor.execute("SELECT has_table_privilege(current_user, %s, %s)", (table, privilege))
                        row = cursor.fetchone()
                        if not row or row[0] is not True:
                            raise crawl_itviec.StorageError(
                                "Tài khoản PostgreSQL thiếu quyền " + privilege + " trên " + table + ".")
        except sink.db_errors as exc:
            # Driver exception text can contain connection details; use sanitized messages only.
            LOG.error("%s Kiểm tra cả schema staging trong itviec_schema.sql.", sink._error(exc))
            return 4
        LOG.info("PostgreSQL đã kết nối; schema raw/staging, view dành cho DWH và quyền truy cập hợp lệ.")
        return 0
    except crawl_itviec.StorageError as exc:
        LOG.error("%s", exc)
        return 4
    finally:
        if sink is not None:
            try:
                sink.close()
            except sink.db_errors:
                LOG.warning("Không đóng được kết nối PostgreSQL.")


def main(argv=None) -> int:
    parser = crawl_itviec.build_parser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check-db", action="store_true", help="Chỉ kiểm tra kết nối, bảng raw/staging và quyền; không crawl.")
    mode.add_argument("--process-run-id", help="Làm sạch/ánh xạ một crawl_run_id đã lưu trong raw vào staging; không crawl lại.")
    parser.add_argument("--no-compose-db", action="store_true", help="Không tự lấy cấu hình DB từ Docker Compose.")
    args = crawl_itviec.parse_args(argv, parser=parser)
    if args.process_run_id is not None:
        args.process_run_id = args.process_run_id.strip()
        if not args.process_run_id:
            parser.error("--process-run-id cần một crawl_run_id có nội dung.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args.db_password = None
    if (args.cookie_file is None and
            not args.browser and not args.check_db and args.process_run_id is None):
        if not DEFAULT_COOKIE_FILE.is_file():
            LOG.error("Chưa có file cookie: %s. Lưu cookie ITviec vào file này "
                      "hoặc chỉ định --cookie-file DUONG_DAN.", DEFAULT_COOKIE_FILE)
            return 1
        args.cookie_file = DEFAULT_COOKIE_FILE
        LOG.info("Dùng file cookie cục bộ: %s.", DEFAULT_COOKIE_FILE)
    try:
        try:
            database_source = _configure_compose_database(args)
        except crawl_itviec.StorageError as exc:
            LOG.error("%s", exc)
            return 4
        if database_source == "compose":
            LOG.info("Dùng cấu hình PostgreSQL trong docker/docker-compose.yml.")
        # Prompt once, reuse only in memory, and never include it in crawl metadata.
        if args.db_password_prompt:
            try:
                args.db_password = getpass.getpass("Mật khẩu PostgreSQL: ")
            except EOFError:
                LOG.error("Không nhập được mật khẩu PostgreSQL; dùng terminal tương tác hoặc cấu hình .pgpass.")
                return 4
            args.db_password_prompt = False
        database_status = check_database(args)
        if database_status or args.check_db:
            return database_status
        if args.process_run_id is not None:
            args.crawl_run_id = args.process_run_id
            LOG.info("Làm sạch và ánh xạ batch raw vào staging; crawl_run_id=%s.", args.crawl_run_id)
            return normalize_itviec.stage_database_run(args.crawl_run_id, args)
        args.crawl_run_id = crawl_itviec.new_crawl_run_id()
        LOG.info("Bước 1/2: crawl và lưu dữ liệu gốc vào raw PostgreSQL.")
        crawl_status = crawl_itviec.run(args)
        result = getattr(args, "crawl_result", None)
        if crawl_status == 4 or (isinstance(result, dict) and result.get("storage_failed")):
            return 4
        if not isinstance(result, dict) or result.get("crawl_run_id") != args.crawl_run_id:
            LOG.error("Không xác nhận được kết quả của lần crawl vừa chạy; dừng trước bước làm sạch/ánh xạ vào staging.")
            return crawl_status or 1
        if result.get("interrupted"):
            return 130
        collected_count = result.get("collected_count", 0)
        if not isinstance(collected_count, int) or collected_count <= 0:
            LOG.error("Lần crawl vừa chạy không có tin; dừng trước bước làm sạch/ánh xạ vào staging.")
            return crawl_status or 1
        LOG.info("Bước 2/2: làm sạch và ánh xạ %s tin từ raw vào staging; crawl_run_id=%s.",
                 collected_count, args.crawl_run_id)
        staging_status = normalize_itviec.stage_database_run(args.crawl_run_id, args)
        if staging_status == 4:
            return 4
        if staging_status:
            LOG.error("Bước làm sạch/ánh xạ chưa hoàn thành; xem raw.crawl_runs.metadata.staging.")
        return crawl_status if crawl_status else staging_status
    except KeyboardInterrupt:
        return 130
    finally:
        args.db_password = None


if __name__ == "__main__":
    sys.exit(main())
