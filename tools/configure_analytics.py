"""Contract 1.2 migration and BI-serving layer. No raw replay; explicit writes only.

DATABASE_URL or PG* supply the connection. Default: read-only check.
"""
from __future__ import annotations
import argparse,hashlib,json,re,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tools import clean_staging as c
from data_preprocessing import canonicalization as k
from psycopg import sql

REMOVED_TABLES=('job_skills','job_education_requirements','job_foreign_languages')
MODEL=ROOT/'data_preprocessing/analytics.sql'
CONTRACT=ROOT/'data_preprocessing/staging_contract_1_2.sql'
MANAGED_VIEWS=set(re.findall(r'VIEW analytics\.(\w+) AS',MODEL.read_text(encoding='utf-8')))

def views(conn,schema):
    defs={n:d for n,d in conn.execute('SELECT viewname,definition FROM pg_views WHERE schemaname=%s ORDER BY viewname',(schema,))}
    deps={n:set() for n in defs}
    for n,parent in conn.execute('''SELECT DISTINCT v.relname,p.relname FROM pg_rewrite r
      JOIN pg_class v ON v.oid=r.ev_class JOIN pg_namespace vn ON vn.oid=v.relnamespace
      JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
      JOIN pg_class p ON p.oid=d.refobjid JOIN pg_namespace pn ON pn.oid=p.relnamespace
      WHERE vn.nspname=%s AND pn.nspname=%s AND p.relkind='v' AND v.oid<>p.oid''',(schema,schema)):
        deps[n].add(parent)
    order=[]
    while deps:
        ready=sorted(n for n,d in deps.items() if not d.intersection(deps))
        if not ready:raise ValueError('View dependency cycle')
        order+=ready
        for n in ready:del deps[n]
    return {'definitions':defs,'order':order}

def layer(conn,schema):
    if not conn.execute('SELECT to_regnamespace(%s)',(schema,)).fetchone()[0]:return {'exists':False}
    names=[r[0] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename',(schema,))]
    tables={n:[r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {}.{} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(schema),sql.Identifier(n)))] for n in names}
    functions=[r[0] for r in conn.execute('''SELECT pg_get_functiondef(p.oid) FROM pg_proc p
      JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s ORDER BY proname''',(schema,))]
    return {'exists':True,'tables':tables,'views':views(conn,schema),'functions':functions}

