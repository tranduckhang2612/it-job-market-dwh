"""Compatibility import for the reorganized warehouse loader."""

import sys
from it_job_market_dwh.loaders import warehouse as _module

sys.modules[__name__] = _module
