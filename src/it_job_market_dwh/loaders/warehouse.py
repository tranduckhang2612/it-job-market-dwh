"""Load the latest normalized PostgreSQL jobs directly into SQL Server DW."""
import argparse
from collections import Counter, defaultdict
import json
import os
from datetime import timezone

import psycopg
from psycopg.rows import dict_row

from it_job_market_dwh.analytics.atomic import GROUPS, atomize
from it_job_market_dwh.normalization.contracts import PERSISTED_NORMALIZED_FIELDS
from it_job_market_dwh.paths import SQL_ROOT
from it_job_market_dwh.warehouse.core_skill_categories import core_skill_category
from it_job_market_dwh.warehouse.dimensions import _lock
from it_job_market_dwh.warehouse.framework_languages import framework_language
from it_job_market_dwh.warehouse.role_categories import role_category

DW_DDL = SQL_ROOT / "dw.sql"
JOB_FIELDS = ("source", "title", "company_name", "job_url", "crawl_run_id", "observed_at_utc")
NORMALIZED_COLUMNS = (*PERSISTED_NORMALIZED_FIELDS, "normalization_version", "normalized_at")
# Keep these projections aligned with the dimension contract in dw.sql.
DIM_FIELDS = {
    "programming_language": ("category",),
    "framework": ("category", "programming_language"),
    "core_skill": ("category",),
    "role": ("category",),
    "city": (),
    "foreign_language": ("level_text",),
    "level": (),
    "soft_skill": ("category", "level_text"),
}
DERIVED_FIELDS = {
    "core_skill": ("category", core_skill_category),
    "role": ("category", lambda name, _: role_category(name)),
    "framework": ("programming_language", lambda name, _: framework_language(name)),
}


def read_source():
    """Read a consistent source snapshot without requiring an analytics schema."""
    jobs = {}
    groups = {group: [] for group in GROUPS}
    with psycopg.connect("", row_factory=dict_row) as pg:
        pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        query = (
            f"SELECT DISTINCT ON (job_id) {','.join(NORMALIZED_COLUMNS)} "
            "FROM normalize_data.normalized_jobs "
            "ORDER BY job_id, observed_at DESC, crawl_run_id DESC"
        )
        for job in pg.execute(query):
            # Reuse the pure Python classification rules, without publishing analytics.
            output = atomize(job)
            scalar = output["scalar"]
            job_id = scalar["job_id"]
            at = scalar["observed_at"].astimezone(timezone.utc).replace(tzinfo=None)
            jobs[job_id] = (*(scalar[field] for field in JOB_FIELDS[:-1]), at)
            for group in GROUPS:
                groups[group].extend((job_id, value["name"], value["category"], value["level_text"])
                                     for value in output["groups"][group])
    return jobs, groups


def setup(cursor):
    cursor.execute(DW_DDL.read_text(encoding="utf-8"))
    # Consume the whole batch before the next statement or COMMIT. sp_rename
    # can produce intermediate ODBC results before the remaining DDL executes.
    while cursor.nextset():
        pass


def date_key(day):
    return day.year * 10000 + day.month * 100 + day.day


def dimension_values(group, job_key, name, category, level):
    attributes = {"category": category, "level_text": level}
    if group in DERIVED_FIELDS:
        field, derive = DERIVED_FIELDS[group]
        attributes[field] = derive(name, attributes.get(field))
    return (job_key, name, *(attributes[field] for field in DIM_FIELDS[group]))