def state(conn):
    checks=[r for r in conn.execute("SELECT t.relname,c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='staging' AND pg_get_constraintdef(c.oid) LIKE '%requirement_type%' ORDER BY t.relname,c.conname")]
    cols=[r for r in conn.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='staging' AND column_name='requirement_type' ORDER BY table_name")]
    return {'core':c.snapshot(conn),'bi':layer(conn,'bi'),'analytics':layer(conn,'analytics'),'staging_views':views(conn,'staging'),'removed_columns':cols,'removed_checks':checks}

def drop_views(conn,schema):
    found=views(conn,schema)
    unknown=set(found['definitions'])-MANAGED_VIEWS
    if unknown:raise ValueError('Unmanaged views retained; migration stopped: '+','.join(sorted(unknown)))
    for name in reversed(found['order']):
        # No CASCADE: external consumers must be reviewed rather than silently removed.
        conn.execute(sql.SQL('DROP VIEW {}.{}').format(sql.Identifier(schema),sql.Identifier(name)))

def status(conn):
    return {'bi_exists':bool(conn.execute("SELECT to_regnamespace('bi')").fetchone()[0]),
            'analytics_exists':bool(conn.execute("SELECT to_regnamespace('analytics')").fetchone()[0]),
            'requirement_columns':conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema IN ('staging','analytics') AND column_name='requirement_type'").fetchone()[0],
            'observations':conn.execute('SELECT count(*) FROM staging.job_observations').fetchone()[0]}

def validate(conn):
    s=status(conn);assert not s['bi_exists'] and s['analytics_exists'] and s['requirement_columns']==0
    for record in c.jobs(conn):
        normalizer=c.iv if record['source']=='itviec' else c.td
        normalizer.validate_normalized_job({f:record[f] for f in normalizer.NORMALIZED_FIELDS})
    assert conn.execute('SELECT count(*) FROM analytics.fact_job_latest').fetchone()[0]==conn.execute('SELECT count(DISTINCT job_id) FROM staging.dwh_ready_jobs').fetchone()[0]
    assert conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='analytics' AND table_name IN ('fact_job_latest','dim_skills','bridge_job_skills_latest') AND data_type IN ('ARRAY','json','jsonb')").fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM analytics.bridge_job_skills_latest b LEFT JOIN analytics.dim_skills d USING(skill_key) WHERE d.skill_key IS NULL').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM (SELECT observation_id,skill_key,count(*) FROM analytics.bridge_job_skills_latest GROUP BY 1,2 HAVING count(*)>1)t').fetchone()[0]==0
    assert conn.execute('SELECT count(*) FROM analytics.role_skill_demand_latest WHERE job_pct<0 OR job_pct>100').fetchone()[0]==0

def signatures():return hashlib.sha256(MODEL.read_bytes()+CONTRACT.read_bytes()+k.CATALOG_PATH.read_bytes()).hexdigest()

def apply(conn,fail_after_write=False):
    with conn.transaction():
        c.lock(conn);before=state(conn)
        if before['bi']['exists'] and before['analytics']['exists']:raise ValueError('Both bi and analytics exist; no automatic merge')
        current=conn.execute("SELECT to_regclass('analytics.pipeline_state')").fetchone()[0]
        if current and conn.execute('SELECT model_sha256 FROM analytics.pipeline_state WHERE singleton').fetchone()==(signatures(),):
            validate(conn)
            return {'changed':False,'raw_unchanged':True,'staging_child_writes':0},before,before
        ready=conn.execute('SELECT count(*) FROM staging.dwh_ready_jobs').fetchone()[0]
        eligible=[r[0] for r in conn.execute('SELECT o.observation_id FROM staging.job_observations o JOIN staging.dwh_ready_jobs j USING(job_id,crawl_run_id)')]
        if before['bi']['exists']:conn.execute('ALTER SCHEMA bi RENAME TO analytics')
        conn.execute('CREATE SCHEMA IF NOT EXISTS analytics')
        drop_views(conn,'analytics')
        conn.execute(CONTRACT.read_text(encoding='utf-8'),prepare=False)
        for table in REMOVED_TABLES:
            conn.execute(sql.SQL('ALTER TABLE staging.{} DROP COLUMN IF EXISTS requirement_type').format(sql.Identifier(table)))
        if fail_after_write:raise RuntimeError('injected_atomicity_failure')
        # Only migrate previously validated 1.1 records, and revalidate all records before commit.
        conn.execute("UPDATE staging.job_observations SET normalization_version='1.2' WHERE normalization_version='1.1' AND observation_id=ANY(%s)",(eligible,))
        conn.execute("UPDATE raw.crawl_runs SET metadata=jsonb_set(metadata,'{staging,version}','\"1.2\"'::jsonb) WHERE metadata->'staging'->>'version'='1.1' AND metadata->'staging'->>'status'='succeeded' AND metadata->'staging'->>'ready_for_dwh'='true' AND metadata->'staging'->'quality'->>'error_count'='0'")
        assert conn.execute('SELECT count(*) FROM staging.dwh_ready_jobs').fetchone()[0]==ready
        conn.execute(MODEL.read_text(encoding='utf-8'),prepare=False)
        conn.execute('DELETE FROM analytics.value_aliases');conn.execute('DELETE FROM analytics.canonical_values')
        doc,maps=k.catalog()
        with conn.cursor() as cur:
            cur.executemany('INSERT INTO analytics.canonical_values VALUES(%s,%s,%s,%s)',[(d,d+':'+k.label_key(r['name']),r['name'],r['category']) for d,rs in doc['domains'].items() for r in rs])
            cur.executemany('INSERT INTO analytics.value_aliases VALUES(%s,%s,%s)',[(d,a,d+':'+k.label_key(r['name'])) for d,m in maps.items() for a,r in m.items()])
        conn.execute('INSERT INTO analytics.standardization_metadata VALUES(%s,%s) ON CONFLICT(version) DO UPDATE SET catalog_sha256=excluded.catalog_sha256',(doc['version'],hashlib.sha256(k.CATALOG_PATH.read_bytes()).hexdigest()))
        conn.execute('INSERT INTO analytics.pipeline_state VALUES(true,%s) ON CONFLICT(singleton) DO UPDATE SET model_sha256=excluded.model_sha256',(signatures(),))
        validate(conn);after=state(conn)
        assert before['core']['raw.job_postings']==after['core']['raw.job_postings']
        for table in REMOVED_TABLES:
            removed=[{key:value for key,value in row.items() if key!='requirement_type'} for row in before['core']['staging.'+table]]
            assert sorted(map(c.dumps,removed))==sorted(map(c.dumps,after['core']['staging.'+table]))
        return {'changed':True,'raw_unchanged':True,'staging_child_writes':0,'observations':status(conn)['observations']},before,after

def restore_views(conn,schema,saved):
    for n in saved['order']:
        conn.execute(sql.SQL('CREATE OR REPLACE VIEW {}.{} AS ').format(sql.Identifier(schema),sql.Identifier(n))+sql.SQL(saved['definitions'][n]))

def rollback(conn,before,after):
    with conn.transaction():
        c.lock(conn)
        if c.digest(state(conn))!=c.digest(after):raise ValueError('Rollback precondition failed: subsequent changes retained')
        drop_views(conn,'analytics')
        for table,col,typ,nullable in before['removed_columns']:
            conn.execute(sql.SQL('ALTER TABLE staging.{} ADD COLUMN {} TEXT').format(sql.Identifier(table),sql.Identifier(col)))
            for row in before['core']['staging.'+table]:
                conn.execute(sql.SQL('UPDATE staging.{} SET requirement_type=%s WHERE observation_id=%s AND item_order=%s').format(sql.Identifier(table)),(row['requirement_type'],row['observation_id'],row['item_order']))
            if nullable=='NO':conn.execute(sql.SQL('ALTER TABLE staging.{} ALTER COLUMN requirement_type SET NOT NULL').format(sql.Identifier(table)))
        for table,name,definition in before['removed_checks']:
            conn.execute(sql.SQL('ALTER TABLE staging.{} ADD CONSTRAINT {} ').format(sql.Identifier(table),sql.Identifier(name))+sql.SQL(definition))
        from psycopg.types.json import Jsonb
        for row in before['core']['staging.job_observations']:
            conn.execute('UPDATE staging.job_observations SET normalization_version=%s WHERE observation_id=%s',(row['normalization_version'],row['observation_id']))
        for row in before['core']['raw.crawl_runs']:
            conn.execute('UPDATE raw.crawl_runs SET metadata=%s WHERE crawl_run_id=%s',(Jsonb(row['metadata']),row['crawl_run_id']))
        restore_views(conn,'staging',before['staging_views'])
        original='bi' if before['bi']['exists'] else 'analytics'
        saved=before[original]
        if saved['exists']:
            if original=='bi':conn.execute('ALTER SCHEMA analytics RENAME TO bi')
            now=layer(conn,original)
            for table in reversed(list(now['tables'])):
                conn.execute(sql.SQL('DELETE FROM {}.{}').format(sql.Identifier(original),sql.Identifier(table)))
            for table in set(now['tables'])-set(saved['tables']):
                conn.execute(sql.SQL('DROP TABLE {}.{}').format(sql.Identifier(original),sql.Identifier(table)))
            for table in ('canonical_values','value_aliases','standardization_metadata','pipeline_state'):
                for row in saved['tables'].get(table,[]):
                    cols=list(row)
                    conn.execute(sql.SQL('INSERT INTO {}.{} ({}) VALUES ({})').format(sql.Identifier(original),sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols)),list(row.values()))
            for definition in saved['functions']:conn.execute(definition)
            restore_views(conn,original,saved['views'])
        else:
            for table in ('value_aliases','canonical_values','standardization_metadata','pipeline_state'):
                conn.execute(sql.SQL('DROP TABLE analytics.{}').format(sql.Identifier(table)))
            conn.execute('DROP FUNCTION analytics.label_key(text)');conn.execute('DROP SCHEMA analytics')
        assert c.digest(state(conn))==c.digest(before),'Full-state rollback mismatch'

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');p.add_argument('--rollback',type=Path);p.add_argument('--out',type=Path,default=ROOT/'reports/analytics-20261030/check')
    a=p.parse_args(argv)
    if a.apply and a.rollback:p.error('Choose apply or rollback')
    with c.connect() as conn:
        if a.rollback:
            rollback(conn,json.loads((a.rollback/'before.snapshot.json').read_text(encoding='utf-8')),json.loads((a.rollback/'after.snapshot.json').read_text(encoding='utf-8')))
            print('ROLLBACK_OK original_schema_and_requirement_labels_restored=true');return 0
        if a.apply:
            if (a.out/'before.snapshot.json').exists():p.error('Use a new output directory')
            with conn.transaction():
                c.lock(conn);c.write(a.out/'before.snapshot.json',state(conn));metrics,before,after=apply(conn)
                c.write(a.out/'after.snapshot.json',after);c.write(a.out/'metrics.json',metrics)
            print('APPLY_OK schema=analytics requirement_columns=0 changed='+str(metrics['changed']).lower()+' staging_child_writes=0 raw_unchanged=true')
        else:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY');result=status(conn)
            c.write(a.out/'status.json',result)
            print('CHECK_OK '+json.dumps(result,sort_keys=True)+' writes=0')
    return 0
if __name__=='__main__':raise SystemExit(main())
