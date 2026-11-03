"""Repository paths used by the source package and compatibility commands."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LEGACY_DATA_ROOT = PROJECT_ROOT / "data_preprocessing"
SQL_ROOT = LEGACY_DATA_ROOT / "sql"
ROLE_CATALOG = PROJECT_ROOT / "ds.md"
ITVIEC_PRIVATE_DATA = LEGACY_DATA_ROOT / "itviec_crawl_data"
