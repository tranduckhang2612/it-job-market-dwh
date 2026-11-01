"""Compatibility entry point for the persistent step-3 analytics loader."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.analytics_loader import main
if __name__ == '__main__':
    raise SystemExit(main())
