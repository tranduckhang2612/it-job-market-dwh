"""Step 3: staging -> persistent analytics, never write back to staging/raw.

DATABASE_URL / PG* configure the connection. Default: read-only preview.
--apply publishes eligible batches; --crawl-run-id restricts the publication.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from psycopg import sql
from tools import clean_staging as c
from tools import analytics_state as s
from data_preprocessing import canonicalization as k
from data_preprocessing.analytics_transform import transform_record, TRANSFORM_VERSION

FILES = [ROOT / 'data_preprocessing' / name for name in
         ('analytics_storage.sql', 'analytics_contract.sql', 'analytics.sql')]
DATA_TABLES = ['job_observations', *c.iv.STAGING_CHILD_COLUMNS, 'job_education_majors', 'load_state']
TABLE_ORDER = ['pipeline_state', 'canonical_values', 'value_aliases', 'standardization_metadata', *DATA_TABLES]
MANAGED_VIEWS = set(re.findall(r'VIEW analytics\.(\w+) AS', '\n'.join(p.read_text(encoding='utf-8') for p in FILES)))
LINEAGE = ('job_id', 'source', 'source_job_id', 'job_url', 'crawl_run_id', 'observed_at', 'published_at')


def signatures():
    transform = hashlib.sha256(b''.join(p.read_bytes() for p in
        [ROOT/'data_preprocessing/analytics_transform.py', ROOT/'data_preprocessing/canonicalization.py', k.CATALOG_PATH])).hexdigest()
    model = hashlib.sha256(b''.join(p.read_bytes() for p in FILES) + k.CATALOG_PATH.read_bytes()).hexdigest()
    return model, transform


def lock(conn):
    if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('analytics:step3',0))").fetchone()[0]:
        raise ValueError('Another analytics publication is running')


def check_owned(conn):
    if conn.execute("SELECT to_regnamespace('bi')").fetchone()[0]:
        raise ValueError('Legacy bi schema found; reconcile the contract 1.2 migration first')
    saved = s.layer(conn, 'analytics')
    if saved['exists']:
        unknown = set(saved['tables']) - set(TABLE_ORDER)
        unknown |= set(saved['views']['definitions']) - MANAGED_VIEWS
        if unknown:
            raise ValueError('Unmanaged analytics objects retained: '+','.join(sorted(unknown)))


def configure(conn):
    model, _ = signatures()
    exists = conn.execute("SELECT to_regclass('analytics.pipeline_state')").fetchone()[0]
    if exists and conn.execute('SELECT model_sha256 FROM analytics.pipeline_state WHERE singleton').fetchone() == (model,):
        return False
    check_owned(conn)
    for path in FILES:
        conn.execute(path.read_text(encoding='utf-8'), prepare=False)
    conn.execute('DELETE FROM analytics.value_aliases')
    conn.execute('DELETE FROM analytics.canonical_values')
    document, maps = k.catalog()
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO analytics.canonical_values VALUES(%s,%s,%s,%s)',
            [(d,d+':'+k.label_key(r['name']),r['name'],r['category']) for d,rows in document['domains'].items() for r in rows])
        cur.executemany('INSERT INTO analytics.value_aliases VALUES(%s,%s,%s)',
            [(d,a,d+':'+k.label_key(r['name'])) for d,mapping in maps.items() for a,r in mapping.items()])
    conn.execute('INSERT INTO analytics.standardization_metadata VALUES(%s,%s) ON CONFLICT(version) DO UPDATE SET catalog_sha256=excluded.catalog_sha256',
                 (document['version'], hashlib.sha256(k.CATALOG_PATH.read_bytes()).hexdigest()))
    conn.execute('INSERT INTO analytics.pipeline_state VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET model_sha256=excluded.model_sha256',(model,))
    return True


def plan(conn, crawl_run_id=None):
    _, transform = signatures()
    query = '''SELECT o.observation_id,to_jsonb(j) FROM staging.dwh_ready_jobs j
               JOIN staging.job_observations o USING(job_id,crawl_run_id)'''
    params = ()
    if crawl_run_id:
        query += ' WHERE j.crawl_run_id=%s'
        params = (crawl_run_id,)
    rows = conn.execute(query+' ORDER BY o.observation_id', params).fetchall()
    if crawl_run_id and not rows:
        raise ValueError('Batch absent or not fully eligible: '+crawl_run_id)
    runs = Counter(payload['crawl_run_id'] for _,payload in rows)
    for run in runs:
        expected = conn.execute('SELECT count(*) FROM raw.job_postings WHERE crawl_run_id=%s',(run,)).fetchone()[0]
        if runs[run] != expected:
            raise ValueError('Incomplete eligible batch: '+run)
    state_exists = conn.execute("SELECT to_regclass('analytics.load_state')").fetchone()[0]
    saved = dict((r[0],r[1:]) for r in conn.execute(
        'SELECT observation_id,source_sha256,output_sha256,transform_sha256 FROM analytics.load_state')) if state_exists else {}
    published = {r[0]:c.canonical(r[1]) for r in conn.execute(
        'SELECT o.observation_id,to_jsonb(j) FROM analytics.jobs j JOIN analytics.job_observations o USING(job_id,crawl_run_id)')} if state_exists else {}
    result = []
    for oid,payload in rows:
        source = c.canonical(payload)
        normalizer = c.iv if source['source']=='itviec' else c.td
        normalizer.validate_normalized_job({f:source[f] for f in normalizer.NORMALIZED_FIELDS})
        output = c.canonical(transform_record(source))
        normalizer.validate_normalized_job({f:output[f] for f in normalizer.NORMALIZED_FIELDS})
        if any(source[f] != output[f] for f in LINEAGE):
            raise ValueError('Step 3 changed source lineage')
        source_sha, output_sha = c.digest({'observation_id':oid,'job':source}), c.digest(output)
        old = saved.get(oid)
        if old and (oid not in published or c.digest(published[oid]) != old[1]):
            raise ValueError('Published analytics row changed outside loader: '+str(oid))
        unchanged = old == (source_sha,output_sha,transform)
        result.append({'observation_id':oid,'job':output,'source_sha256':source_sha,
                       'output_sha256':output_sha,'transform_sha256':transform,'changed':not unchanged})
    return result


def write_job(conn, item):
    oid, job = item['observation_id'], item['job']
    existing = conn.execute('SELECT job_id,crawl_run_id FROM analytics.job_observations WHERE observation_id=%s',(oid,)).fetchone()
    if existing and existing != (job['job_id'],job['crawl_run_id']):
        raise ValueError('Observation ID lineage collision')
    columns = ('observation_id',)+c.iv.DB_SCALAR_FIELDS+('normalization_version',)
    update = sql.SQL(',').join(sql.SQL('{}=EXCLUDED.{}').format(sql.Identifier(f),sql.Identifier(f)) for f in columns if f!='observation_id')
    query = sql.SQL('INSERT INTO analytics.job_observations ({}) VALUES ({}) ON CONFLICT(observation_id) DO UPDATE SET {},normalized_at=CURRENT_TIMESTAMP').format(
        sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns),update)
    conn.execute(query,(oid,)+tuple(job[f] for f in c.iv.DB_SCALAR_FIELDS)+('1.2',))
    rows = c.iv._database_child_rows(job,oid)
    for table, fields in c.iv.STAGING_CHILD_COLUMNS.items():
        conn.execute(sql.SQL('DELETE FROM analytics.{} WHERE observation_id=%s').format(sql.Identifier(table)),(oid,))
        if rows[table]:
            cols = ('observation_id','item_order')+fields
            with conn.cursor() as cur:
                cur.executemany(sql.SQL('INSERT INTO analytics.{} ({}) VALUES ({})').format(sql.Identifier(table),
                    sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols)),rows[table])
    if rows['job_education_majors']:
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO analytics.job_education_majors(observation_id,education_order,item_order,major) VALUES(%s,%s,%s,%s)',rows['job_education_majors'])
    conn.execute('''INSERT INTO analytics.load_state VALUES(%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
        ON CONFLICT(observation_id) DO UPDATE SET source_sha256=excluded.source_sha256,
        output_sha256=excluded.output_sha256,transform_sha256=excluded.transform_sha256,
        transform_version=excluded.transform_version,loaded_at=excluded.loaded_at''',
        (oid,item['source_sha256'],item['output_sha256'],item['transform_sha256'],TRANSFORM_VERSION))


def validate(conn):
    for payload, in conn.execute('SELECT to_jsonb(j) FROM analytics.jobs j'):
        record = c.canonical(payload)
        normalizer = c.iv if record['source']=='itviec' else c.td
        normalizer.validate_normalized_job({f:record[f] for f in normalizer.NORMALIZED_FIELDS})
    if conn.execute('SELECT count(*) FROM analytics.job_observations').fetchone() != conn.execute('SELECT count(*) FROM analytics.load_state').fetchone():
        raise ValueError('Analytics observation/load-state coverage mismatch')
    if conn.execute('SELECT count(*) FROM analytics.bridge_job_skills_latest b LEFT JOIN analytics.dim_skills d USING(skill_key) WHERE d.skill_key IS NULL').fetchone()[0]:
        raise ValueError('Orphan skill keys')
    if conn.execute('SELECT count(*) FROM analytics.role_skill_demand_latest WHERE job_pct<0 OR job_pct>100').fetchone()[0]:
        raise ValueError('Invalid percentages')


def apply(conn, crawl_run_id=None, fail_after_write=False, out=None):
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        lock(conn)
        before = s.snapshot(conn)
        if out:
            c.write(out/'before.snapshot.json',before)
        items = plan(conn,crawl_run_id)
        configured = configure(conn)
        changed = [item for item in items if item['changed']]
        for item in changed:
            write_job(conn,item)
            if fail_after_write:
                raise RuntimeError('injected_atomicity_failure')
        validate(conn)
        readback = {r[0]:c.canonical(r[1]) for r in conn.execute('SELECT o.observation_id,to_jsonb(j) FROM analytics.jobs j JOIN analytics.job_observations o USING(job_id,crawl_run_id)')}
        if any(readback.get(item['observation_id']) != item['job'] for item in items):
            raise ValueError('Analytics read-back differs from step-3 output')
        after = s.snapshot(conn)
        if c.digest(before['upstream']) != c.digest(after['upstream']):
            raise ValueError('Raw/staging changed during publication')
        metrics = {'changed':bool(configured or changed),'selected_observations':len(items),
                   'written_observations':len(changed),'skipped_observations':len(items)-len(changed),
                   'raw_writes':0,'staging_writes':0,
                   'analytics_observations':conn.execute('SELECT count(*) FROM analytics.job_observations').fetchone()[0],
                   'latest_jobs':conn.execute('SELECT count(*) FROM analytics.fact_job_latest').fetchone()[0]}
        if out:
            c.write(out/'after.snapshot.json',after)
            c.write(out/'metrics.json',metrics)
        return metrics,before,after


def rollback(conn, before, after):
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        lock(conn)
        if c.digest(s.snapshot(conn)) != c.digest(after):
            raise ValueError('Rollback precondition failed: subsequent changes retained')
        check_owned(conn)
        current = s.layer(conn,'analytics')
        saved = before['analytics']
        for name in reversed(current['views']['order']):
            conn.execute(sql.SQL('DROP VIEW analytics.{}').format(sql.Identifier(name)))
        for name in reversed(TABLE_ORDER):
            if name in current['tables']:
                conn.execute(sql.SQL('DELETE FROM analytics.{}').format(sql.Identifier(name)))
                if not saved['exists'] or name not in saved['tables']:
                    conn.execute(sql.SQL('DROP TABLE analytics.{}').format(sql.Identifier(name)))
        if saved['exists']:
            for name in TABLE_ORDER:
                for row in saved['tables'].get(name,[]):
                    cols = list(row)
                    conn.execute(sql.SQL('INSERT INTO analytics.{} ({}) VALUES ({})').format(sql.Identifier(name),
                        sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols)),list(row.values()))
            for definition in saved['functions']:
                conn.execute(definition)
            for name in saved['views']['order']:
                conn.execute(sql.SQL('CREATE VIEW analytics.{} AS ').format(sql.Identifier(name))+sql.SQL(saved['views']['definitions'][name]))
        else:
            conn.execute('DROP FUNCTION analytics.label_key(text)')
            conn.execute('DROP SCHEMA analytics')
        if c.digest(s.snapshot(conn)) != c.digest(before):
            raise ValueError('Rollback state mismatch')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--apply',action='store_true')
    mode.add_argument('--rollback',type=Path)
    p.add_argument('--crawl-run-id')
    p.add_argument('--out',type=Path,default=ROOT/'reports/analytics-physical/check')
    a = p.parse_args(argv)
    if a.crawl_run_id is not None:
        a.crawl_run_id = a.crawl_run_id.strip()
        if not a.crawl_run_id or a.rollback:
            p.error("Use a nonempty crawl-run-id for check/apply only")
    with c.connect() as conn:
        if a.rollback:
            rollback(conn,json.loads((a.rollback/'before.snapshot.json').read_text(encoding='utf-8')),
                     json.loads((a.rollback/'after.snapshot.json').read_text(encoding='utf-8')))
            print('ROLLBACK_OK analytics_restored=true raw_writes=0 staging_writes=0')
        elif a.apply:
            if (a.out/'before.snapshot.json').exists():
                p.error('Use a new output directory for each publication')
            metrics,_,_ = apply(conn,a.crawl_run_id,out=a.out)
            print('APPLY_OK '+json.dumps(metrics,sort_keys=True))
        else:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
                items = plan(conn,a.crawl_run_id)
                result = {'selected_observations':len(items),'pending_observations':sum(i['changed'] for i in items),'writes':0}
            c.write(a.out/'status.json',result)
            print('CHECK_OK '+json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
