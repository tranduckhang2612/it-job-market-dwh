"""Run one pipeline loader: normalize, analytics, warehouse, or legacy staging."""

import argparse
from importlib import import_module
import sys

STAGES = {
    "normalize": "it_job_market_dwh.loaders.normalize",
    "analytics": "it_job_market_dwh.loaders.analytics",
    "warehouse": "it_job_market_dwh.loaders.warehouse",
    "staging": "it_job_market_dwh.loaders.staging",
}


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", nargs="?", choices=tuple(STAGES))
    if args and args[0] in STAGES:
        return import_module(STAGES[args[0]]).main(args[1:])
    parsed = parser.parse_args(args)
    if parsed.stage is None:
        parser.print_help()
        return 0
    return import_module(STAGES[parsed.stage]).main([])


if __name__ == "__main__":
    raise SystemExit(main())
