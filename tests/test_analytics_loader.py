"""Step boundary and isolated PostgreSQL publication regression tests."""
import copy
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

from data_preprocessing.topdev_normalize_data import normalize_topdev as td
from data_preprocessing.analytics_transform import transform_record
from tools import analytics_loader as loader
from tools import analytics_state as state
from tools import clean_staging as c


def raw(sid='a', skills=None, at='2026-11-01T10:00:00+07:00'):
    return {'id':sid,'title':'Backend Developer','description':'Build reliable services.',
            'detail_url':'https://topdev.vn/detail-jobs/backend-'+sid,'observed_at':at,
            'skills_arr':skills if skills is not None else ['cpp','Cplus','C/C++','English']}


class StepBoundaryTests(unittest.TestCase):
    def test_step_three_accepts_existing_staging_without_mutation(self):
        staging = td.normalize_job(raw(),'fixture')
        # Existing staging input can retain aliases; no upstream code is changed.
        staging['skills'] = [{'name':n,'category':'technical'} for n in ['cpp','Cplus','C/C++','English']]
        staging['foreign_languages'] = []
        original = copy.deepcopy(staging)
        output = transform_record(staging)
        self.assertEqual(staging,original)
        self.assertIn('cpp',[v['name'] for v in staging['skills']])
        self.assertEqual([v['name'] for v in output['skills']],['C++','C'])
        self.assertEqual([v['language'] for v in output['foreign_languages']],['English'])
        self.assertEqual(transform_record(output),output)

    def test_serving_sql_has_no_live_staging_dependency(self):
        root=Path(__file__).resolve().parents[1]/'data_preprocessing'
        for name in ('analytics.sql','analytics_contract.sql','analytics_storage.sql'):
            text=(root/name).read_text(encoding='utf-8')
            self.assertNotIn('staging.',text)
            self.assertNotIn('REFERENCES raw.',text)


