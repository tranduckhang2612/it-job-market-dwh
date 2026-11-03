"""Pure contract tests: no upstream processing and no benefit extraction."""
import copy
from datetime import datetime, timezone
import unittest

from data_preprocessing.analytics import atomic as a


def sample(sid='1', source='topdev', run='run1', **extra):
    now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    return {'job_id':source+':'+sid, 'source':source, 'source_job_id':sid,
            'job_url':'https://'+source+'.com/jobs/'+sid, 'crawl_run_id':run,
            'observed_at':now, 'normalized_at':now, 'normalization_version':'5.1',
            'title':'Backend Developer', 'company_name':'Fixture', 'description':'Build services.',
            'job_roles':['Back-end Developer'], 'seniority_levels':['Junior','Mid-level'],
            'location_text':None, 'cities':['HCMC','Hồ Chí Minh'], 'work_mode':'hybrid',
            'skills':[{'name':'cpp','category':'other'},{'name':'Cplus','category':'other'},
                      {'name':'ReactJS','category':'other'},{'name':'Docker','category':'devops'}],
            'foreign_languages':[{'language':'English','level_text':'TOEIC 650'}],
            'soft_skills':['Teamwork'], 'normalization_meta':{'fields':{}}, **extra}


class AtomicTests(unittest.TestCase):
    def test_copy_dedup_and_separate_groups(self):
        job=sample();before=copy.deepcopy(job);out=a.atomize(job)
        self.assertEqual(job,before)
        self.assertEqual([v['name'] for v in out['groups']['programming_language']],['C++'])
        self.assertEqual([v['name'] for v in out['groups']['framework']],['React'])
        self.assertEqual([v['name'] for v in out['groups']['core_skill']],['Docker'])
        self.assertEqual(len(out['groups']['city']),1)

    def test_known_composites_split_but_named_concepts_do_not(self):
        out=a.atomize(sample(skills=[{'name':n,'category':'other'} for n in ['Java/React','C/C++','CI/CD','UI/UX']]))
        self.assertEqual({v['name'] for v in out['groups']['programming_language']},{'Java','C','C++'})
        self.assertEqual({v['name'] for v in out['groups']['core_skill']},{'CI/CD','UI/UX'})
        self.assertEqual([v['name'] for v in out['groups']['framework']],['React'])

    def test_unknown_composite_goes_to_review_without_fake_atom(self):
        out=a.atomize(sample(skills=[{'name':'InternalFoo/Bar','category':'other'}]))
        self.assertEqual(out['groups']['core_skill'],[])
        self.assertEqual(out['issues'][0]['reason'],'unresolved_composite')
        self.assertEqual(out['issues'][0]['source_value'],'InternalFoo/Bar')

    def test_languages_are_not_programming_languages(self):
        out=a.atomize(sample(skills=[{'name':'English','category':'other'},{'name':'Python','category':'programming_language'}]))
        self.assertEqual([v['name'] for v in out['groups']['programming_language']],['Python'])
        self.assertEqual({v['name'] for v in out['groups']['foreign_language']},{'English'})
        self.assertEqual({v['level_text'] for v in out['groups']['foreign_language']},{None,'TOEIC 650'})

    def test_role_and_business_domains_are_not_core_skills(self):
        out=a.atomize(sample(skills=[{'name':n,'category':'other'} for n in ['Business Analyst','Banking & Finance','E-commerce']]))
        self.assertEqual(out['groups']['core_skill'],[])
        self.assertIn('Business Analyst',[v['name'] for v in out['groups']['role']])
        self.assertEqual(sum(i['reason']=='business_domain_not_a_skill' for i in out['issues']),2)

    def test_unknown_language_does_not_become_a_real_language(self):
        out=a.atomize(sample(foreign_languages=[{'language':'unknown','level_text':None}]))
        self.assertEqual(out['groups']['foreign_language'],[])
        self.assertEqual(out['scalar']['foreign_language_status'],'unknown')

    def test_certificate_text_is_preserved_not_converted_to_cefr(self):
        out=a.atomize(sample(foreign_languages=[{'language':'Japanese','level_text':'JLPT N2'}]))
        self.assertEqual(out['groups']['foreign_language'][0]['level_text'],'JLPT N2')
        self.assertEqual([v['name'] for v in out['groups']['level']],sorted(['Junior','Mid-level'],key=lambda n:a.digest(['level',n.casefold(),''])))

    def test_history_and_source_have_distinct_keys(self):
        self.assertNotEqual(a.atomize(sample())['observation_key'],a.atomize(sample(source='itviec'))['observation_key'])
        self.assertNotEqual(a.atomize(sample())['observation_key'],a.atomize(sample(run='run2'))['observation_key'])

    def test_observation_and_catalog_keys_are_repeatable(self):
        self.assertEqual(a.atomize(sample()),a.atomize(sample()))

    def test_bad_source_version_shape_and_time_are_rejected(self):
        for change in [{'normalization_version':'1.2'},{'job_roles':[]},{'skills':['Java']},
                       {'observed_at':datetime(2026,11,2)},{'foreign_languages':[]}]:
            with self.subTest(change=change),self.assertRaises((ValueError,TypeError)):
                a.atomize(sample(**change))

    def test_no_benefit_or_requirement_type_is_published(self):
        out=a.atomize(sample())
        self.assertNotIn('benefit',out['groups'])
        self.assertNotIn('requirement_type',a.dumps(out))

    def test_all_catalog_aliases_are_unambiguous(self):
        maps=a.catalog()
        self.assertEqual(maps['programming_language'][a.label_key('Cplus')]['name'],'C++')
        self.assertNotEqual(maps['programming_language']['java']['name'],maps['programming_language']['javascript']['name'])


if __name__=='__main__':
    unittest.main()
