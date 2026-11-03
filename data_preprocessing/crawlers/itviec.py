"""Compatibility import for the reorganized source package."""

import sys
from it_job_market_dwh.ingestion import itviec as _module

sys.modules[__name__] = _module
