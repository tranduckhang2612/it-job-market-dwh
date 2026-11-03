"""Định nghĩa và kiểm tra hợp đồng output của lớp chuẩn hóa.

Module này không chứa rule suy luận. Khi thêm/bớt field persisted, cập nhật contract,
DDL, migration và version trong cùng một thay đổi.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal


NORMALIZATION_VERSION = "3.0"
NORMALIZE_DATA_VERSION = "5.1"

NORMALIZED_FIELDS = (
    "job_id", "source", "source_job_id", "job_url", "crawl_run_id",
    "observed_at", "published_at", "title", "company_name", "description",
    "job_roles", "seniority_levels", "location_text", "cities", "work_mode",
    "skills", "required_knowledge", "experience_text", "experience_min_years",
    "experience_max_years", "education_requirements", "foreign_languages",
    "soft_skills", "salary_text", "salary_min", "salary_max", "salary_currency",
    "salary_period", "salary_basis", "salary_status",
)

NORMALIZATION_META_FIELD = "normalization_meta"
NORMALIZED_WITH_META_FIELDS = NORMALIZED_FIELDS + (NORMALIZATION_META_FIELD,)
NORMALIZE_DATA_EXCLUDED_FIELDS = frozenset({
    "experience_text", "experience_min_years", "experience_max_years",
    "education_requirements", "published_at", "required_knowledge",
    "salary_text", "salary_min", "salary_max", "salary_currency",
    "salary_period", "salary_basis", "salary_status",
})
PERSISTED_NORMALIZED_FIELDS = tuple(
    field for field in NORMALIZED_FIELDS if field not in NORMALIZE_DATA_EXCLUDED_FIELDS
) + (NORMALIZATION_META_FIELD,)

ARRAY_FIELDS = (
    "job_roles", "seniority_levels", "cities", "skills", "required_knowledge",
    "education_requirements", "foreign_languages", "soft_skills",
)


class NormalizationError(ValueError):
    """Một raw record không thể chuyển an toàn sang hợp đồng chuẩn hóa."""


def validate_normalized_job(job: dict) -> None:
    if not isinstance(job, dict) or tuple(job) != NORMALIZED_FIELDS:
        raise NormalizationError("Bản ghi không đúng hợp đồng 30 cột.")
    for field in (
        "job_id", "source", "job_url", "crawl_run_id", "title", "description",
        "salary_status",
    ):
        if not isinstance(job[field], str) or not job[field].strip():
            raise NormalizationError(f"Trường bắt buộc {field} không hợp lệ.")
    if job["source"] not in {"itviec", "topdev"}:
        raise NormalizationError("source không hợp lệ.")
    if not isinstance(job["observed_at"], datetime) or job["observed_at"].utcoffset() is None:
        raise NormalizationError("observed_at phải có timezone.")
    if job["published_at"] is not None and (
        not isinstance(job["published_at"], datetime) or job["published_at"].utcoffset() is None
    ):
        raise NormalizationError("published_at phải có timezone.")
    for field in ARRAY_FIELDS:
        if not isinstance(job[field], list):
            raise NormalizationError(f"{field} phải là list.")
    if not job["job_roles"]:
        raise NormalizationError("job_roles rỗng sau role gate.")
    if job["work_mode"] not in {None, "onsite", "hybrid", "remote"}:
        raise NormalizationError("work_mode không hợp lệ.")
    if job["salary_status"] not in {"disclosed", "negotiable", "not_disclosed", "unknown"}:
        raise NormalizationError("salary_status không hợp lệ.")
    for field in ("experience_min_years", "experience_max_years", "salary_min", "salary_max"):
        value = job[field]
        if value is not None and (
            not isinstance(value, Decimal) or not value.is_finite() or value < 0
        ):
            raise NormalizationError(f"{field} không hợp lệ.")
    if (
        job["experience_min_years"] is not None
        and job["experience_max_years"] is not None
        and job["experience_min_years"] > job["experience_max_years"]
    ):
        raise NormalizationError("Khoảng experience bị đảo.")
    if (
        job["salary_min"] is not None
        and job["salary_max"] is not None
        and job["salary_min"] > job["salary_max"]
    ):
        raise NormalizationError("Khoảng salary bị đảo.")
    if job["salary_status"] == "disclosed" and job["salary_min"] is None and job["salary_max"] is None:
        raise NormalizationError("Lương disclosed phải có ít nhất một cận.")
    if job["salary_status"] != "disclosed" and (
        job["salary_min"] is not None or job["salary_max"] is not None
    ):
        raise NormalizationError("Lương không disclosed không được có cận số.")


def validate_normalized_job_with_meta(job: dict) -> None:
    if not isinstance(job, dict) or tuple(job) != NORMALIZED_WITH_META_FIELDS:
        raise NormalizationError("Bản ghi đầy đủ không đúng hợp đồng 30 cột + metadata.")
    validate_normalized_job({field: job[field] for field in NORMALIZED_FIELDS})
    metadata = job[NORMALIZATION_META_FIELD]
    if not isinstance(metadata, dict) or not isinstance(metadata.get("fields"), dict):
        raise NormalizationError("normalization_meta phải là object có fields.")
    expected_meta_fields = {
        "title", "company_name", "description", "job_roles", "seniority_levels",
        "cities", "work_mode", "skills", "experience_text", "foreign_languages",
    }
    if set(metadata["fields"]) != expected_meta_fields:
        raise NormalizationError("normalization_meta.fields không đúng hợp đồng.")


def validate_normalized_storage_job(job: dict) -> None:
    if not isinstance(job, dict) or tuple(job) != PERSISTED_NORMALIZED_FIELDS:
        raise NormalizationError(
            f"Bản ghi normalize_data không đúng hợp đồng {len(PERSISTED_NORMALIZED_FIELDS)} field."
        )
    metadata = job[NORMALIZATION_META_FIELD]
    if not isinstance(metadata, dict) or not isinstance(metadata.get("fields"), dict):
        raise NormalizationError("normalization_meta phải là object có fields.")
    expected_meta_fields = {
        "title", "company_name", "description", "job_roles", "seniority_levels",
        "cities", "work_mode", "skills", "foreign_languages",
    }
    if set(metadata["fields"]) != expected_meta_fields:
        raise NormalizationError("normalization_meta.fields không đúng hợp đồng lưu trữ.")
