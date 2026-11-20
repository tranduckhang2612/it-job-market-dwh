"""Publish and validate the historical dw_v2 model."""

from __future__ import annotations

import argparse
import json
import os

from it_job_market_dwh.paths import SQL_ROOT


SHADOW_SCRIPT_NAMES = (
    "dw_v2.sql",
    "dw_v2_backfill_dimensions.sql",
    "dw_v2_backfill_facts.sql",
    "dw_v2_semantic_views.sql",
    "dw_v2_consumer_cutover.sql",
)

DIRECT_SCRIPT_NAMES = (
    "dw_v2.sql",
    "dw_v2_semantic_views.sql",
    "dw_v2_direct_publish.sql",
)

# Backward-compatible name for callers that inspect the shadow migration order.
SCRIPT_NAMES = SHADOW_SCRIPT_NAMES


def _execute_script(cursor, name: str) -> None:
    sql = (SQL_ROOT / name).read_text(encoding="utf-8")
    cursor.execute(sql)
    while True:
        if cursor.description is not None:
            cursor.fetchall()
        if not cursor.nextset():
            break


def scripts_for(mode: str) -> tuple[str, ...]:
    if mode == "shadow":
        return SHADOW_SCRIPT_NAMES
    if mode == "direct":
        return DIRECT_SCRIPT_NAMES
    raise ValueError(f"Unsupported publish mode: {mode}")


def publish(conn, mode: str = "shadow") -> dict[str, object]:
    """Run every idempotent publish stage and stop on the first failed gate."""
    completed = []
    try:
        cursor = conn.cursor()
        for name in scripts_for(mode):
            _execute_script(cursor, name)
            conn.commit()
            completed.append(name)
    except BaseException:
        conn.rollback()
        raise
    return {"status": "published", "mode": mode, "scripts": completed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Đồng bộ dw_v2, kiểm tra contract BI/ML và cấp quyền đọc.",
    )
    parser.add_argument(
        "--mode",
        choices=("shadow", "direct"),
        default="shadow",
        help="shadow đối soát dw cũ; direct chỉ kiểm tra và publish dbo.",
    )
    args = parser.parse_args(argv)

    script_names = scripts_for(args.mode)

    if not args.apply:
        print(json.dumps({
            "status": "preview", "mode": args.mode, "scripts": list(script_names),
        }))
        return 0
    if not os.environ.get("DW_SQLSERVER_CONNECTION_STRING"):
        parser.error("Thiếu DW_SQLSERVER_CONNECTION_STRING để publish dbo.")

    import pyodbc

    conn = pyodbc.connect(os.environ["DW_SQLSERVER_CONNECTION_STRING"], autocommit=False)
    try:
        result = publish(conn, mode=args.mode)
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
