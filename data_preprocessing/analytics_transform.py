"""Step 3: canonicalize a staging record without modifying its input."""
from data_preprocessing.canonicalization import standardize_record

TRANSFORM_VERSION = "2026.11.01.1"

def transform_record(job):
    result = standardize_record(job)
    for field in ("skills", "education_requirements", "foreign_languages"):
        for item in result[field]:
            item.pop("requirement_type", None)
    return result
