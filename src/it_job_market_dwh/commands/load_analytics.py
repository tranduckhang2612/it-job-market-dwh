"""Compatibility command for it_job_market_dwh.loaders.analytics."""

if __name__ == "__main__":
    from it_job_market_dwh.loaders.analytics import main
    raise SystemExit(main())
else:
    import sys
    from it_job_market_dwh.loaders import analytics as _module
    sys.modules[__name__] = _module