@unittest.skipUnless(os.environ.get('ITVIEC_TEST_DSN'),'Set ITVIEC_TEST_DSN for isolated PostgreSQL integration')
class AnalyticsPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        self.sql=sql
        self.options=conninfo_to_dict(os.environ['ITVIEC_TEST_DSN'])
        self.admin=psycopg.connect(**self.options,autocommit=True)
        self.db='analytics_test_'+uuid.uuid4().hex
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(self.db)))
        self.conn=psycopg.connect(**{**self.options,'dbname':self.db},autocommit=True)
        self.roles=[]
        self.args=SimpleNamespace(db_dsn=make_conninfo(**{**self.options,'dbname':self.db}),db_host=None,
            db_port=None,db_name=None,db_user=None,db_password_prompt=False,db_connect_timeout=5)
        root=Path(__file__).resolve().parents[1]
        self.conn.execute((root/'data_preprocessing/topdev_db/topdev_schema.sql').read_text(encoding='utf-8'),prepare=False)

    def tearDown(self):
        self.conn.close()
        self.admin.execute(self.sql.SQL('DROP DATABASE {}').format(self.sql.Identifier(self.db)))
        for role in self.roles:
            self.admin.execute(self.sql.SQL('DROP ROLE {}').format(self.sql.Identifier(role)))
        self.admin.close()

    def seed(self,run='run1',ids=('a',),skills=None,at='2026-11-01T10:00:00+07:00'):
        from psycopg.types.json import Jsonb
        self.conn.execute("INSERT INTO raw.crawl_runs(crawl_run_id,source,started_at,requested_count,collected_count,status) VALUES(%s,'topdev',%s,%s,%s,'succeeded')",(run,at,len(ids),len(ids)))
        for sid in ids:
            payload=raw(sid,skills,at)
            jid,source_id,url=td.normalized_job_identity(payload)
            self.conn.execute('INSERT INTO raw.job_postings(job_id,crawl_run_id,source_job_id,job_url,observed_at,raw_payload) VALUES(%s,%s,%s,%s,%s,%s)',(jid,run,source_id,url,at,Jsonb(payload)))
        self.assertEqual(td.stage_database_run(run,self.args),0)

    def digest(self):
        return c.digest(state.snapshot(self.conn))

    def analytics_xmin(self):
        return self.conn.execute('SELECT observation_id,xmin::text,loaded_at FROM analytics.load_state ORDER BY 1').fetchall()

    def test_first_load_persists_real_tables_without_upstream_writes(self):
        self.seed()
        upstream=c.digest(state.upstream(self.conn))
        metrics,_,_=loader.apply(self.conn)
        self.assertEqual(metrics['written_observations'],1)
        self.assertEqual(c.digest(state.upstream(self.conn)),upstream)
        self.assertEqual(self.conn.execute("SELECT relkind FROM pg_class WHERE oid='analytics.job_observations'::regclass").fetchone()[0],'r')
        self.assertEqual(self.conn.execute('SELECT name FROM analytics.job_skills ORDER BY item_order').fetchall(),[('C++',),('C',)])
        self.assertEqual(self.conn.execute('SELECT name FROM staging.job_skills ORDER BY item_order').fetchall(),[('C++',),('C',)])

    def test_step_three_finishes_existing_staging_aliases_without_replaying_raw(self):
        self.seed(skills=['Python'])
        self.conn.execute("UPDATE staging.job_skills SET name='cpp',category='technical'")
        upstream=c.digest(state.upstream(self.conn))
        metrics,_,_=loader.apply(self.conn)
        self.assertEqual(metrics['written_observations'],1)
        self.assertEqual(self.conn.execute('SELECT name FROM analytics.job_skills').fetchall(),[('C++',)])
        self.assertEqual(self.conn.execute('SELECT name FROM staging.job_skills').fetchall(),[('cpp',)])
        self.assertEqual(c.digest(state.upstream(self.conn)),upstream)

    def test_same_batch_repeat_has_zero_writes(self):
        self.seed();loader.apply(self.conn)
        old=self.digest();xmins=self.analytics_xmin()
        metrics,_,_=loader.apply(self.conn)
        self.assertFalse(metrics['changed']);self.assertEqual(metrics['written_observations'],0)
        self.assertEqual(self.digest(),old);self.assertEqual(self.analytics_xmin(),xmins)

    def test_new_batch_keeps_old_observation_history(self):
        self.seed();loader.apply(self.conn);old=self.analytics_xmin()[0]
        self.seed('run2',skills=['Java'],at='2026-11-02T10:00:00+07:00')
        metrics,_,_=loader.apply(self.conn,'run2')
        self.assertEqual(metrics['written_observations'],1)
        self.assertEqual(metrics['analytics_observations'],2);self.assertEqual(metrics['latest_jobs'],1)
        self.assertEqual(self.analytics_xmin()[0],old)
        self.assertEqual(self.conn.execute('SELECT crawl_run_id FROM analytics.fact_job_latest').fetchone()[0],'run2')

    def test_changed_observation_updates_only_its_analytics_rows(self):
        self.seed(ids=('a','b'));loader.apply(self.conn)
        old=self.analytics_xmin()
        self.conn.execute("UPDATE staging.job_observations SET title='Backend Developer Updated' WHERE job_id='topdev:a'")
        upstream=c.digest(state.upstream(self.conn))
        metrics,_,_=loader.apply(self.conn)
        self.assertEqual(metrics['written_observations'],1)
        self.assertEqual(self.analytics_xmin()[1],old[1])
        self.assertEqual(c.digest(state.upstream(self.conn)),upstream)

    def test_failure_after_first_write_is_fully_atomic(self):
        self.seed(ids=('a','b'));old=self.digest()
        with self.assertRaisesRegex(RuntimeError,'injected_atomicity_failure'):
            loader.apply(self.conn,fail_after_write=True)
        self.assertEqual(self.digest(),old)

    def test_failed_batch_and_partial_lineage_are_not_published(self):
        self.seed(ids=('a','b'))
        self.conn.execute("UPDATE staging.job_observations SET job_url=job_url||'-mismatch' WHERE job_id='topdev:a'")
        old=self.digest()
        with self.assertRaisesRegex(ValueError,'Incomplete eligible batch'):
            loader.apply(self.conn)
        self.assertEqual(self.digest(),old)
        self.conn.execute("UPDATE raw.crawl_runs SET metadata=jsonb_set(metadata,'{staging,status}','\"failed\"'::jsonb) WHERE crawl_run_id='run1'")
        old=self.digest()
        with self.assertRaisesRegex(ValueError,'not fully eligible'):
            loader.apply(self.conn,'run1')
        self.assertEqual(self.digest(),old)

    def test_analytics_remains_readable_without_staging_rows(self):
        self.seed();loader.apply(self.conn)
        self.conn.execute('DELETE FROM staging.job_observations')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM analytics.fact_job_latest').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM analytics.job_skills').fetchone()[0],2)

    def test_publication_works_with_select_only_upstream_privileges(self):
        self.seed();loader.apply(self.conn)
        self.seed('run2',ids=('b',))
        role='analytics_role_'+uuid.uuid4().hex
        self.roles.append(role)
        q=self.sql.Identifier(role)
        self.conn.execute(self.sql.SQL('CREATE ROLE {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT USAGE ON SCHEMA raw,staging,analytics TO {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA raw,staging TO {}').format(q))
        self.conn.execute(self.sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA analytics TO {}').format(q))
        self.conn.execute(self.sql.SQL('SET ROLE {}').format(q))
        try:
            self.assertFalse(self.conn.execute("SELECT has_table_privilege(current_user,'staging.job_observations','UPDATE')").fetchone()[0])
            metrics,_,_=loader.apply(self.conn,'run2')
            self.assertEqual(metrics['written_observations'],1)
        finally:
            self.conn.execute('RESET ROLE')

    def test_rollback_restores_absent_or_persisted_layer(self):
        self.seed()
        _,before,after=loader.apply(self.conn)
        loader.rollback(self.conn,before,after)
        self.assertEqual(self.digest(),c.digest(before))
        loader.apply(self.conn)
        self.seed('run2',ids=('b',))
        _,before,after=loader.apply(self.conn)
        loader.rollback(self.conn,before,after)
        self.assertEqual(self.digest(),c.digest(before))

    def test_stale_rollback_and_tampered_output_are_rejected(self):
        self.seed();_,before,after=loader.apply(self.conn)
        self.conn.execute("UPDATE analytics.job_observations SET title='Manual change'")
        with self.assertRaisesRegex(ValueError,'Rollback precondition failed'):
            loader.rollback(self.conn,before,after)
        with self.assertRaisesRegex(ValueError,'outside loader'):
            loader.apply(self.conn)

    def test_role_percentage_keeps_jobs_without_skills_in_denominator(self):
        self.seed('run1',ids=('a','b'),skills=['Java'])
        self.seed('run2',ids=('c',),skills=[])
        loader.apply(self.conn)
        row=self.conn.execute("SELECT jobs_with_skill,total_role_jobs,job_pct FROM analytics.role_skill_demand_latest WHERE role_name='Backend Developer' AND skill_name='Java'").fetchone()
        self.assertEqual(row[:2],(2,3));self.assertEqual(float(row[2]),66.67)


if __name__=='__main__':
    unittest.main()
