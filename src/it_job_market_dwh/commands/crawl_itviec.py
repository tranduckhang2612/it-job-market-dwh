#!/usr/bin/env python3
"""CLI crawl ITviec và ghi JSON gốc vào PostgreSQL schema raw.

Chạy từ thư mục gốc project:
    python main_itviec.py
    python main_itviec.py --limit 50
    python main_itviec.py --check-db

Mặc định crawler lấy cookie từ
data_preprocessing/itviec_crawl_data/cookie_itviec.txt và ghi vào
raw.crawl_runs, raw.job_postings. Database chưa có hai bảng raw sẽ được
khởi tạo bằng data_preprocessing/sql/raw_schema.sql.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from it_job_market_dwh.ingestion import itviec as crawl_itviec
from it_job_market_dwh.ingestion.raw_storage import ensure_raw_schema, validate_raw_schema
from it_job_market_dwh.paths import PROJECT_ROOT, ITVIEC_PRIVATE_DATA


LOG = logging.getLogger("itviec.pipeline")
DEFAULT_COOKIE_FILE = ITVIEC_PRIVATE_DATA / "cookie_itviec.txt"
_CONNECTION_ENV = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE",
)


def _configure_compose_database(args) -> str:
    """Dùng cấu hình Compose khi CLI, DATABASE_URL và PG* đều chưa có."""
    if (
        getattr(args, "db_dsn", "")
        or any(getattr(args, field, None) is not None
               for field in ("db_host", "db_port", "db_name", "db_user"))
        or any(name in os.environ for name in _CONNECTION_ENV)
    ):
        return "external"
    compose = PROJECT_ROOT / "docker" / "docker-compose.yml"
    if getattr(args, "no_compose_db", False) or not compose.is_file():
        return "libpq"
    error = crawl_itviec.StorageError(
        "Không đọc được cấu hình PostgreSQL từ Docker Compose. Kiểm tra service "
        "postgres hoặc dùng --db-* / DATABASE_URL / PG*."
    )
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "config", "--format", "json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode:
            raise error
        service = json.loads(result.stdout)["services"]["postgres"]
        environment = service["environment"]
        user, database, password = (
            environment[name]
            for name in ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")
        )
        if not all(isinstance(value, str) and value for value in (user, database, password)):
            raise error
        mapping = next(
            port for port in service.get("ports", [])
            if str(port.get("target")) == "5432" and port.get("protocol", "tcp") == "tcp"
        )
        published = str(mapping["published"])
        if not published.isdecimal() or not 1 <= int(published) <= 65535:
            raise error
        host = mapping.get("host_ip") or "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        if not isinstance(host, str):
            raise error
    except (
        OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError,
        StopIteration, AttributeError,
    ):
        raise error from None
    args.db_host, args.db_port = host, int(published)
    args.db_name, args.db_user, args.db_password = database, user, password
    return "compose"


def check_database(args) -> int:
    """Khởi tạo nếu cần rồi kiểm tra hai bảng raw và quyền truy cập."""
    sink = None
    try:
        sink = crawl_itviec.PostgresSink(args)
        created = ensure_raw_schema(sink.conn)
        validate_raw_schema(sink.conn, crawl_itviec.StorageError)
        if created:
            LOG.info("Đã khởi tạo schema raw từ raw_schema.sql.")
        LOG.info("PostgreSQL đã kết nối; hai bảng raw và quyền truy cập hợp lệ.")
        return 0
    except crawl_itviec.StorageError as exc:
        LOG.error("%s", exc)
        return 4
    except Exception:
        LOG.error("Không khởi tạo hoặc kiểm tra được schema raw.")
        return 4
    finally:
        if sink is not None:
            try:
                sink.close()
            except sink.db_errors:
                LOG.warning("Không đóng được kết nối PostgreSQL.")


def main(argv=None) -> int:
    parser = crawl_itviec.build_parser(description=__doc__)
    parser.add_argument(
        "--check-db", action="store_true",
        help="Chỉ kiểm tra kết nối, hai bảng raw và quyền; không crawl.",
    )
    parser.add_argument(
        "--no-compose-db", action="store_true",
        help="Không tự lấy cấu hình DB từ Docker Compose.",
    )
    args = crawl_itviec.parse_args(argv, parser=parser)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if not hasattr(args, "db_password"):
        args.db_password = None

    if args.cookie_file is None and not args.browser and not args.check_db:
        if not DEFAULT_COOKIE_FILE.is_file():
            LOG.error(
                "Chưa có file cookie: %s. Lưu cookie ITviec vào file này "
                "hoặc chỉ định --cookie-file DUONG_DAN.", DEFAULT_COOKIE_FILE,
            )
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
        if args.db_password_prompt:
            try:
                args.db_password = getpass.getpass("Mật khẩu PostgreSQL: ")
            except EOFError:
                LOG.error("Không nhập được mật khẩu PostgreSQL.")
                return 4
            args.db_password_prompt = False
        database_status = check_database(args)
        if database_status or args.check_db:
            return database_status
        args.crawl_run_id = crawl_itviec.new_crawl_run_id()
        LOG.info("Crawl ITviec và lưu JSON gốc vào schema raw.")
        return crawl_itviec.run(args)
    except KeyboardInterrupt:
        return 130
    finally:
        args.db_password = None


if __name__ == "__main__":
    sys.exit(main())
