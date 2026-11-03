#!/usr/bin/env python3
"""Entrypoint legacy tương thích; logic nằm trong ``it_job_market_dwh.loaders``."""

from data_preprocessing.cli.load_staging import main


if __name__ == "__main__":
    raise SystemExit(main())
