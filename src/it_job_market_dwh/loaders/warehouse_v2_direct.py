"""Load the latest normalized PostgreSQL observations directly into dbo."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timezone
import hashlib
import json
import os

import psycopg
from psycopg.rows import dict_row

from it_job_market_dwh.analytics.atomic import GROUPS, atomize
from it_job_market_dwh.normalization.contracts import PERSISTED_NORMALIZED_FIELDS
from it_job_market_dwh.paths import SQL_ROOT
from it_job_market_dwh.warehouse.core_skill_categories import core_skill_category
from it_job_market_dwh.warehouse.framework_languages import framework_language
from it_job_market_dwh.warehouse.role_categories import role_category


DW_V2_DDL = SQL_ROOT / "dw_v2.sql"
NECESSARY_GROUPS = (
    "core_skill", "foreign_language", "framework", "programming_language",
)


def read_source():
    """Read one latest normalized observation per job from a consistent snapshot."""
    columns = (*PERSISTED_NORMALIZED_FIELDS, "normalization_version", "normalized_at")
    result = []
    with psycopg.connect("", row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        query = (
            f"SELECT DISTINCT ON (job_id) {','.join(columns)} "
            "FROM normalize_data.normalized_jobs "
            "ORDER BY job_id, observed_at DESC, crawl_run_id DESC"
        )
        for job in conn.execute(query):
            result.append(atomize(job))
    return result


def normalized(value: str) -> str:
    return value.strip().lower()


def utc_naive(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def date_key(day) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def observation_id(job_id: str, crawl_run_id: str) -> str:
    """Match the migration identity so direct and shadow loads can coexist."""
    # SQL Server HASHBYTES receives CONCAT(nvarchar, ...), hence UTF-16LE bytes.
    value = f"{job_id}|{crawl_run_id}".encode("utf-16le")
    return hashlib.sha256(value).hexdigest().upper()


def skill_value(group, row):
    name = row["name"]
    category = row["category"] or ""
    programming_language = None
    if group == "core_skill":
        category = core_skill_category(name, category)
    elif group == "framework":
        programming_language = framework_language(name)
    elif group == "foreign_language":
        category = ""
    return group, name, normalized(name), category, programming_language


def role_value(row):
    name = row["name"]
    return name, normalized(name), role_category(name)


def _consume(cursor) -> None:
    while True:
        if cursor.description is not None:
            cursor.fetchall()
        if not cursor.nextset():
            break


def _ensure_schema(conn) -> None:
    cursor = conn.cursor()
    cursor.execute(DW_V2_DDL.read_text(encoding="utf-8"))
    _consume(cursor)
    conn.commit()


def _acquire_lock(cursor) -> None:
    result = cursor.execute(
        "DECLARE @result int; "
        "EXEC @result=sys.sp_getapplock "
        "@Resource=N'it_job_market_dw:dw_v2:direct_load',"
        "@LockMode=N'Exclusive',@LockOwner=N'Transaction',@LockTimeout=0; "
        "SELECT @result"
    ).fetchone()[0]
    if result < 0:
        raise RuntimeError("Another direct dw_v2 load is already running")


def _insert_dimensions(cursor, observations, writes):
    scalar_rows = [item["scalar"] for item in observations]
    days = {utc_naive(row["observed_at"]).date() for row in scalar_rows}
    existing_dates = {row[0] for row in cursor.execute("SELECT date_key FROM dbo.dim_date")}
    day_names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    month_names = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )
    for day in sorted(days):
        key = date_key(day)
        if key in existing_dates:
            continue
        weekday = day.isoweekday()
        cursor.execute(
            "INSERT INTO dbo.dim_date("
            "date_key,calendar_date,day_of_week,day_name,month_no,month_name,"
            "quarter_no,year_no,is_weekend) VALUES(?,?,?,?,?,?,?,?,?)",
            key, day, weekday, day_names[weekday - 1], day.month,
            month_names[day.month - 1], (day.month - 1) // 3 + 1,
            day.year, int(weekday >= 6),
        )
        writes["dates"] += 1

    for source in sorted({row["source"] for row in scalar_rows}):
        source_name = {"itviec": "ITviec", "topdev": "TopDev"}.get(source, source)
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_source WHERE source_code=?) "
            "INSERT INTO dbo.dim_source(source_code,source_name,is_active) VALUES(?,?,1)",
            source, source, source_name,
        )

    companies = {}
    for row in scalar_rows:
        name = (row.get("company_name") or "").strip()
        if name:
            companies.setdefault(normalized(name), name)
    for normalized_name, name in sorted(companies.items()):
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_company WHERE normalized_name=?) "
            "INSERT INTO dbo.dim_company(company_name,normalized_name) VALUES(?,?)",
            normalized_name, name, normalized_name,
        )

    runs = {}
    for row in scalar_rows:
        at = utc_naive(row["observed_at"])
        run = runs.setdefault(row["crawl_run_id"], {
            "sources": set(), "start": at, "finish": at,
        })
        run["sources"].add(row["source"])
        run["start"] = min(run["start"], at)
        run["finish"] = max(run["finish"], at)
    for run_id, value in sorted(runs.items()):
        scope = next(iter(value["sources"])) if len(value["sources"]) == 1 else "multi"
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_crawl_run WHERE crawl_run_id=?) "
            "INSERT INTO dbo.dim_crawl_run("
            "crawl_run_id,source_scope,pipeline_version,started_at_utc,finished_at_utc,run_status) "
            "VALUES(?,?,N'5.1',?,?,N'succeeded')",
            run_id, run_id, scope, value["start"], value["finish"],
        )

    for row in scalar_rows:
        at = utc_naive(row["observed_at"])
        source_job_id = row.get("source_job_id")
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_job WHERE job_id=?) "
            "INSERT INTO dbo.dim_job("
            "job_id,source_job_id,canonical_job_url,first_seen_at_utc,last_seen_at_utc) "
            "VALUES(?,?,?,?,?)",
            row["job_id"], row["job_id"], source_job_id, row["job_url"], at, at,
        )
        cursor.execute(
            "UPDATE dbo.dim_job SET source_job_id=?,canonical_job_url=?,"
            "first_seen_at_utc=CASE WHEN ?>first_seen_at_utc THEN first_seen_at_utc ELSE ? END,"
            "last_seen_at_utc=CASE WHEN ?<last_seen_at_utc THEN last_seen_at_utc ELSE ? END,"
            "updated_at_utc=SYSUTCDATETIME() WHERE job_id=?",
            source_job_id, row["job_url"], at, at, at, at, row["job_id"],
        )

    skill_rows = {}
    role_rows = {}
    city_rows = {}
    level_rows = {}
    for item in observations:
        for group in (*NECESSARY_GROUPS, "soft_skill"):
            for row in item["groups"][group]:
                value = skill_value(group, row)
                skill_rows.setdefault((value[0], value[2], value[3]), value)
                level = row.get("level_text")
                if group == "foreign_language" and level:
                    level_rows.setdefault(normalized(level), level)
        for row in item["groups"]["role"]:
            value = role_value(row)
            role_rows.setdefault((value[1], value[2]), value)
        for row in item["groups"]["city"]:
            name = row["name"]
            city_rows.setdefault(normalized(name), name)

    for value in sorted(skill_rows.values(), key=lambda row: (row[0], row[2], row[3])):
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_skill "
            "WHERE skill_type=? AND normalized_name=? AND category=?) "
            "INSERT INTO dbo.dim_skill("
            "skill_type,skill_name,normalized_name,category,programming_language) "
            "VALUES(?,?,?,?,?)",
            value[0], value[2], value[3], *value,
        )
    for value in sorted(role_rows.values(), key=lambda row: (row[1], row[2])):
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_role "
            "WHERE normalized_name=? AND role_category=?) "
            "INSERT INTO dbo.dim_role(role_name,normalized_name,role_category) VALUES(?,?,?)",
            value[1], value[2], *value,
        )
    for normalized_name, name in sorted(city_rows.items()):
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_city WHERE normalized_name=?) "
            "INSERT INTO dbo.dim_city(city_name,normalized_name,region_name) VALUES(?,?,NULL)",
            normalized_name, name, normalized_name,
        )
    for normalized_name, name in sorted(level_rows.items()):
        cursor.execute(
            "IF NOT EXISTS (SELECT 1 FROM dbo.dim_proficiency_level WHERE normalized_name=?) "
            "INSERT INTO dbo.dim_proficiency_level(level_code,level_name,normalized_name) "
            "VALUES(NULL,?,?)",
            normalized_name, name, normalized_name,
        )


def apply(conn, observations):
    """Append a normalized snapshot to dw_v2 in one guarded transaction."""
    if not observations:
        raise ValueError("Empty normalized snapshot; dw_v2 left unchanged")
    if any(set(item.get("groups", {})) != set(GROUPS) for item in observations):
        raise ValueError("Expected all normalized dimension groups")

    _ensure_schema(conn)
    cursor = conn.cursor()
    writes = defaultdict(int)
    try:
        _acquire_lock(cursor)
        _insert_dimensions(cursor, observations, writes)

        source_keys = dict(cursor.execute("SELECT source_code,source_key FROM dbo.dim_source"))
        company_keys = dict(cursor.execute("SELECT normalized_name,company_key FROM dbo.dim_company"))
        run_keys = dict(cursor.execute("SELECT crawl_run_id,crawl_run_key FROM dbo.dim_crawl_run"))
        job_keys = dict(cursor.execute("SELECT job_id,job_key FROM dbo.dim_job"))
        skill_keys = {
            (row[0], row[1], row[2]): row[3]
            for row in cursor.execute(
                "SELECT skill_type,normalized_name,category,skill_key FROM dbo.dim_skill"
            )
        }
        role_keys = {
            (row[0], row[1]): row[2]
            for row in cursor.execute(
                "SELECT normalized_name,role_category,role_key FROM dbo.dim_role"
            )
        }
        city_keys = dict(cursor.execute("SELECT normalized_name,city_key FROM dbo.dim_city"))
        level_keys = dict(cursor.execute(
            "SELECT normalized_name,proficiency_level_key FROM dbo.dim_proficiency_level"
        ))

        for item in observations:
            row = item["scalar"]
            at = utc_naive(row["observed_at"])
            job_key = job_keys[row["job_id"]]
            run_key = run_keys[row["crawl_run_id"]]
            source_key = source_keys[row["source"]]
            company_name = (row.get("company_name") or "").strip()
            company_key = company_keys.get(normalized(company_name), 0) if company_name else 0
            key = date_key(at.date())
            oid = observation_id(row["job_id"], row["crawl_run_id"])
            existing = cursor.execute(
                "SELECT observation_id FROM dbo.fact_job_observation "
                "WHERE job_key=? AND crawl_run_key=?",
                job_key, run_key,
            ).fetchone()
            if existing:
                if existing[0] != oid:
                    cursor.execute(
                        "UPDATE dbo.fact_job_necessary_skill SET observation_id=? "
                        "WHERE job_key=? AND crawl_run_key=?",
                        oid, job_key, run_key,
                    )
                    cursor.execute(
                        "UPDATE dbo.fact_job_role_city SET observation_id=? "
                        "WHERE job_key=? AND crawl_run_key=?",
                        oid, job_key, run_key,
                    )
                    cursor.execute(
                        "UPDATE dbo.fact_job_observation SET observation_id=? "
                        "WHERE job_key=? AND crawl_run_key=?",
                        oid, job_key, run_key,
                    )
                    writes["lineage_repairs"] += 1
            else:
                cursor.execute(
                    "INSERT INTO dbo.fact_job_observation("
                    "observation_id,job_key,date_key,source_key,company_key,crawl_run_key,"
                    "observed_at_utc,job_title_snapshot,work_mode) VALUES(?,?,?,?,?,?,?,?,?)",
                    oid, job_key, key, source_key, company_key, run_key,
                    at, row["title"], row.get("work_mode"),
                )
                writes["observations"] += 1

            occurrences = defaultdict(list)
            for group in NECESSARY_GROUPS:
                for skill in item["groups"][group]:
                    value = skill_value(group, skill)
                    occurrences[(value[0], value[2], value[3])].append(skill.get("level_text"))
            for natural, levels in occurrences.items():
                skill_key = skill_keys[natural]
                for context_no, level_text in enumerate(
                    sorted(levels, key=lambda value: (value is not None, value or ""))
                ):
                    level_key = level_keys.get(normalized(level_text), 0) if level_text else 0
                    if cursor.execute(
                        "SELECT COUNT(*) FROM dbo.fact_job_necessary_skill "
                        "WHERE job_key=? AND crawl_run_key=? AND skill_key=? AND context_no=?",
                        job_key, run_key, skill_key, context_no,
                    ).fetchone()[0]:
                        continue
                    cursor.execute(
                        "INSERT INTO dbo.fact_job_necessary_skill("
                        "observation_id,job_key,date_key,source_key,crawl_run_key,skill_key,"
                        "proficiency_level_key,context_no,level_text_snapshot) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        oid, job_key, key, source_key, run_key, skill_key,
                        level_key, context_no, level_text,
                    )
                    writes["skills"] += 1

            roles = {
                role_keys[(value[1], value[2])]
                for value in (role_value(role) for role in item["groups"]["role"])
            } or {0}
            cities = {
                city_keys[normalized(city["name"])]
                for city in item["groups"]["city"]
            } or {0}
            for role_key in roles:
                for city_key in cities:
                    if cursor.execute(
                        "SELECT COUNT(*) FROM dbo.fact_job_role_city "
                        "WHERE job_key=? AND crawl_run_key=? AND role_key=? AND city_key=?",
                        job_key, run_key, role_key, city_key,
                    ).fetchone()[0]:
                        continue
                    cursor.execute(
                        "INSERT INTO dbo.fact_job_role_city("
                        "observation_id,job_key,date_key,source_key,crawl_run_key,role_key,city_key) "
                        "VALUES(?,?,?,?,?,?,?)",
                        oid, job_key, key, source_key, run_key, role_key, city_key,
                    )
                    writes["role_city"] += 1

        conn.commit()
    except BaseException:
        conn.rollback()
        raise

    return {
        "status": "loaded",
        "source_observations": len(observations),
        "inserted": dict(sorted(writes.items())),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Ghi trực tiếp vào dbo.")
    args = parser.parse_args(argv)
    observations = read_source()
    if not args.apply:
        print(json.dumps({"status": "preview", "source_observations": len(observations)}))
        return 0
    if not os.environ.get("DW_SQLSERVER_CONNECTION_STRING"):
        parser.error("Thiếu DW_SQLSERVER_CONNECTION_STRING để ghi dbo.")

    import pyodbc

    conn = pyodbc.connect(os.environ["DW_SQLSERVER_CONNECTION_STRING"], autocommit=False)
    try:
        result = apply(conn, observations)
    finally:
        conn.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
