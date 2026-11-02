"""Atomic analytics integration on UUID databases; never changes the project DB."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import uuid

from data_preprocessing.analytics import storage as s, atomic as a
from data_preprocessing.normalization.contracts import PERSISTED_NORMALIZED_FIELDS
from test_analytics_atomic import sample


@unittest.skipUnless(os.environ.get('ITVIEC_TEST_DSN'),'Set ITVIEC_TEST_DSN for PostgreSQL integration')
class AnalyticsPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        self.sql=sql
        self.options=conninfo_to_dict(os.environ['ITVIEC_TEST_DSN'])
        self.admin=psycopg.connect(**self.options,autocommit=True)
        self.db='atomic_test_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.db)))
        self.conn=psycopg.connect(**{**self.options,'dbname':self.db},autocommit=True)
        self.role=None
        root=Path(__file__).resolve().parents[1]
        for name in ['raw_schema.sql','normalize_data_schema.sql']:
            self.conn.execute((root/'data_preprocessing/sql'/name).read_text(encoding='utf-8'),prepare=False)

    def tearDown(self):
        self.conn.close()
        self.admin.execute(self.sql.SQL('DROP DATABASE {}').format(self.sql.Identifier(self.db)))
        if self.role:
            self.admin.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(self.role)))
        self.admin.close()

    def insert(self,**kwargs):
        from psycopg.types.json import Jsonb
        job=sample(**kwargs)
        self.conn.execute("INSERT INTO raw.crawl_runs(crawl_run_id,source,started_at,requested_count,collected_count,status) VALUES(%s,%s,%s,1,1,'succeeded') ON CONFLICT DO NOTHING",(job['crawl_run_id'],job['source'],job['observed_at']))
        self.conn.execute('INSERT INTO raw.job_postings(job_id,crawl_run_id,source_job_id,job_url,observed_at,raw_payload) VALUES(%s,%s,%s,%s,%s,%s)',(job['job_id'],job['crawl_run_id'],job['source_job_id'],job['job_url'],job['observed_at'],Jsonb({'fixture':True})))
        fields=PERSISTED_NORMALIZED_FIELDS+('normalization_version','normalized_at')
        values=[Jsonb(job[f]) if isinstance(job[f],(list,dict)) else job[f] for f in fields]
        q=self.sql.SQL('INSERT INTO normalize_data.normalized_jobs ({}) VALUES ({})').format(self.sql.SQL(',').join(map(self.sql.Identifier,fields)),self.sql.SQL(',').join(self.sql.Placeholder() for _ in fields))
        self.conn.execute(q,values)
        return job

    def all_xmins(self):
        return {t:self.conn.execute(self.sql.SQL('SELECT xmin::text,to_jsonb(t)::text FROM analytics.{} t ORDER BY to_jsonb(t)::text').format(self.sql.Identifier(t))).fetchall() for t in s.TABLES}

    def test_first_load_and_same_input_repeat_have_no_writes(self):
        self.insert()
        with patch('data_preprocessing.cli.load_normalize_data.normalize_raw_record_for_storage',side_effect=AssertionError('Do not process raw')):
            metrics,_,_=s.apply(self.conn)
        self.assertEqual(metrics['inserted'],1)
        before=self.all_xmins();upstream=s.source_fingerprint(self.conn)
        metrics,_,_=s.apply(self.conn)
        self.assertEqual(metrics['unchanged'],1);self.assertEqual(metrics['updated'],0)
        self.assertEqual(self.all_xmins(),before);self.assertEqual(s.source_fingerprint(self.conn),upstream)

    def test_8_groups_are_physical_scalar_tables_and_no_benefit(self):
        self.insert();s.apply(self.conn)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='analytics'").fetchone()[0],19)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='analytics' AND data_type IN ('json','jsonb','ARRAY')").fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='analytics' AND tablename LIKE '%benefit%'").fetchone()[0],0)

    def test_new_history_keeps_old_observation_and_scopes(self):
        self.insert();s.apply(self.conn)
        old=self.conn.execute('SELECT observation_key,xmin::text FROM analytics.job_observations').fetchone()
        self.insert(run='run2')
        metrics,_,_=s.apply(self.conn,crawl_run_id='run2')
        self.assertEqual(metrics['inserted'],1);self.assertEqual(metrics['observations'],2)
        self.assertEqual(self.conn.execute('SELECT xmin::text FROM analytics.job_observations WHERE observation_key=%s',(old[0],)).fetchone()[0],old[1])

    def test_update_replaces_only_changed_observation_bridges(self):
        self.insert();self.insert(sid='2');s.apply(self.conn)
        old=self.conn.execute("SELECT xmin::text FROM analytics.job_observations WHERE job_id='topdev:2'").fetchone()[0]
        self.conn.execute("UPDATE normalize_data.normalized_jobs SET skills='[{\"name\":\"Java\",\"category\":\"programming_language\"}]'::jsonb WHERE job_id='topdev:1'")
        metrics,_,_=s.apply(self.conn)
        self.assertEqual(metrics['updated'],1)
        self.assertEqual(self.conn.execute("SELECT xmin::text FROM analytics.job_observations WHERE job_id='topdev:2'").fetchone()[0],old)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM analytics.bridge_job_framework b JOIN analytics.job_observations j USING(observation_key) WHERE j.job_id='topdev:1'").fetchone()[0],0)

    def test_source_retraction_only_removes_its_selected_scope(self):
        self.insert();self.insert(sid='2',run='run2');s.apply(self.conn)
        self.conn.execute("DELETE FROM normalize_data.normalized_jobs WHERE crawl_run_id='run1'")
        metrics,_,_=s.apply(self.conn,crawl_run_id='run2')
        self.assertEqual(metrics['deleted'],0)
        metrics,_,_=s.apply(self.conn,crawl_run_id='run1')
        self.assertEqual(metrics['deleted'],1)
        self.assertEqual(self.conn.execute('SELECT crawl_run_id FROM analytics.job_observations').fetchone()[0],'run2')

    def test_failure_rolls_back_ddl_and_rows(self):
        self.insert();before=a.digest(s.snapshot(self.conn))
        with self.assertRaisesRegex(RuntimeError,'injected_atomicity_failure'):
            s.apply(self.conn,fail_after_write=True)
        self.assertEqual(a.digest(s.snapshot(self.conn)),before)

    def test_invalid_input_rejects_whole_transaction(self):
        self.insert();self.insert(sid='2')
        self.conn.execute("UPDATE normalize_data.normalized_jobs SET skills='[\"Python\"]'::jsonb WHERE job_id='topdev:2'")
        before=a.digest(s.snapshot(self.conn))
        with self.assertRaises(ValueError):s.apply(self.conn)
        self.assertEqual(a.digest(s.snapshot(self.conn)),before)

    def test_only_select_on_normalize_is_sufficient_no_raw_access(self):
        self.insert();s.apply(self.conn);self.insert(sid='2')
        self.role='atomic_role_'+uuid.uuid4().hex;q=self.sql.Identifier(self.role)
        self.conn.execute(self.sql.SQL('CREATE ROLE {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT USAGE ON SCHEMA normalize_data,analytics TO {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT SELECT ON normalize_data.normalized_jobs TO {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA analytics TO {}').format(q))
        self.conn.execute(self.sql.SQL('SET ROLE {}').format(q))
        try:
            self.assertFalse(self.conn.execute("SELECT has_schema_privilege(current_user,'raw','USAGE')").fetchone()[0])
            metrics,_,_=s.apply(self.conn)
            self.assertEqual(metrics['inserted'],1)
        finally:self.conn.execute('RESET ROLE')

    def test_rollback_absent_then_existing_layer(self):
        self.insert();_,before,after=s.apply(self.conn)
        s.rollback(self.conn,before,after);self.assertEqual(a.digest(s.snapshot(self.conn)),a.digest(before))
        s.apply(self.conn);self.insert(sid='2')
        _,before,after=s.apply(self.conn)
        s.rollback(self.conn,before,after);self.assertEqual(a.digest(s.snapshot(self.conn)),a.digest(before))

    def test_tampering_and_stale_rollback_are_rejected(self):
        self.insert();_,before,after=s.apply(self.conn)
        self.conn.execute("UPDATE analytics.job_observations SET title='Manual edit'")
        with self.assertRaisesRegex(ValueError,'Rollback precondition'):s.rollback(self.conn,before,after)
        with self.assertRaisesRegex(ValueError,'manually changed'):s.apply(self.conn)

    def test_no_skill_jobs_are_in_role_percentage_denominator(self):
        for sid in ['1','2']:self.insert(sid=sid,skills=[{'name':'Java','category':'programming_language'}])
        self.insert(sid='3',skills=[]);s.apply(self.conn)
        row=self.conn.execute("SELECT jobs_with_skill,total_role_jobs,job_pct FROM analytics.role_skill_demand_latest WHERE skill_name='Java'").fetchone()
        self.assertEqual(row[:2],(2,3));self.assertEqual(float(row[2]),66.67)

    def test_existing_legacy_analytics_is_retained(self):
        self.insert();self.conn.execute('CREATE SCHEMA analytics');self.conn.execute('CREATE TABLE analytics.legacy(id integer)')
        with self.assertRaisesRegex(ValueError,'Existing analytics model differs'):s.apply(self.conn)
        self.assertIsNotNone(self.conn.execute("SELECT to_regclass('analytics.legacy')").fetchone()[0])

    def test_analytics_reports_survive_without_source_rows(self):
        self.insert();s.apply(self.conn)
        self.conn.execute('DELETE FROM normalize_data.normalized_jobs')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM analytics.fact_job_latest').fetchone()[0],1)
        # Explicit sync, not the deletion itself, retracts the published row.
        self.assertEqual(s.apply(self.conn)[0]['deleted'],1)


if __name__=='__main__':
    unittest.main()
