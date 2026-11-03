"""Compatibility command for it_job_market_dwh.loaders.staging."""

if __name__ == "__main__":
    from it_job_market_dwh.loaders.staging import main
    raise SystemExit(main())
else:
    import sys
    from it_job_market_dwh.loaders import staging as _module
    sys.modules[__name__] = _module
