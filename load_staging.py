#!/usr/bin/env python3
"""Entrypoint legacy tương thích; logic nằm trong ``data_preprocessing.cli``."""

from data_preprocessing.cli.load_staging import main


if __name__ == "__main__":
    raise SystemExit(main())
