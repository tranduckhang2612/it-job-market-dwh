"""Load current PostgreSQL analytics job/value associations into SQL Server."""
import argparse
import json
import os
from datetime import timezone

import psycopg
import pyodbc

from data_preprocessing.warehouse import dimensions as legacy

GROUPS = legacy.GROUPS
JOB_FIELDS = ("source", "title", "company_name", "job_url", "crawl_run_id", "observed_at_utc")


def read_source():
    with psycopg.connect("") as pg:
        pg.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        state = pg.execute("SELECT 1 FROM analytics.pipeline_state WHERE model_name='normalize_atomic_v1'")
        if not state.fetchone():
            raise ValueError("Expected normalize_atomic_v1 analytics model")
        jobs = {}
        for job_id, source, title, company, url, run, at in pg.execute(
            "SELECT job_id,source,title,company_name,job_url,crawl_run_id,observed_at "
            "FROM analytics.fact_job_latest"
        ):
            if job_id in jobs:
                raise ValueError("Duplicate current job ID")
            jobs[job_id] = (source, title, company, url, run, at.astimezone(timezone.utc).replace(tzinfo=None))
        groups = {}
        for g in GROUPS:
            key = g + "_key"
            catalog = dict((k, (n, c)) for k, n, c in pg.execute(
                f"SELECT {key},name,category FROM analytics.dim_{g}"))
            if len(set(catalog.values())) != len(catalog):
                raise ValueError(f"Ambiguous natural key in {g}")
            links = {}
            for job, value, level in pg.execute(
                f"SELECT j.job_id,b.{key},b.level_text FROM analytics.bridge_job_{g}_latest b "
                "JOIN analytics.fact_job_latest j USING(observation_key)"
            ):
                links.setdefault((job, catalog[value]), set()).add(level)
            groups[g] = (set(catalog.values()), links)
        return jobs, groups


def setup(c):
    c.execute("IF SCHEMA_ID('dw') IS NULL EXEC('CREATE SCHEMA dw')")
    c.execute("""IF OBJECT_ID('dw.fact_job','U') IS NULL CREATE TABLE dw.fact_job(
        job_key int IDENTITY PRIMARY KEY, job_id nvarchar(256) COLLATE Latin1_General_100_BIN2 NOT NULL UNIQUE,
        source nvarchar(128) NOT NULL, title nvarchar(max), company_name nvarchar(max),
        job_url nvarchar(max), crawl_run_id nvarchar(256), observed_at_utc datetime2(6) NOT NULL)""")
    for g in GROUPS:
        dim, bridge = f"dw.dim_{g}", f"dw.bridge_job_{g}"
        c.execute(f"""IF OBJECT_ID('{dim}','U') IS NULL CREATE TABLE {dim}(
            dim_id int IDENTITY PRIMARY KEY,
            name nvarchar(512) COLLATE Latin1_General_100_BIN2 NOT NULL,
            category nvarchar(128) COLLATE Latin1_General_100_BIN2 NOT NULL,
            created_at_utc datetime2(6) NOT NULL, updated_at_utc datetime2(6) NOT NULL,
            CONSTRAINT UQ_dim_{g}_natural UNIQUE(name,category))""")
        if c.execute(f"SELECT COL_LENGTH('{dim}','row_sha256')").fetchval() is not None:
            c.execute(f"ALTER TABLE {dim} DROP CONSTRAINT CK_dim_{g}_hash, CK_dim_{g}_key, UQ_dim_{g}_analytics")
            c.execute(f"ALTER TABLE {dim} DROP COLUMN row_sha256, analytics_key")
        c.execute(f"""IF OBJECT_ID('{bridge}','U') IS NULL CREATE TABLE {bridge}(
            job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
            dim_id int NOT NULL REFERENCES {dim}(dim_id), context_no int NOT NULL,
            level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no))""")
        c.execute(f"""CREATE OR ALTER VIEW dw.job_{g} AS
            SELECT j.job_id,j.source,j.title,j.company_name,j.observed_at_utc,
                   d.name,d.category,b.level_text
            FROM {bridge} b JOIN dw.fact_job j ON j.job_key=b.job_key
            JOIN {dim} d ON d.dim_id=b.dim_id""")


