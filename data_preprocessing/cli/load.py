"""Compatibility import for the unified loader command."""

import sys
from it_job_market_dwh.commands import load as _module

sys.modules[__name__] = _module
