"""Khởi tạo và kiểm tra schema ``raw`` mà hai crawler dùng chung."""

from __future__ import annotations

from pathlib import Path


RAW_SCHEMA_FILE = Path(__file__).resolve().parents[1] / "sql" / "raw_schema.sql"

RAW_TABLES = {
    "raw.crawl_runs": (
        (
            "crawl_run_id", "source", "started_at", "finished_at",
            "requested_count", "collected_count", "status", "metadata",
            "errors", "ingested_at",
        ),
        ("SELECT", "INSERT", "UPDATE"),
    ),
    "raw.job_postings": (
        (
            "job_id", "crawl_run_id", "source_job_id", "job_url",
            "observed_at", "raw_payload", "ingested_at",
        ),
        ("SELECT", "INSERT"),
    ),
}


def ensure_raw_schema(connection) -> bool:
    """Tạo schema raw khi database chưa có đủ hai bảng bắt buộc."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT to_regclass('raw.crawl_runs'), to_regclass('raw.job_postings')"
        )
        tables = cursor.fetchone()
        if tables and all(tables):
            return False
        cursor.execute(RAW_SCHEMA_FILE.read_text(encoding="utf-8"), prepare=False)
    return True


def validate_raw_schema(connection, storage_error) -> None:
    """Kiểm tra cột và quyền tối thiểu mà crawler thực sự sử dụng."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        for table, (columns, privileges) in RAW_TABLES.items():
            cursor.execute(
                "SELECT " + ", ".join(columns) + " FROM " + table + " LIMIT 0"
            )
            for privilege in privileges:
                cursor.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)",
                    (table, privilege),
                )
                row = cursor.fetchone()
                if not row or row[0] is not True:
                    raise storage_error(
                        f"Tài khoản PostgreSQL thiếu quyền {privilege} trên {table}."
                    )
