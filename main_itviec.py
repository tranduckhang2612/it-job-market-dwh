#!/usr/bin/env python3
"""Entrypoint tương thích; logic nằm trong ``it_job_market_dwh.commands``."""

from data_preprocessing.cli.crawl_itviec import main


if __name__ == "__main__":
    raise SystemExit(main())
