"""Role/language gate và quy tắc chuẩn hóa dữ liệu."""

from .contracts import (
    NORMALIZATION_VERSION,
    NORMALIZE_DATA_VERSION,
    NormalizationError,
)
from .normalizer import (
    normalize_raw_record,
    normalize_raw_record_for_storage,
    normalize_raw_record_with_meta,
)
from .role_gate import RoleGateDecision, evaluate_role_gate, load_role_catalog

__all__ = (
    "NORMALIZATION_VERSION",
    "NORMALIZE_DATA_VERSION",
    "NormalizationError",
    "RoleGateDecision",
    "evaluate_role_gate",
    "load_role_catalog",
    "normalize_raw_record",
    "normalize_raw_record_for_storage",
    "normalize_raw_record_with_meta",
)
