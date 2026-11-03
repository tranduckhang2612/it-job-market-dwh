"""Transactional normalize_data -> analytics publication, with guarded rollback."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re

from psycopg import sql
from psycopg.rows import dict_row
from it_job_market_dwh.analytics import atomic as a
from it_job_market_dwh.normalization.contracts import PERSISTED_NORMALIZED_FIELDS
from it_job_market_dwh.paths import SQL_ROOT

DDL = SQL_ROOT / 'analytics_schema.sql'
SOURCE = 'normalize_data.normalized_jobs'
MODEL_NAME = 'normalize_atomic_v1'
TABLES = ['pipeline_state', 'job_observations']
for _g in a.GROUPS:
    TABLES.extend(['dim_'+_g, 'bridge_job_'+_g])
TABLES.append('observation_issues')
VIEWS = re.findall(r'CREATE OR REPLACE VIEW analytics\.(\w+) AS', DDL.read_text(encoding='utf-8'))
FACT_FIELDS = ('observation_key', *a.SCALARS, 'foreign_language_status', 'source_normalized_at',
               'source_normalization_version', 'source_sha256', 'output_sha256', 'model_sha256')


def signature():
    return hashlib.sha256(DDL.read_bytes() + Path(a.__file__).read_bytes() + a.CATALOG.read_bytes()).hexdigest()


def exists(conn):
    return bool(conn.execute("SELECT to_regclass('analytics.pipeline_state')").fetchone()[0])


def source_fingerprint(conn):
    hasher = hashlib.sha256()
    with conn.cursor() as cur:
        cur.execute('SELECT xmin::text,to_jsonb(j)::text FROM normalize_data.normalized_jobs j ORDER BY job_id,crawl_run_id')
        while rows := cur.fetchmany(500):
            for row in rows:
                data = a.dumps(row).encode('utf-8')
                hasher.update(len(data).to_bytes(8, 'big') + data)
    return hasher.hexdigest()


def check_source(conn):
    if not conn.execute('SELECT to_regclass(%s)', (SOURCE,)).fetchone()[0]:
        raise ValueError('Missing source normalize_data.normalized_jobs; run the upstream loader first')
    columns = tuple(r[0] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='normalize_data' AND table_name='normalized_jobs' ORDER BY ordinal_position"))
    if columns != PERSISTED_NORMALIZED_FIELDS + ('normalization_version', 'normalized_at'):
        raise ValueError('Source does not match normalize_data 5.1 storage columns')


def check_owned(conn):
    present = bool(conn.execute("SELECT to_regnamespace('analytics')").fetchone()[0])
    if not present:
        return
    tables = {r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='analytics'")}
    views = {r[0] for r in conn.execute("SELECT viewname FROM pg_views WHERE schemaname='analytics'")}
    extras = tables-set(TABLES) | views-set(VIEWS)
    if extras or (tables and not exists(conn)):
        raise ValueError('Existing analytics model differs; retained without changes')
    if exists(conn):
        row = conn.execute('SELECT model_name FROM analytics.pipeline_state WHERE singleton').fetchone()
        if row != (MODEL_NAME,):
            raise ValueError('analytics owner/model marker mismatch')
        if tables != set(TABLES) or views != set(VIEWS):
            raise ValueError('Incomplete managed analytics model')


def snapshot(conn):
    check_source(conn)
    check_owned(conn)
    layer_exists = bool(conn.execute("SELECT to_regnamespace('analytics')").fetchone()[0])
    tables = {}
    if exists(conn):
        for name in TABLES:
            tables[name] = [r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM analytics.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(name)))]
    return {'source_sha256': source_fingerprint(conn), 'analytics_exists': layer_exists, 'tables': tables}


def configure(conn):
    check_owned(conn)
    if exists(conn) and conn.execute('SELECT model_sha256 FROM analytics.pipeline_state WHERE singleton').fetchone() == (signature(),):
        return False
    conn.execute(DDL.read_text(encoding='utf-8'), prepare=False)
    conn.execute('INSERT INTO analytics.pipeline_state VALUES(true,%s,%s) ON CONFLICT(singleton) DO UPDATE SET model_sha256=excluded.model_sha256', (MODEL_NAME, signature()))
    return True


def read_source(conn, crawl_run_id=None, source=None):
    fields = PERSISTED_NORMALIZED_FIELDS + ('normalization_version', 'normalized_at')
    query = sql.SQL('SELECT {} FROM normalize_data.normalized_jobs').format(sql.SQL(',').join(map(sql.Identifier, fields)))
    conditions, params = [], []
    for field, value in [('crawl_run_id', crawl_run_id), ('source', source)]:
        if value is not None:
            conditions.append(sql.SQL('{}=%s').format(sql.Identifier(field)))
            params.append(value)
    if conditions:
        query += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(conditions)
    query += sql.SQL(' ORDER BY job_id,crawl_run_id')
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        while rows := cur.fetchmany(500):
            yield from rows


def read_output(conn, oid):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute('SELECT '+','.join(a.SCALARS)+',foreign_language_status FROM analytics.job_observations WHERE observation_key=%s', (oid,))
        scalar = cur.fetchone()
        groups = {}
        for g in a.GROUPS:
            cur.execute(sql.SQL('SELECT d.{} AS value_key,d.name,d.category,b.context_key,b.level_text FROM analytics.{} b JOIN analytics.{} d USING({}) WHERE b.observation_key=%s ORDER BY 1,4').format(
                sql.Identifier(g+'_key'), sql.Identifier('bridge_job_'+g), sql.Identifier('dim_'+g), sql.Identifier(g+'_key')), (oid,))
            groups[g] = cur.fetchall()
        cur.execute('SELECT issue_key,field_name,source_value,reason FROM analytics.observation_issues WHERE observation_key=%s ORDER BY issue_key', (oid,))
        issues = cur.fetchall()
    return {'observation_key': oid, 'scalar': scalar, 'groups': groups, 'issues': issues}


def plan(conn, crawl_run_id=None, source=None):
    check_source(conn)
    check_owned(conn)
    stored = {}
    if exists(conn):
        stored = {r[0]: r[1:] for r in conn.execute('SELECT observation_key,source_sha256,output_sha256,model_sha256,source,crawl_run_id FROM analytics.job_observations')}
    desired = []
    for job in read_source(conn, crawl_run_id, source):
        output = a.atomize(job)
        oid = output['observation_key']
        previous = stored.get(oid)
        if previous and a.digest(read_output(conn, oid)) != previous[1]:
            raise ValueError('Analytics output was manually changed: '+job['job_id'])
        source_hash, output_hash = a.digest(job), a.digest(output)
        desired.append({'job': job, 'output': output, 'source_sha256': source_hash, 'output_sha256': output_hash,
                        'kind': 'insert' if previous is None else ('unchanged' if previous[:3] == (source_hash, output_hash, signature()) else 'update')})
    keys = {row['output']['observation_key'] for row in desired}
    deleted = [oid for oid, row in stored.items() if oid not in keys and (source is None or row[3] == source) and (crawl_run_id is None or row[4] == crawl_run_id)]
    return desired, deleted


def write_observation(conn, item):
    job, output = item['job'], item['output']
    oid = output['observation_key']
    fields = dict(observation_key=oid, **output['scalar'], source_normalized_at=job['normalized_at'],
                  source_normalization_version=job['normalization_version'], source_sha256=item['source_sha256'],
                  output_sha256=item['output_sha256'], model_sha256=signature())
    query = sql.SQL('INSERT INTO analytics.job_observations ({}) VALUES ({}) ON CONFLICT(observation_key) DO UPDATE SET {},loaded_at=CURRENT_TIMESTAMP').format(
        sql.SQL(',').join(map(sql.Identifier, FACT_FIELDS)), sql.SQL(',').join(sql.Placeholder() for _ in FACT_FIELDS),
        sql.SQL(',').join(sql.SQL('{}=excluded.{}').format(sql.Identifier(f), sql.Identifier(f)) for f in FACT_FIELDS if f != 'observation_key'))
    conn.execute(query, tuple(fields[f] for f in FACT_FIELDS))
    for group in a.GROUPS:
        conn.execute(sql.SQL('DELETE FROM analytics.{} WHERE observation_key=%s').format(sql.Identifier('bridge_job_'+group)), (oid,))
        for value in output['groups'][group]:
            conn.execute(sql.SQL('INSERT INTO analytics.{} VALUES(%s,%s,%s) ON CONFLICT({}) DO NOTHING').format(sql.Identifier('dim_'+group), sql.Identifier(group+'_key')),
                         (value['value_key'], value['name'], value['category']))
            conn.execute(sql.SQL('INSERT INTO analytics.{} VALUES(%s,%s,%s,%s)').format(sql.Identifier('bridge_job_'+group)),
                         (oid, value['value_key'], value['context_key'], value['level_text']))
    conn.execute('DELETE FROM analytics.observation_issues WHERE observation_key=%s', (oid,))
    for issue in output['issues']:
        conn.execute('INSERT INTO analytics.observation_issues VALUES(%s,%s,%s,%s,%s)',
                     (oid, issue['issue_key'], issue['field_name'], issue['source_value'], issue['reason']))


def apply(conn, crawl_run_id=None, source=None, fail_after_write=False, out=None):
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('normalize_atomic_v1',0))").fetchone()[0]:
            raise ValueError('Another atomic analytics load is running')
        before = snapshot(conn)
        if out:
            write_json(out/'before.json', before)
        items, deleted = plan(conn, crawl_run_id, source)
        configured = configure(conn)
        counts = {kind: sum(i['kind'] == kind for i in items) for kind in ('insert', 'update', 'unchanged')}
        for oid in deleted:
            conn.execute('DELETE FROM analytics.job_observations WHERE observation_key=%s', (oid,))
        for item in items:
            if item['kind'] != 'unchanged':
                write_observation(conn, item)
                if fail_after_write:
                    raise RuntimeError('injected_atomicity_failure')
            if a.digest(read_output(conn, item['output']['observation_key'])) != item['output_sha256']:
                raise ValueError('Atomic output read-back mismatch')
        after = snapshot(conn)
        if before['source_sha256'] != after['source_sha256']:
            raise ValueError('normalize_data changed during load')
        metrics = {'input_rows': len(items), 'inserted': counts['insert'], 'updated': counts['update'],
                   'unchanged': counts['unchanged'], 'deleted': len(deleted), 'configured': configured,
                   'upstream_writes': 0, 'observations': conn.execute('SELECT count(*) FROM analytics.job_observations').fetchone()[0],
                   'latest_jobs': conn.execute('SELECT count(*) FROM analytics.fact_job_latest').fetchone()[0],
                   'review_items': conn.execute('SELECT count(*) FROM analytics.observation_issues').fetchone()[0]}
        if out:
            write_json(out/'after.json', after)
            write_json(out/'metrics.json', metrics)
        return metrics, before, after


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(a.dumps(value)+'\n', encoding='utf-8')


def rollback(conn, before, after):
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        if not conn.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('normalize_atomic_v1',0))").fetchone()[0]:
            raise ValueError('Another analytics operation is running')
        if a.digest(snapshot(conn)) != a.digest(after):
            raise ValueError('Rollback precondition failed: later changes retained')
        for name in reversed(TABLES):
            conn.execute(sql.SQL('DELETE FROM analytics.{}').format(sql.Identifier(name)))
        if before['tables']:
            for name in TABLES:
                for row in before['tables'][name]:
                    cols = list(row)
                    conn.execute(sql.SQL('INSERT INTO analytics.{} ({}) VALUES ({})').format(sql.Identifier(name),
                        sql.SQL(',').join(map(sql.Identifier, cols)), sql.SQL(',').join(sql.Placeholder() for _ in cols)), list(row.values()))
        else:
            for name in reversed(VIEWS):
                conn.execute(sql.SQL('DROP VIEW analytics.{}').format(sql.Identifier(name)))
            for name in reversed(TABLES):
                conn.execute(sql.SQL('DROP TABLE analytics.{}').format(sql.Identifier(name)))
            if not before['analytics_exists']:
                conn.execute('DROP SCHEMA analytics')
        if a.digest(snapshot(conn)) != a.digest(before):
            raise ValueError('Rollback read-back mismatch')
