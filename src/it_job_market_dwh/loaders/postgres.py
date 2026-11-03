"""PostgreSQL connection and raw-source helpers shared by loader commands."""

import argparse
import json
import os
import subprocess

from it_job_market_dwh.paths import PROJECT_ROOT


_CONNECTION_ENV = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE",
)


def add_scope_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--crawl-run-id", help="Chỉ xử lý một crawl_run_id.")
    parser.add_argument("--source", choices=("itviec", "topdev"), help="Chỉ xử lý một nguồn.")
    parser.add_argument("--limit", type=int, help="Giới hạn số raw row cần đọc.")


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db-dsn", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-password-prompt", action="store_true")
    parser.add_argument("--db-connect-timeout", type=int, default=10)
    parser.add_argument("--no-compose-db", action="store_true")


def configure_compose_database(args, storage_error) -> str:
    if (
        args.db_dsn
        or any(getattr(args, field) is not None
               for field in ("db_host", "db_port", "db_name", "db_user"))
        or any(name in os.environ for name in _CONNECTION_ENV)
    ):
        return "external"
    if args.no_compose_db:
        return "libpq"
    compose = PROJECT_ROOT / "docker" / "docker-compose.yml"
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose), "config", "--format", "json"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode:
            raise ValueError
        service = json.loads(result.stdout)["services"]["postgres"]
        environment = service["environment"]
        mapping = next(
            port for port in service.get("ports", [])
            if str(port.get("target")) == "5432" and port.get("protocol", "tcp") == "tcp"
        )
        host = mapping.get("host_ip") or "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        published = int(mapping["published"])
        user, database, password = (
            environment[name]
            for name in ("POSTGRES_USER", "POSTGRES_DB", "POSTGRES_PASSWORD")
        )
        if not host or not user or not database or not password:
            raise ValueError
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError, StopIteration):
        raise storage_error(
            "Không đọc được cấu hình PostgreSQL từ Docker Compose; dùng --db-* hoặc DATABASE_URL/PG*."
        ) from None
    args.db_host, args.db_port = host, published
    args.db_name, args.db_user, args.db_password = database, user, password
    return "compose"


def connect(args, application_name: str, storage_error):
    try:
        import psycopg
    except ImportError:
        raise storage_error('Thiếu psycopg. Chạy: python -m pip install "psycopg[binary]"') from None
    options = {
        "connect_timeout": args.db_connect_timeout,
        "application_name": application_name,
        "autocommit": True,
    }
    for field, parameter in (
        ("db_host", "host"), ("db_port", "port"),
        ("db_name", "dbname"), ("db_user", "user"),
    ):
        value = getattr(args, field)
        if value is not None:
            options[parameter] = value
    if args.db_password is not None:
        options["password"] = args.db_password
    try:
        return psycopg.connect(args.db_dsn or "", **options)
    except (psycopg.Error, ValueError, TypeError):
        raise storage_error("Không kết nối được PostgreSQL.") from None


def validate_raw_source(connection, storage_error) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.job_id, p.crawl_run_id, p.source_job_id, p.job_url, "
            "p.observed_at, p.raw_payload, r.source, r.status "
            "FROM raw.job_postings p JOIN raw.crawl_runs r USING (crawl_run_id) LIMIT 0"
        )
        for table in ("raw.crawl_runs", "raw.job_postings"):
            cursor.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", (table,))
            if cursor.fetchone()[0] is not True:
                raise storage_error(f"Tài khoản PostgreSQL thiếu quyền SELECT trên {table}.")


def raw_selection_query(*, crawl_run_id=None, source=None, limit=None):
    conditions = ["r.status <> 'running'"]
    parameters = []
    if crawl_run_id:
        conditions.append("p.crawl_run_id = %s")
        parameters.append(crawl_run_id)
    if source:
        conditions.append("r.source = %s")
        parameters.append(source)
    query = (
        "SELECT p.job_id, p.crawl_run_id, r.source, p.source_job_id, p.job_url, "
        "p.observed_at, p.raw_payload FROM raw.job_postings p "
        "JOIN raw.crawl_runs r USING (crawl_run_id) WHERE " + " AND ".join(conditions)
        + " ORDER BY p.ingested_at, p.job_id"
    )
    if limit is not None:
        query += " LIMIT %s"
        parameters.append(limit)
    return query, parameters
