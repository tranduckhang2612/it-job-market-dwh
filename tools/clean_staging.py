#!/usr/bin/env python3
"""Profile/replay existing raw data atomically, without crawling or deleting jobs.

Connection: DATABASE_URL or libpq PG* variables. No credentials are stored here.
Default mode is a read-only dry-run. --apply is explicit.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data_preprocessing import staging_cleaning as rules
from data_preprocessing.itviec_normalize_data import normalize_itviec as iv
from data_preprocessing.topdev_normalize_data import normalize_topdev as td

TABLES = ("raw.crawl_runs", "raw.job_postings", "staging.job_observations",
          *("staging." + t for t in iv.STAGING_CHILD_COLUMNS), "staging.job_education_majors")
VIEWS = ("staging.job_quality_flags", "staging.it_candidate_jobs")


def encode(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode)


def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode) + "\n", encoding="utf-8")


def connect(dsn=None, **kwargs):
    import psycopg
    return psycopg.connect(dsn or os.environ.get("DATABASE_URL", ""), autocommit=True,
                          connect_timeout=10, application_name="staging_cleaning", **kwargs)


def snapshot(conn):
    from psycopg import sql
    result = {}
    for table in TABLES:
        query = sql.SQL("SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text").format(sql.Identifier(*table.split(".")))
        result[table] = [r[0] for r in conn.execute(query)]
    result["__views__"] = {v: conn.execute("SELECT pg_get_viewdef(to_regclass(%s),true)", (v,)).fetchone()[0] for v in VIEWS}
    return result


def canonical(job):
    result = json.loads(dumps(job))
    for field in ("observed_at", "published_at"):
        if result.get(field):
            value = result[field].replace("Z", "+00:00")
            value = re.sub(r"(\.\d{1,6})(?=[+-]\d{2}:\d{2}$)", lambda m: "." + m.group(1)[1:].ljust(6, "0"), value)
            result[field] = datetime.fromisoformat(value).astimezone(timezone.utc).isoformat()
    return result


def jobs(conn):
    return [canonical(r[0]) for r in conn.execute("SELECT to_jsonb(j) FROM staging.jobs j ORDER BY job_id,crawl_run_id")]


def profile(records):
    missing = {f: sum(j[f] is None or j[f] == [] for j in records)
               for f in ("company_name", "published_at", "cities", "skills", "work_mode", "experience_text",
                         "experience_min_years", "salary_min", "salary_max", "salary_currency", "salary_period", "salary_basis")}
    return {"observations": len(records), "distinct_jobs": len({j['job_id'] for j in records}),
            "sources": dict(Counter(j["source"] for j in records)),
            "runs": len({j["crawl_run_id"] for j in records}),
            "salary_status": dict(Counter(j["salary_status"] for j in records)), "missing": missing,
            "numeric_experience": sum(j["experience_min_years"] is not None or j["experience_max_years"] is not None for j in records),
            "likely_non_it_observations": sum(rules.likely_non_it(j) for j in records),
            "likely_non_it_jobs": len({j["job_id"] for j in records if rules.likely_non_it(j)}),
            "noncanonical_cities": sum(c != rules.canonical_city(c) for j in records for c in j["cities"]),
            "noncanonical_skill_rows": sum(j["skills"] != rules.clean_record(j)["skills"] for j in records),
            "duplicate_observation_keys": len(records) - len({(j["job_id"], j["crawl_run_id"]) for j in records})}


def plan(conn):
    before = jobs(conn)
    current = {(j["job_id"], j["crawl_run_id"]): j for j in before}
    desired, changes = [], []
    # This tool cleans observations already in staging; it does not silently
    # publish raw rows rejected by an earlier pipeline.
    rows = conn.execute("""SELECT r.job_id, r.crawl_run_id, c.source, r.source_job_id,
                                  r.job_url, r.observed_at, r.raw_payload
                           FROM raw.job_postings r JOIN raw.crawl_runs c USING (crawl_run_id)
                           JOIN staging.job_observations o USING (job_id,crawl_run_id)
                           ORDER BY r.job_id,r.crawl_run_id""").fetchall()
    if len(rows) != len(before):
        raise ValueError("Staging/raw lineage count mismatch")
    for jid, run, source, sid, url, at, payload in rows:
        normalizer = {"itviec": iv, "topdev": td}[source]
        j = canonical(normalizer.normalize_job(payload, run, observed_at=at.isoformat()))
        if (j["job_id"], j["source_job_id"], j["job_url"], j["observed_at"]) != (jid, sid, url, at.astimezone(timezone.utc).isoformat()):
            raise ValueError("Normalization changed immutable raw lineage: " + jid)
        old = current[(jid, run)]
        diff = {f: {"before": old[f], "after": j[f]} for f in iv.NORMALIZED_FIELDS if old[f] != j[f]}
        if diff:
            changes.append({"job_id": jid, "crawl_run_id": run, "fields": diff})
        desired.append(j)
    return before, desired, changes


def lock(conn):
    from psycopg import sql
    # All crawler/staging writers are blocked while the snapshot and changes
    # are checked. A fixed timeout prevents waiting indefinitely.
    conn.execute("SET LOCAL lock_timeout = '10s'")
    conn.execute(sql.SQL("LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE").format(
        sql.SQL(",").join(sql.Identifier(*t.split(".")) for t in TABLES)))


def apply(conn):
    from psycopg.types.json import Jsonb
    with conn.transaction():
        lock(conn)
        original = snapshot(conn)
        before, desired, changes = plan(conn)
        identities = [(r["observation_id"], r["job_id"], r["crawl_run_id"]) for r in original["staging.job_observations"]]
        ready_before = conn.execute("SELECT count(*) FROM staging.dwh_ready_jobs").fetchone()[0]
        changed_keys = {(c["job_id"], c["crawl_run_id"]) for c in changes}
        fields = Counter(f for c in changes for f in c["fields"])
        for job in desired:
            if (job["job_id"], job["crawl_run_id"]) in changed_keys:
                ordered = {f: job[f] for f in iv.NORMALIZED_FIELDS}
                iv._database_write_job(conn, ordered)
        conn.execute((ROOT / "data_preprocessing/staging_quality.sql").read_text(encoding="utf-8"))
        for run in {c["crawl_run_id"] for c in changes}:
            meta = {"version": rules.CLEANING_VERSION, "changed_observations": sum(c["crawl_run_id"] == run for c in changes)}
            conn.execute("UPDATE raw.crawl_runs SET metadata=jsonb_set(metadata,'{cleaning}',%s,true) WHERE crawl_run_id=%s",
                         (Jsonb(meta), run))
        after = jobs(conn)
        if after != desired:
            raise AssertionError("Read-back does not equal the complete 30-field plan")
        final = snapshot(conn)
        if original["raw.job_postings"] != final["raw.job_postings"]:
            raise AssertionError("Raw postings changed")
        if sorted(identities) != sorted((r["observation_id"], r["job_id"], r["crawl_run_id"]) for r in final["staging.job_observations"]):
            raise AssertionError("Observation identity changed")
        if ready_before != conn.execute("SELECT count(*) FROM staging.dwh_ready_jobs").fetchone()[0]:
            raise AssertionError("DWH-ready coverage changed")
        if plan(conn)[2]:
            raise AssertionError("Second cleaning pass is not idempotent")
    return {"before": profile(before), "after": profile(after), "changed_observations": len(changes),
            "changed_fields": dict(fields), "changes": changes, "raw_unchanged": True,
            "observation_ids_unchanged": True, "idempotent": True, "readback_30_fields": True,
            "dwh_ready_rows": ready_before}, original, final, after


def rollback(conn, original, expected):
    from psycopg import sql
    from psycopg.types.json import Jsonb
    with conn.transaction():
        lock(conn)
        if digest(snapshot(conn)) != digest(expected):
            raise ValueError("Database changed since cleaning; rollback precondition failed")
        # Restore parents in place, including technical timestamps and original
        # observation IDs; source postings are never touched.
        for row in original["staging.job_observations"]:
            columns = [k for k in row if k != "observation_id"]
            query = sql.SQL("UPDATE staging.job_observations SET {} WHERE observation_id=%s").format(
                sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in columns))
            conn.execute(query, [row[k] for k in columns] + [row["observation_id"]])
        for table in reversed(TABLES[3:]):
            conn.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(*table.split("."))))
        for table in TABLES[3:]:
            for row in original[table]:
                columns = list(row)
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(*table.split(".")), sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() for _ in columns))
                conn.execute(query, list(row.values()))
        for row in original["raw.crawl_runs"]:
            conn.execute("UPDATE raw.crawl_runs SET metadata=%s WHERE crawl_run_id=%s",
                         (Jsonb(row["metadata"]), row["crawl_run_id"]))
        # These two additive views belong to this cleaning migration only.
        for view in reversed(VIEWS):
            conn.execute(sql.SQL("DROP VIEW IF EXISTS {}").format(sql.Identifier(*view.split("."))))
        for view in VIEWS:
            definition = original.get("__views__", {}).get(view)
            if definition:
                conn.execute(sql.SQL("CREATE VIEW {} AS ").format(sql.Identifier(*view.split("."))) + sql.SQL(definition))
        if digest(snapshot(conn)) != digest(original):
            raise AssertionError("Rollback snapshot mismatch")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", type=Path, help="Directory containing before/after snapshots from --apply")
    parser.add_argument("--out", type=Path, default=ROOT / "reports/staging-cleaning-20261027/dry-run")
    args = parser.parse_args(argv)
    if args.apply and args.rollback:
        parser.error("Choose either apply or rollback")
    with connect() as conn:
        if args.rollback:
            original = json.loads((args.rollback / "before.snapshot.json").read_text(encoding="utf-8"))
            expected = json.loads((args.rollback / "after.snapshot.json").read_text(encoding="utf-8"))
            rollback(conn, original, expected)
            print("ROLLBACK: original staging/raw metadata restored; raw postings unchanged")
            return 0
        if args.apply:
            if (args.out / "before.snapshot.json").exists():
                parser.error("Use a new output directory; existing rollback evidence is preserved")
            # Save rollback input BEFORE any transaction writes, and never
            # overwrite evidence from a previous application.
            args.out.mkdir(parents=True, exist_ok=True)
            with conn.transaction():
                lock(conn)
                write(args.out / "before.snapshot.json", snapshot(conn))
                metrics, original, final, after = apply(conn)
                write(args.out / "after.snapshot.json", final)
                write(args.out / "MODIFIED_FILE.json", after)
                write(args.out / "metrics.json", metrics)
            print(f"APPLY: rows={len(after)} changed={metrics['changed_observations']} raw_unchanged=true idempotent=true")
        else:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                before, desired, changes = plan(conn)
            write(args.out / "plan.json", {"before": profile(before), "after": profile(desired), "changes": changes})
            print(f"DRY_RUN: rows={len(before)} changed={len(changes)} writes=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
