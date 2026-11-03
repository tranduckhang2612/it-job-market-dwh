"""Compatibility CLI wrapper; implementation lives in it_job_market_dwh.loaders."""
from data_preprocessing.cli.load_analytics import main

if __name__ == '__main__':
    raise SystemExit(main())