def sync_dimension(cursor, group, wanted):
    dim, key = f"dw.dim_{group}", f"{group}_key"
    fields = ("fact_job_key", "name", *DIM_FIELDS[group])
    columns = ",".join(fields)
    placeholders = ",".join("?" for _ in fields)
    actual = defaultdict(list)
    attribute_updates = {}
    derived = DERIVED_FIELDS.get(group)
    if derived:
        derived_field, derive = derived
        derived_index = fields.index(derived_field)
    for row in cursor.execute(f"SELECT {key},{columns} FROM {dim} ORDER BY {key}"):
        row_id, *values = row
        # Match by the current derivation and update the existing row in place.
        if derived:
            expected = derive(values[1], values[derived_index])
            if expected != values[derived_index]:
                attribute_updates[row_id] = expected
                values[derived_index] = expected
        actual[tuple(values)].append(row_id)

    writes = 0
    # Match as a multiset: IDs of unchanged rows survive, including identical duplicates.
    for occurrence, keys in actual.items():
        keep = wanted[occurrence]
        for row_id in keys[:keep]:
            if row_id in attribute_updates:
                cursor.execute(f"UPDATE {dim} SET {derived_field}=? WHERE {key}=?", attribute_updates[row_id], row_id)
                writes += 1
        for excess in keys[keep:]:
            cursor.execute(f"DELETE FROM {dim} WHERE {key}=?", excess)
            writes += 1
    for occurrence, count in wanted.items():
        for _ in range(count - len(actual.get(occurrence, ()))):
            cursor.execute(f"INSERT INTO {dim}({columns}) VALUES({placeholders})", *occurrence)
            writes += 1
    checked = Counter(tuple(row) for row in cursor.execute(f"SELECT {columns} FROM {dim}"))
    if checked != wanted:
        raise ValueError(f"DW reconciliation failed for {group}")
    return sum(checked.values()), writes


def apply(conn, data):
    jobs, groups = data
    if not jobs:
        raise ValueError("Empty source snapshot; DW left unchanged")
    if set(groups) != set(GROUPS) or any(row[0] not in jobs for rows in groups.values() for row in rows):
        raise ValueError("Expected all dimension groups with valid job IDs")

    try:
        _lock(conn)
        cursor = conn.cursor()
        setup(cursor)
        dates = {values[-1].date() for values in jobs.values()}
        existing_dates = {row[0] for row in cursor.execute("SELECT date_key FROM dw.dim_date")}
        writes = 0
        for day in sorted(dates):
            key = date_key(day)
            if key not in existing_dates:
                cursor.execute("INSERT INTO dw.dim_date(date_key,calendar_date) VALUES(?,?)", key, day)
                writes += 1

        fields = (*JOB_FIELDS, "observed_date_key")
        columns = ",".join(fields)
        placeholders = ",".join("?" for _ in range(len(fields) + 1))
        assignments = ",".join(f"{field}=?" for field in fields)
        old = {row[0]: tuple(row[1:]) for row in cursor.execute(f"SELECT job_id,{columns} FROM dw.fact_job")}
        for job, values in jobs.items():
            values = (*values, date_key(values[-1].date()))
            if job not in old:
                cursor.execute(f"INSERT INTO dw.fact_job(job_id,{columns}) VALUES({placeholders})", job, *values)
                writes += 1
            elif old[job] != values:
                cursor.execute(f"UPDATE dw.fact_job SET {assignments} WHERE job_id=?", *values, job)
                writes += 1
        for job in old.keys() - jobs.keys():
            # Dimension occurrences are removed by their fact FK.
            cursor.execute("DELETE FROM dw.fact_job WHERE job_id=?", job)
            writes += 1

        ids = dict(tuple(row) for row in cursor.execute("SELECT job_id,fact_job_key FROM dw.fact_job"))
        counts = {}
        for group in GROUPS:
            wanted = Counter(
                dimension_values(group, ids[job], name, category, level)
                for job, name, category, level in groups[group]
            )
            counts[group], group_writes = sync_dimension(cursor, group, wanted)
            writes += group_writes
        conn.commit()
        return dict(jobs=len(ids), links=counts, data_writes=writes)
    except BaseException:
        conn.rollback()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Replace the current DW snapshot; default only previews")
    args = parser.parse_args(argv)
    data = read_source()
    if args.apply:
        import pyodbc

        conn = pyodbc.connect(os.environ["DW_SQLSERVER_CONNECTION_STRING"], autocommit=False)
        try:
            result = apply(conn, data)
        finally:
            conn.close()
    else:
        jobs, groups = data
        result = dict(jobs=len(jobs), links={group: len(rows) for group, rows in groups.items()})
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
