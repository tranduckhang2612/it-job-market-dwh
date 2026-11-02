#!/usr/bin/env python3
"""CLI đọc raw, áp dụng hai lớp xử lý và upsert vào normalize_data."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterable, Mapping

from data_preprocessing.normalization.contracts import (
    ARRAY_FIELDS,
    NORMALIZATION_META_FIELD,
    NORMALIZE_DATA_VERSION,
    PERSISTED_NORMALIZED_FIELDS,
    NormalizationError,
)
from data_preprocessing.normalization.normalizer import normalize_raw_record_for_storage
from data_preprocessing.normalization.role_gate import evaluate_role_gate, load_role_catalog


LOG = logging.getLogger("normalize_data.loader")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
NORMALIZE_SCHEMA_FILE = PROJECT_ROOT / "data_preprocessing" / "sql" / "normalize_data_schema.sql"
_CONNECTION_ENV = (
    "PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD",
    "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE",
)
_PERSISTED_ARRAY_FIELDS = tuple(
    field for field in ARRAY_FIELDS if field in PERSISTED_NORMALIZED_FIELDS
)
_JSONB_FIELDS = set(_PERSISTED_ARRAY_FIELDS) | {NORMALIZATION_META_FIELD}
_INSERT_COLUMNS = PERSISTED_NORMALIZED_FIELDS + ("normalization_version",)
_UPDATE_COLUMNS = tuple(
    column for column in _INSERT_COLUMNS if column not in {"job_id", "crawl_run_id"}
)
_COLUMN_TYPES = {
    **{column: "text" for column in (
        "job_id", "source", "source_job_id", "job_url", "crawl_run_id", "title",
        "company_name", "description", "location_text", "work_mode",
        "normalization_version",
    )},
    **{column: "jsonb" for column in (*_PERSISTED_ARRAY_FIELDS, NORMALIZATION_META_FIELD)},
    **{column: "timestamp with time zone" for column in (
        "observed_at", "normalized_at",
    )},
}
_NOT_NULL_COLUMNS = {
    "job_id", "source", "job_url", "crawl_run_id", "observed_at", "title",
    "description", *_PERSISTED_ARRAY_FIELDS, NORMALIZATION_META_FIELD,
    "normalization_version", "normalized_at",
}
UPSERT_SQL = (
    "INSERT INTO normalize_data.normalized_jobs AS existing (" + ", ".join(_INSERT_COLUMNS) + ") "
    "VALUES (" + ", ".join(["%s"] * len(_INSERT_COLUMNS)) + ") "
    "ON CONFLICT (job_id, crawl_run_id) DO UPDATE SET "
    + ", ".join(column + " = EXCLUDED." + column for column in _UPDATE_COLUMNS)
    + ", normalized_at = CURRENT_TIMESTAMP"
    + " WHERE ROW(" + ", ".join("existing." + column for column in _UPDATE_COLUMNS) + ")"
    + " IS DISTINCT FROM ROW("
    + ", ".join("EXCLUDED." + column for column in _UPDATE_COLUMNS) + ")"
)


class NormalizeStorageError(RuntimeError):
    """Không thể kiểm tra hoặc ghi normalize_data."""


@dataclass
class NormalizeSummary:
    scanned: int = 0
    normalized: int = 0
    rejected_role: int = 0
    rejected_language: int = 0
    rejected_normalization: int = 0
    removed_stale: int = 0
    failed_write: int = 0


def _configure_compose_database(args) -> str:
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
        raise NormalizeStorageError(
            "Không đọc được cấu hình PostgreSQL từ Docker Compose; dùng --db-* hoặc DATABASE_URL/PG*."
        ) from None
    args.db_host, args.db_port = host, published
    args.db_name, args.db_user, args.db_password = database, user, password
    return "compose"


def _connect(args):
    try:
        import psycopg
    except ImportError:
        raise NormalizeStorageError(
            'Thiếu psycopg. Chạy: python -m pip install "psycopg[binary]"'
        ) from None
    options = {
        "connect_timeout": args.db_connect_timeout,
        "application_name": "normalize_data_loader",
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
        raise NormalizeStorageError("Không kết nối được PostgreSQL.") from None


def validate_raw_source(connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.job_id, p.crawl_run_id, p.source_job_id, p.job_url, "
            "p.observed_at, p.raw_payload, r.source, r.status "
            "FROM raw.job_postings p JOIN raw.crawl_runs r USING (crawl_run_id) LIMIT 0"
        )
        for table in ("raw.crawl_runs", "raw.job_postings"):
            cursor.execute("SELECT has_table_privilege(current_user, %s, 'SELECT')", (table,))
            if cursor.fetchone()[0] is not True:
                raise NormalizeStorageError(
                    f"Tài khoản PostgreSQL thiếu quyền SELECT trên {table}."
                )


def ensure_normalize_data_schema(connection) -> bool:
    """Tạo bảng nếu thiếu rồi kiểm tra chính xác hợp đồng cột và quyền."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('normalize_data.normalized_jobs')")
        row = cursor.fetchone()
        created = not row or row[0] is None
        if created:
            cursor.execute(NORMALIZE_SCHEMA_FILE.read_text(encoding="utf-8"), prepare=False)
        cursor.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema='normalize_data' AND table_name='normalized_jobs' "
            "ORDER BY ordinal_position"
        )
        actual = tuple(cursor.fetchall())
        for schema_name in ("raw", "normalize_data"):
            cursor.execute(
                "SELECT has_schema_privilege(current_user, %s, 'USAGE')", (schema_name,),
            )
            if cursor.fetchone()[0] is not True:
                raise NormalizeStorageError(
                    f"Tài khoản PostgreSQL thiếu quyền USAGE trên schema {schema_name}."
                )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            cursor.execute(
                "SELECT has_table_privilege(current_user, "
                "'normalize_data.normalized_jobs', %s)",
                (privilege,),
            )
            if cursor.fetchone()[0] is not True:
                raise NormalizeStorageError(
                    "Tài khoản PostgreSQL thiếu quyền "
                    f"{privilege} trên normalize_data.normalized_jobs."
                )
        cursor.execute(
            "SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu "
            "ON tc.constraint_schema=kcu.constraint_schema "
            "AND tc.constraint_name=kcu.constraint_name "
            "WHERE tc.table_schema='normalize_data' AND tc.table_name='normalized_jobs' "
            "AND tc.constraint_type='PRIMARY KEY' ORDER BY kcu.ordinal_position"
        )
        primary_key = tuple(row[0] for row in cursor.fetchall())
    expected_names = PERSISTED_NORMALIZED_FIELDS + ("normalization_version", "normalized_at")
    expected = tuple(
        (column, _COLUMN_TYPES[column], "NO" if column in _NOT_NULL_COLUMNS else "YES")
        for column in expected_names
    )
    if actual != expected:
        raise NormalizeStorageError(
            "normalize_data.normalized_jobs không đúng hợp đồng chuẩn hóa phiên bản 5.1."
        )
    if primary_key != ("job_id", "crawl_run_id"):
        raise NormalizeStorageError(
            "normalize_data.normalized_jobs không có đúng khóa chính (job_id, crawl_run_id)."
        )
    return created


