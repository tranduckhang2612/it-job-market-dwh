"""Load the published analytics snapshot into SQL Server DW."""

from __future__ import annotations

import argparse
import os
import sys

if __package__:
    from .main import DatabaseConfigError, database_env, run_step
else:
    from main import DatabaseConfigError, database_env, run_step


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", action="store_true", help="Chỉ xem số tin và liên kết, không ghi SQL Server.")
    args = parser.parse_args(argv)
    if not args.preview and not os.environ.get("DW_SQLSERVER_CONNECTION_STRING"):
        parser.error("Thiếu DW_SQLSERVER_CONNECTION_STRING để ghi SQL Server.")
    try:
        env = database_env()
    except DatabaseConfigError as exc:
        print(f"Lỗi cấu hình PostgreSQL: {exc}", file=sys.stderr)
        return 4
    print(f"Nguồn analytics PostgreSQL: {env.get('PGDATABASE') or 'PGSERVICE=' + env['PGSERVICE']}", flush=True)
    status = run_step(["load.py", "warehouse", *([] if args.preview else ["--apply"])], env)
    if status:
        print(f"Nạp SQL Server DW thất bại (exit {status}).", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
