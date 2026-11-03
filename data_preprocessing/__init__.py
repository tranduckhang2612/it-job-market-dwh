"""Compatibility imports for the package now located under ``src``."""

from pathlib import Path
import sys

_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))
