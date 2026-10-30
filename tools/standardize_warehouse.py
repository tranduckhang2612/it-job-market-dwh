"""Compatibility entrypoint: contract 1.2 analytics configuration, without raw replay.

Historical 2026-10-29 snapshots require the archived matching script.
New apply/rollback snapshots are produced by configure_analytics.py.
"""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.configure_analytics import main
if __name__=='__main__':raise SystemExit(main())