def apply(conn, data):
    jobs, groups = data
    if not jobs:
        raise ValueError("Empty source snapshot; DW left unchanged")
    try:
        legacy._lock(conn)
        c = conn.cursor()
        setup(c)
        fields = ",".join(JOB_FIELDS)
        old = {r[0]: (r[1], tuple(r[2:])) for r in c.execute(
            f"SELECT job_id,job_key,{fields} FROM dw.fact_job")}
        writes = 0
        for job, values in jobs.items():
            if job not in old:
                c.execute(f"INSERT INTO dw.fact_job(job_id,{fields}) VALUES(?,?,?,?,?,?,?)", job, *values)
                writes += 1
            elif old[job][1] != values:
                assignments = ",".join(f"{f}=?" for f in JOB_FIELDS)
                c.execute(f"UPDATE dw.fact_job SET {assignments} WHERE job_id=?", *values, job)
                writes += 1
        for job in old.keys() - jobs.keys():
            c.execute("DELETE FROM dw.fact_job WHERE job_id=?", job)
            writes += 1
        ids = dict(tuple(r) for r in c.execute("SELECT job_id,job_key FROM dw.fact_job"))
        counts = {}
        for g, (catalog, links) in groups.items():
            dim, bridge = f"dw.dim_{g}", f"dw.bridge_job_{g}"
            keys = {(n, k): i for i, n, k in c.execute(f"SELECT dim_id,name,category FROM {dim}")}
            for name, category in sorted(catalog - keys.keys()):
                c.execute(f"INSERT INTO {dim}(name,category,created_at_utc,updated_at_utc) "
                          "VALUES(?,?,SYSUTCDATETIME(),SYSUTCDATETIME())", name, category)
                writes += 1
            keys = {(n, k): i for i, n, k in c.execute(f"SELECT dim_id,name,category FROM {dim}")}
            actual = {tuple(r[:3]): r[3] for r in c.execute(
                f"SELECT job_key,dim_id,context_no,level_text FROM {bridge}")}
            wanted = {(ids[job], keys[value], i): level
                      for (job, value), levels in links.items()
                      for i, level in enumerate(sorted(levels, key=lambda x: (x is not None, x or "")))}
            for key in actual.keys() - wanted.keys():
                c.execute(f"DELETE FROM {bridge} WHERE job_key=? AND dim_id=? AND context_no=?", *key)
                writes += 1
            for key, value in wanted.items():
                if key not in actual:
                    c.execute(f"INSERT INTO {bridge}(job_key,dim_id,context_no,level_text) VALUES(?,?,?,?)",
                              *key, value)
                    writes += 1
                elif actual[key] != value:
                    c.execute(f"UPDATE {bridge} SET level_text=? WHERE job_key=? AND dim_id=? AND context_no=?",
                              value, *key)
                    writes += 1
            checked = {tuple(r[:3]): r[3] for r in c.execute(
                f"SELECT job_key,dim_id,context_no,level_text FROM {bridge}")}
            if checked != wanted:
                raise ValueError(f"DW reconciliation failed for {g}")
            counts[g] = len(checked)
        conn.commit()
        return dict(jobs=len(ids), links=counts, data_writes=writes)
    except BaseException:
        conn.rollback()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Replace the current DW snapshot; default only previews")
    args = parser.parse_args()
    data = read_source()
    if args.apply:
        conn = pyodbc.connect(os.environ["DW_SQLSERVER_CONNECTION_STRING"], autocommit=False)
        try:
            result = apply(conn, data)
        finally:
            conn.close()
    else:
        jobs, groups = data
        result = dict(jobs=len(jobs), links={g: sum(map(len, v.values())) for g, (_, v) in groups.items()})
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
