#!/usr/bin/env python3
"""Canonicalize all supported staging domains and build atomic BI views.

Default: read-only dry-run. --apply is explicit. Supply DATABASE_URL or PG*.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools import clean_staging as c
from data_preprocessing import canonicalization as k

def bi_snapshot(conn):
    exists=conn.execute("SELECT to_regnamespace('bi') IS NOT NULL").fetchone()[0]
    if not exists:return {'exists':False}
    tables={}
    from psycopg import sql
    names=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='bi' ORDER BY tablename")]
    for name in names:
        tables[name]=[r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM bi.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(name)))]
    views={r[0]:r[1] for r in conn.execute("SELECT viewname,definition FROM pg_views WHERE schemaname='bi' ORDER BY viewname")}
    functions=[r[0] for r in conn.execute("SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='bi' ORDER BY proname")]
    return {'exists':True,'tables':tables,'views':views,'functions':functions}

def state(conn):return {'core':c.snapshot(conn),'bi':bi_snapshot(conn)}

def seed_bi(conn):
    from psycopg import sql
    conn.execute((ROOT/'data_preprocessing/bi_atomic.sql').read_text(encoding='utf-8'),prepare=False)
    conn.execute('DELETE FROM bi.value_aliases')
    conn.execute('DELETE FROM bi.canonical_values')
    document,maps=k.catalog()
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO bi.canonical_values(domain,value_key,canonical_name,category) VALUES(%s,%s,%s,%s)',
            [(d,d+':'+k.label_key(r['name']),r['name'],r['category']) for d,rows in document['domains'].items() for r in rows])
        cur.executemany('INSERT INTO bi.value_aliases(domain,alias_key,value_key) VALUES(%s,%s,%s)',
            [(d,a,d+':'+k.label_key(r['name'])) for d,aliases in maps.items() for a,r in aliases.items()])
    conn.execute('INSERT INTO bi.standardization_metadata VALUES(%s,%s) ON CONFLICT(version) DO UPDATE SET catalog_sha256=excluded.catalog_sha256',
                 (document['version'],hashlib.sha256(k.CATALOG_PATH.read_bytes()).hexdigest()))

def profile(conn):
    records=c.jobs(conn)
    result=c.profile(records)
    result['domains']={field:len({str(v) for j in records for v in j[field]})
        for field in ('job_roles','seniority_levels','cities','soft_skills','required_knowledge')}
    result['skill_rows']=sum(len(j['skills']) for j in records)
    result['distinct_skill_names']=len({s['name'] for j in records for s in j['skills']})
    if conn.execute("SELECT to_regclass('bi.fact_job_latest') IS NOT NULL").fetchone()[0]:
        result['bi']={view:conn.execute('SELECT count(*) FROM bi.'+view).fetchone()[0] for view in (
          'fact_job_observations','fact_job_latest','bridge_job_attributes','bridge_job_skills_latest','dim_attribute','review_queue')}
        result['language_demand']=[{'language':n,'distinct_jobs':n_jobs} for n,n_jobs in conn.execute('SELECT * FROM bi.programming_language_demand ORDER BY distinct_jobs DESC,canonical_name')]
        result['unresolved_composites']=conn.execute('SELECT count(*) FROM bi.review_queue WHERE NOT is_atomic').fetchone()[0]
    return result

def validate(conn):
    assert conn.execute('SELECT count(*) FROM bi.fact_job_latest').fetchone()[0]==conn.execute('SELECT count(DISTINCT job_id) FROM staging.dwh_ready_jobs').fetchone()[0]
    assert conn.execute("SELECT count(*) FROM (SELECT observation_id,domain,value_key,education_order,count(*) FROM bi.bridge_job_attributes GROUP BY 1,2,3,4 HAVING count(*)>1)t").fetchone()[0]==0
    assert conn.execute("SELECT count(*) FROM bi.bridge_job_attributes b LEFT JOIN bi.dim_attribute d USING(domain,value_key) WHERE d.value_key IS NULL").fetchone()[0]==0
    assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='bi' AND table_name IN ('fact_job_latest','bridge_job_attributes','dim_attribute') AND (data_type IN ('ARRAY','json','jsonb'))").fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM bi.bridge_job_skills_latest b JOIN bi.fact_job_latest f USING(observation_id,job_id)').fetchone()[0]==conn.execute('SELECT count(*) FROM bi.bridge_job_skills_latest').fetchone()[0]

def apply(conn,fail_after_write=False):
    with conn.transaction():
        c.lock(conn)
        original=state(conn);before=profile(conn)
        metrics,_,_,_=c.apply(conn)
        seed_bi(conn)
        if fail_after_write:raise RuntimeError('injected_atomicity_failure')
        validate(conn)
        after=profile(conn);final=state(conn)
        if original['core']['raw.job_postings']!=final['core']['raw.job_postings']:raise AssertionError('Raw changed')
        if c.plan(conn)[2]:raise AssertionError('Not idempotent')
    return {'version':k.catalog()[0]['version'],'before':before,'after':after,
            'changed_observations':metrics['changed_observations'],'changed_fields':metrics['changed_fields'],
            'changes':metrics['changes'],'raw_unchanged':True,'idempotent':True},original,final

def rollback(conn,before,after):
    from psycopg import sql
    with conn.transaction():
        c.lock(conn)
        if c.digest(state(conn))!=c.digest(after):raise ValueError('Rollback precondition failed; subsequent changes preserved')
        if not before['bi']['exists']:
            conn.execute('DROP SCHEMA bi CASCADE')
        else:
            for table in ('value_aliases','canonical_values','standardization_metadata'):
                conn.execute(sql.SQL('DELETE FROM bi.{}').format(sql.Identifier(table)))
            for table in ('canonical_values','value_aliases','standardization_metadata'):
                for row in before['bi']['tables'][table]:
                    cols=list(row)
                    conn.execute(sql.SQL('INSERT INTO bi.{} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols)),list(row.values()))
            for definition in before['bi']['functions']:conn.execute(definition)
            # All views have the same column contract for this version; definitions are restored in place.
            for name,definition in before['bi']['views'].items():
                conn.execute(sql.SQL('CREATE OR REPLACE VIEW bi.{} AS ').format(sql.Identifier(name))+sql.SQL(definition))
        c.rollback(conn,before['core'],after['core'])
        if c.digest(state(conn))!=c.digest(before):raise AssertionError('Full rollback mismatch')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--rollback',type=Path)
    parser.add_argument('--out',type=Path,default=ROOT/'reports/standardization-20261029/dry-run')
    args=parser.parse_args(argv)
    if args.apply and args.rollback:parser.error('Choose apply or rollback')
    with c.connect() as conn:
        if args.rollback:
            rollback(conn,json.loads((args.rollback/'before.snapshot.json').read_text(encoding='utf-8')),json.loads((args.rollback/'after.snapshot.json').read_text(encoding='utf-8')))
            print('ROLLBACK_OK raw_unchanged=true schema_and_rows_restored=true');return 0
        if args.apply:
            if (args.out/'before.snapshot.json').exists():parser.error('Use a new output directory')
            args.out.mkdir(parents=True,exist_ok=True)
            with conn.transaction():
                c.lock(conn)
                c.write(args.out/'before.snapshot.json',state(conn))
                metrics,before,after=apply(conn)
                c.write(args.out/'after.snapshot.json',after)
                c.write(args.out/'metrics.json',metrics)
            print(f"APPLY_OK observations={metrics['after']['observations']} jobs={metrics['after']['distinct_jobs']} changed={metrics['changed_observations']} raw_unchanged=true atomic=true")
        else:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                before,desired,changes=c.plan(conn)
            c.write(args.out/'plan.json',{'before':c.profile(before),'after':c.profile(desired),'changes':changes})
            print(f'DRY_RUN_OK observations={len(before)} changed={len(changes)} writes=0')
    return 0
if __name__=='__main__':raise SystemExit(main())
