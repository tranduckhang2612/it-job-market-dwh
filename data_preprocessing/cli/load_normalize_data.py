"""Compatibility import for the reorganized loader."""

import sys
from it_job_market_dwh.loaders import normalize as _module

sys.modules[__name__] = _module
