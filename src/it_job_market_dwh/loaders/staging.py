#!/usr/bin/env python3
"""CLI legacy: đọc raw, chuẩn hóa 30 cột và upsert vào staging."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import getpass
import logging
import sys
from typing import Iterable, Mapping

from it_job_market_dwh.normalization.contracts import (
    ARRAY_FIELDS,
    NORMALIZATION_VERSION,
    NORMALIZED_FIELDS,
    NormalizationError,
)
from it_job_market_dwh.normalization.normalizer import normalize_raw_record
from it_job_market_dwh.normalization.role_gate import evaluate_role_gate, load_role_catalog
from it_job_market_dwh.paths import SQL_ROOT
from it_job_market_dwh.loaders import postgres


LOG = logging.getLogger("staging.loader")
STAGING_SCHEMA_FILE = SQL_ROOT / "staging_schema.sql"
_INSERT_COLUMNS = NORMALIZED_FIELDS + ("normalization_version",)
_UPDATE_COLUMNS = tuple(
    column for column in _INSERT_COLUMNS if column not in {"job_id", "crawl_run_id"}
)
UPSERT_SQL = (
    "INSERT INTO staging.normalized_jobs (" + ", ".join(_INSERT_COLUMNS) + ") "
    "VALUES (" + ", ".join(["%s"] * len(_INSERT_COLUMNS)) + ") "
    "ON CONFLICT (job_id, crawl_run_id) DO UPDATE SET "
    + ", ".join(column + " = EXCLUDED." + column for column in _UPDATE_COLUMNS)
    + ", normalized_at = CURRENT_TIMESTAMP"
)


class StagingStorageError(RuntimeError):
    """Không thể kiểm tra hoặc ghi staging."""


@dataclass
class LoadSummary:
    scanned: int = 0
    staged: int = 0
    rejected_role: int = 0
    rejected_language: int = 0
    rejected_normalization: int = 0
    removed_stale: int = 0
    failed_write: int = 0


def _configure_compose_database(args) -> str:
    return postgres.configure_compose_database(args, StagingStorageError)


def _connect(args):
    return postgres.connect(args, "staging_loader", StagingStorageError)


def ensure_staging_schema(connection) -> bool:
    """Tạo staging.normalized_jobs nếu thiếu và kiểm tra đúng hợp đồng cột."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('staging.normalized_jobs')")
        row = cursor.fetchone()
        created = not row or row[0] is None
        if created:
            cursor.execute(STAGING_SCHEMA_FILE.read_text(encoding="utf-8"), prepare=False)
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='staging' AND table_name='normalized_jobs' "
            "ORDER BY ordinal_position"
        )
        actual = tuple(value[0] for value in cursor.fetchall())
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            cursor.execute(
                "SELECT has_table_privilege(current_user, 'staging.normalized_jobs', %s)",
                (privilege,),
            )
            if cursor.fetchone()[0] is not True:
                raise StagingStorageError(
                    f"Tài khoản PostgreSQL thiếu quyền {privilege} trên staging.normalized_jobs."
                )
    expected = NORMALIZED_FIELDS + ("normalization_version", "normalized_at")
    if actual != expected:
        raise StagingStorageError("staging.normalized_jobs không đúng hợp đồng 30 cột.")
    return created


def validate_raw_source(connection) -> None:
    postgres.validate_raw_source(connection, StagingStorageError)


def fetch_raw_records(connection, *, crawl_run_id=None, source=None, limit=None) -> list[dict]:
    query, parameters = postgres.raw_selection_query(
        crawl_run_id=crawl_run_id, source=source, limit=limit,
    )
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        columns = tuple(column.name for column in cursor.description)
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _write_job(connection, job: dict) -> None:
    from psycopg.types.json import Jsonb

    values = [Jsonb(job[field]) if field in ARRAY_FIELDS else job[field]
              for field in NORMALIZED_FIELDS]
    values.append(NORMALIZATION_VERSION)
    connection.execute(UPSERT_SQL, values)


def _remove_staged_job(connection, raw_record: Mapping) -> int:
    job_id = raw_record.get("job_id")
    crawl_run_id = raw_record.get("crawl_run_id")
    if not job_id or not crawl_run_id:
        return 0
    result = connection.execute(
        "DELETE FROM staging.normalized_jobs WHERE job_id=%s AND crawl_run_id=%s",
        (job_id, crawl_run_id),
    )
    return max(0, getattr(result, "rowcount", 0) or 0)


def stage_records(
    connection,
    raw_records: Iterable[Mapping],
    *,
    dry_run: bool = False,
) -> LoadSummary:
    """Xử lý độc lập từng tin; lỗi một tin không dừng những tin còn lại."""
    summary = LoadSummary()
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
                    summary.removed_stale += _remove_staged_job(connection, raw_record)
                except Exception as exc:
                    summary.failed_write += 1
                    LOG.error("Không xóa được staging cũ của %s: %s", job_id, type(exc).__name__)
            continue
        try:
            job = normalize_raw_record(dict(raw_record), decision)
        except (NormalizationError, TypeError, ValueError) as exc:
            summary.rejected_normalization += 1
            LOG.warning("Bỏ qua %s ở lớp chuẩn hóa: %s", job_id, exc)
            if not dry_run and isinstance(raw_record, Mapping):
                try:
                    summary.removed_stale += _remove_staged_job(connection, raw_record)
                except Exception as write_exc:
                    summary.failed_write += 1
                    LOG.error("Không xóa được staging cũ của %s: %s", job_id, type(write_exc).__name__)
            continue
        if dry_run:
            summary.staged += 1
            continue
        try:
            _write_job(connection, job)
            summary.staged += 1
        except Exception as exc:
            summary.failed_write += 1
            LOG.error("Không ghi được %s vào staging: %s", job_id, type(exc).__name__)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    postgres.add_scope_arguments(parser)
    parser.add_argument("--dry-run", action="store_true", help="Chạy hai lớp nhưng không ghi staging.")
    parser.add_argument("--check-db", action="store_true", help="Chỉ tạo/kiểm tra schema rồi thoát.")
    postgres.add_connection_arguments(parser)
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
                raise StagingStorageError("Không nhập được mật khẩu PostgreSQL.") from None
        connection = _connect(args)
        validate_raw_source(connection)
        created = ensure_staging_schema(connection)
        if created:
            LOG.info("Đã tạo staging.normalized_jobs.")
        if args.check_db:
            LOG.info("Schema raw và staging hợp lệ.")
            return 0
        records = fetch_raw_records(
            connection, crawl_run_id=args.crawl_run_id, source=args.source, limit=args.limit,
        )
        summary = stage_records(connection, records, dry_run=args.dry_run)
        LOG.info(
            "Hoàn tất: scanned=%s, staged=%s, rejected_role=%s, rejected_language=%s, "
            "rejected_normalization=%s, removed_stale=%s, failed_write=%s%s.",
            summary.scanned, summary.staged, summary.rejected_role, summary.rejected_language,
            summary.rejected_normalization, summary.removed_stale, summary.failed_write,
            " (dry-run)" if args.dry_run else "",
        )
        return 2 if summary.rejected_normalization or summary.failed_write else 0
    except StagingStorageError as exc:
        LOG.error("%s", exc)
        return 4
    finally:
        args.db_password = None
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    sys.exit(main())
