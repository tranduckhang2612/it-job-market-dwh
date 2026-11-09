"""Crawl and normalize job postings, then load SQL Server DW directly."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "docker" / "docker-compose.yml"
DEFAULT_COOKIE_FILE = ROOT / "data_preprocessing" / "itviec_crawl_data" / "cookie_itviec.txt"


class DatabaseConfigError(RuntimeError):
    """The pipeline cannot select a single PostgreSQL database."""


def database_env() -> dict[str, str]:
    """Give every child command the same PostgreSQL target."""
    env = dict(os.environ)
    if env.get("DATABASE_URL"):
        try:
            from psycopg.conninfo import conninfo_to_dict

            options = conninfo_to_dict(env["DATABASE_URL"])
        except Exception as exc:
            raise DatabaseConfigError("DATABASE_URL không hợp lệ hoặc thiếu psycopg.") from exc
        if not options.get("dbname"):
            raise DatabaseConfigError("DATABASE_URL phải chỉ rõ tên database.")
        names = {
            "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
            "dbname": "PGDATABASE", "user": "PGUSER", "password": "PGPASSWORD",
            "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
            "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
            "connect_timeout": "PGCONNECT_TIMEOUT", "service": "PGSERVICE",
        }
        for key, name in names.items():
            if key in options:
                env[name] = str(options[key])
        return env

    if env.get("PGDATABASE") or env.get("PGSERVICE"):
        return env
    if any(env.get(name) for name in ("PGHOST", "PGHOSTADDR", "PGPORT", "PGUSER")):
        raise DatabaseConfigError("Đã đặt PG* nhưng thiếu PGDATABASE; hãy chọn rõ database nguồn.")

    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "config", "--format", "json"],
            cwd=ROOT, capture_output=True, text=True, timeout=15, check=False,
        )
        if result.returncode:
            raise ValueError("docker compose config failed")
        service = json.loads(result.stdout)["services"]["postgres"]
        settings = service["environment"]
        port = next(
            item for item in service["ports"]
            if str(item["target"]) == "5432" and item.get("protocol", "tcp") == "tcp"
        )
        host = port.get("host_ip") or "127.0.0.1"
        if host in {"0.0.0.0", "::", "[::]"}:
            host = "127.0.0.1"
        env.update({
            "PGHOST": host,
            "PGPORT": str(port["published"]),
            "PGDATABASE": settings["POSTGRES_DB"],
            "PGUSER": settings["POSTGRES_USER"],
            "PGPASSWORD": settings["POSTGRES_PASSWORD"],
        })
        if not all(env[name] for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")):
            raise ValueError("incomplete PostgreSQL configuration")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration, TypeError) as exc:
        raise DatabaseConfigError(
            "Không đọc được PostgreSQL từ Docker Compose; đặt PGDATABASE/PG* hoặc DATABASE_URL."
        ) from exc
    return env


def run_step(arguments: list[str], env: dict[str, str]) -> int:
    command = [sys.executable, str(ROOT / arguments[0]), *arguments[1:]]
    print("$ " + " ".join(shlex.quote(part) for part in command), flush=True)
    return subprocess.run(command, cwd=ROOT, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("both", "itviec", "topdev"), default="both")
    parser.add_argument("--limit", type=int, default=20, help="Số tin cần crawl ở mỗi nguồn.")
    parser.add_argument("--cookie-file", type=Path, help="File Cookie ITviec khác đường dẫn mặc định.")
    parser.add_argument("--itviec-login", action="store_true", help="Đăng nhập ITviec bằng trình duyệt có giao diện.")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--normalize-only", action="store_true", help="Dừng sau bước chuẩn hóa PostgreSQL.")
    destination.add_argument("--out", type=Path, help="Luồng cũ: ghi snapshot analytics vào thư mục này thay vì nạp DW.")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit phải lớn hơn 0.")
    if args.cookie_file and args.itviec_login:
        parser.error("Chọn --cookie-file hoặc --itviec-login.")
    out = args.out.expanduser().resolve() if args.out else None
    if out is not None and out.exists():
        parser.error("--out phải là đường dẫn thư mục chưa tồn tại.")

    sources = ("itviec", "topdev") if args.source == "both" else (args.source,)
    cookie_file = args.cookie_file.expanduser().resolve() if args.cookie_file else DEFAULT_COOKIE_FILE
    if "itviec" in sources and not args.itviec_login and not cookie_file.is_file():
        parser.error(f"Thiếu file Cookie ITviec: {cookie_file}. Dùng --cookie-file hoặc --itviec-login.")
    try:
        env = database_env()
    except DatabaseConfigError as exc:
        print(f"Lỗi cấu hình PostgreSQL: {exc}", file=sys.stderr)
        return 4
    if not args.normalize_only and out is None and not env.get("DW_SQLSERVER_CONNECTION_STRING"):
        parser.error("Thiếu DW_SQLSERVER_CONNECTION_STRING; dùng --normalize-only nếu chỉ chuẩn hóa.")
    print(f"PostgreSQL: {env.get('PGDATABASE') or 'PGSERVICE=' + env['PGSERVICE']}", flush=True)

    for source in sources:
        command = [f"main_{source}.py", "--limit", str(args.limit)]
        if source == "itviec":
            if args.itviec_login:
                command.extend(("--login", "--headed"))
            elif args.cookie_file:
                command.extend(("--cookie-file", str(cookie_file)))
        status = run_step(command, env)
        if status:
            print(f"Dừng tại bước crawl {source} (exit {status}).", file=sys.stderr)
            return status

    scope = [] if args.source == "both" else ["--source", args.source]
    stages = [("normalize", ["load.py", "normalize", *scope])]
    if out is not None:
        stages.append(("analytics", ["load.py", "analytics", "--apply", "--out", str(out), *scope]))
    elif not args.normalize_only:
        # DW reconciles a complete snapshot, even when only one source was crawled.
        stages.append(("warehouse", ["load.py", "warehouse", "--apply"]))
    for label, command in stages:
        status = run_step(command, env)
        if status:
            print(f"Dừng tại bước {label} (exit {status}).", file=sys.stderr)
            return status
    if out is not None:
        print(f"Đã nạp analytics. Snapshot: {out}")
    elif args.normalize_only:
        print("Đã chuẩn hóa dữ liệu PostgreSQL.")
    else:
        print("Đã nạp SQL Server DW trực tiếp từ normalize_data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