def fetch_raw_records(
    connection,
    *,
    crawl_run_id=None,
    source=None,
    limit=None,
    batch_size: int = 500,
):
    """Yield raw rows theo batch, không dựng thêm một list JSON lớn trong Python."""
    if batch_size < 1:
        raise ValueError("batch_size phải lớn hơn 0.")
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
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        columns = tuple(column.name for column in cursor.description)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                yield dict(zip(columns, row))


def _write_job(connection, job: dict) -> None:
    from psycopg.types.json import Jsonb

    values = [Jsonb(job[field]) if field in _JSONB_FIELDS else job[field]
              for field in PERSISTED_NORMALIZED_FIELDS]
    values.append(NORMALIZE_DATA_VERSION)
    connection.execute(UPSERT_SQL, values)


def _remove_normalized_job(connection, raw_record: Mapping) -> int:
    job_id = raw_record.get("job_id")
    crawl_run_id = raw_record.get("crawl_run_id")
    if not job_id or not crawl_run_id:
        return 0
    result = connection.execute(
        "DELETE FROM normalize_data.normalized_jobs WHERE job_id=%s AND crawl_run_id=%s",
        (job_id, crawl_run_id),
    )
    return max(0, getattr(result, "rowcount", 0) or 0)


def normalize_records(
    connection,
    raw_records: Iterable[Mapping],
    *,
    dry_run: bool = False,
) -> NormalizeSummary:
    """Xử lý độc lập từng tin; một tin lỗi không làm dừng cả batch."""
    summary = NormalizeSummary()
    catalog = load_role_catalog()
    for raw_record in raw_records:
        summary.scanned += 1
        payload = raw_record.get("raw_payload") if isinstance(raw_record, Mapping) else None
        source = raw_record.get("source") if isinstance(raw_record, Mapping) else None
        decision = evaluate_role_gate(payload, source=source, catalog=catalog)
        job_id = raw_record.get("job_id", "unknown") if isinstance(raw_record, Mapping) else "unknown"
        if not decision.accepted:
            if decision.reason in {"unsupported_language", "unknown_language"}:
                summary.rejected_language += 1
            else:
                summary.rejected_role += 1
            LOG.info("Bỏ qua %s: %s.", job_id, decision.reason)
            if not dry_run and isinstance(raw_record, Mapping):
                try:
                    summary.removed_stale += _remove_normalized_job(connection, raw_record)
                except Exception as exc:
                    summary.failed_write += 1
                    LOG.error("Không xóa được bản cũ của %s: %s", job_id, type(exc).__name__)
            continue
        try:
            job = normalize_raw_record_for_storage(dict(raw_record), decision)
        except (NormalizationError, TypeError, ValueError) as exc:
            summary.rejected_normalization += 1
            LOG.warning("Bỏ qua %s ở lớp chuẩn hóa: %s", job_id, exc)
            if not dry_run and isinstance(raw_record, Mapping):
                try:
                    summary.removed_stale += _remove_normalized_job(connection, raw_record)
                except Exception as write_exc:
                    summary.failed_write += 1
                    LOG.error("Không xóa được bản cũ của %s: %s", job_id, type(write_exc).__name__)
            continue
        if dry_run:
            summary.normalized += 1
            continue
        try:
            _write_job(connection, job)
            summary.normalized += 1
        except Exception as exc:
            summary.failed_write += 1
            LOG.error("Không ghi được %s vào normalize_data: %s", job_id, type(exc).__name__)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crawl-run-id", help="Chỉ xử lý một crawl_run_id.")
    parser.add_argument("--source", choices=("itviec", "topdev"), help="Chỉ xử lý một nguồn.")
    parser.add_argument("--limit", type=int, help="Giới hạn số raw row cần đọc.")
    parser.add_argument("--dry-run", action="store_true", help="Chạy rule nhưng không ghi dữ liệu.")
    parser.add_argument("--check-db", action="store_true", help="Chỉ tạo/kiểm tra schema rồi thoát.")
    parser.add_argument("--db-dsn", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--db-host")
    parser.add_argument("--db-port", type=int)
    parser.add_argument("--db-name")
    parser.add_argument("--db-user")
    parser.add_argument("--db-password-prompt", action="store_true")
    parser.add_argument("--db-connect-timeout", type=int, default=10)
    parser.add_argument("--no-compose-db", action="store_true")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit phải lớn hơn 0.")
    if args.db_port is not None and not 1 <= args.db_port <= 65535:
        parser.error("--db-port phải trong khoảng 1..65535.")
    if args.db_connect_timeout < 1:
        parser.error("--db-connect-timeout phải lớn hơn 0.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args.db_password = None
    connection = None
    try:
        source = _configure_compose_database(args)
        if source == "compose":
            LOG.info("Dùng cấu hình PostgreSQL trong docker/docker-compose.yml.")
        if args.db_password_prompt:
            try:
                args.db_password = getpass.getpass("Mật khẩu PostgreSQL: ")
            except EOFError:
                raise NormalizeStorageError("Không nhập được mật khẩu PostgreSQL.") from None
        connection = _connect(args)
        validate_raw_source(connection)
        created = ensure_normalize_data_schema(connection)
        if created:
            LOG.info("Đã tạo normalize_data.normalized_jobs.")
        if args.check_db:
            LOG.info("Schema raw và normalize_data hợp lệ.")
            return 0
        records = fetch_raw_records(
            connection, crawl_run_id=args.crawl_run_id, source=args.source, limit=args.limit,
        )
        summary = normalize_records(connection, records, dry_run=args.dry_run)
        LOG.info(
            "Hoàn tất: scanned=%s, normalized=%s, rejected_role=%s, "
            "rejected_language=%s, rejected_normalization=%s, removed_stale=%s, "
            "failed_write=%s%s.",
            summary.scanned, summary.normalized, summary.rejected_role,
            summary.rejected_language, summary.rejected_normalization,
            summary.removed_stale, summary.failed_write,
            " (dry-run)" if args.dry_run else "",
        )
        return 2 if summary.rejected_normalization or summary.failed_write else 0
    except NormalizeStorageError as exc:
        LOG.error("%s", exc)
        return 4
    except Exception as exc:
        try:
            import psycopg
        except ImportError:
            raise
        if isinstance(exc, psycopg.Error):
            LOG.error("Lỗi PostgreSQL khi kiểm tra hoặc ghi normalize_data.")
            return 4
        raise
    finally:
        args.db_password = None
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
