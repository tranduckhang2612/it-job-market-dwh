#!/usr/bin/env python3
"""Entrypoint tương thích; logic nằm trong ``data_preprocessing.cli``."""

from data_preprocessing.cli.crawl_itviec import main


if __name__ == "__main__":
    raise SystemExit(main())
