import copy
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from data_preprocessing import canonicalization as k
from data_preprocessing import staging_cleaning as c
from data_preprocessing.topdev_normalize_data import normalize_topdev as td
from data_preprocessing.itviec_normalize_data import normalize_itviec as iv

def job(**extra):
    if 'skills' in extra:extra['skills_arr']=extra.pop('skills')
    raw={'id':99,'title':'Backend Developer','detail_url':'https://topdev.vn/detail-jobs/backend-99',
         'observed_at':'2026-10-29T10:00:00+07:00','description':'Build services.',**extra}
    return td.normalize_job(raw,'canonical_fixture')

class CanonicalAtomicTests(unittest.TestCase):
    def test_catalog_aliases_are_unique_and_idempotent(self):
        document,maps=k.catalog()
        for domain,rows in document['domains'].items():
            for row in rows:
                for alias in row['aliases']:
                    self.assertEqual(k.canonical(domain,alias),row['name'],(domain,alias))
                    self.assertEqual(k.canonical(domain,k.canonical(domain,alias)),row['name'])
    def test_cpp_aliases(self):
        for name in ['C++','Cplus','cpp','CPlusPlus','C plus plus','C + +','Ｃ＋＋','C&plus;&plus;']:
            self.assertEqual(k.canonical('skill',name),'C++')
    def test_csharp_aliases(self):
        for name in ['C#','Csharp','C sharp','C-sharp','C♯']:
            self.assertEqual(k.canonical('skill',name),'C#')
    def test_distinct_technologies_stay_distinct(self):
        for a,b in [('C','C++'),('C++','C#'),('Java','JavaScript'),('.NET','.NET Core'),('Angular','AngularJS')]:
            self.assertNotEqual(k.canonical('skill',a),k.canonical('skill',b))
    def test_composite_known_values_split(self):
        self.assertEqual(k.split_values('skill','C/C++'),['C','C++'])
        self.assertEqual(k.split_values('skill','Agile & Scrum'),['Agile','Scrum'])
        self.assertEqual(k.split_values('skill','JS; TypeScript'),['JavaScript','TypeScript'])
    def test_named_atomic_concepts_are_not_split(self):
        for name in ['CI/CD','UI/UX','Data Structures & Algorithms']:
            domain='knowledge' if name=='Data Structures & Algorithms' else 'skill'
            self.assertEqual(k.split_values(domain,name),[name])
    def test_unknown_composite_not_guessed(self):
        self.assertEqual(k.split_values('skill','InternalFoo/Bar'),['InternalFoo/Bar'])
        self.assertTrue(k.unresolved_composite('skill','InternalFoo/Bar'))
    def test_roles_seniority_city(self):
        self.assertEqual(k.canonical('role','Full-stack Developer'),'Fullstack Developer')
        self.assertEqual(k.canonical('seniority','Sr.'),'Senior')
        self.assertEqual(k.canonical('skill','QA'),'Quality Assurance')
        self.assertEqual(k.canonical('city','HCMC'),'Hồ Chí Minh')
    def test_education_and_soft_skills(self):
        self.assertEqual(k.canonical('degree','Đại học / Cử nhân'),'bachelor')
        self.assertEqual(k.canonical('major','CNTT'),'Information Technology')
        self.assertEqual(k.canonical('soft_skill','Làm việc nhóm'),'Teamwork')
    def test_enums_and_numeric_types(self):
        j=job();j.update(work_mode='Trực tiếp',salary_currency='usd',salary_period='monthly',salary_basis='trước thuế',experience_min_years='0.5')
        out=c.clean_record(j)
        self.assertEqual((out['work_mode'],out['salary_currency'],out['salary_period'],out['salary_basis'],out['experience_min_years']),('onsite','USD','month','gross',0.5))
    def test_missing_values_not_guessed(self):
        out=job()
        for key in ['salary_min','salary_max','salary_currency','salary_period','published_at']:
            self.assertIsNone(out[key])
    def test_foreign_language_and_role_tags_move_to_correct_relation(self):
        out=job(skills=['English','Project Manager','cpp'])
        self.assertNotIn('English',[s['name'] for s in out['skills']])
        self.assertIn('English',[s['language'] for s in out['foreign_languages']])
        self.assertIn('Project Manager',out['job_roles'])
        self.assertEqual([s['name'] for s in out['skills']],['C++'])
    def test_required_precedence_and_atomic_dedup(self):
        j=job();j['skills']=[{'name':n,'category':'technical','requirement_type':req} for n,req in [('cpp','preferred'),('Cplus','required'),('C/C++','unspecified')]]
        out=c.clean_record(j)
        self.assertEqual(len(out['skills']),2)
        self.assertEqual(next(s for s in out['skills'] if s['name']=='C++'),{'name':'C++','category':'programming_languages'})
    def test_record_copy_lineage_and_idempotence(self):
        j=job(skills=['C/C++','JS'],extra_skills=['Agile & Scrum']);before=copy.deepcopy(j);out=c.clean_record(j)
        self.assertEqual(j,before)
        for field in ['job_id','source_job_id','job_url','crawl_run_id','observed_at']:
            self.assertEqual(j[field],out[field])
        self.assertEqual(c.clean_record(out),out)
        self.assertEqual(len(out),30)
    def test_free_text_aliases_and_negative_requirements(self):
        out=job(requirements='Cpp is mandatory. Csharp is not required.')
        self.assertIn('C++',[s['name'] for s in out['skills']])
        self.assertNotIn('C#',[s['name'] for s in out['skills']])
    def test_itviec_also_uses_shared_catalog(self):
        raw={'source_job_id':'99','url':'https://itviec.com/it-jobs/test-99','title':'Backend Developer',
             'job_description':'Build services.','your_skills_and_experience':'Cpp required. English preferred.',
             'crawled_at':'2026-10-29T10:00:00+07:00','skills':['Cplus']}
        original=copy.deepcopy(raw);out=iv.normalize_job(raw,'fixture')
        self.assertEqual(raw,original);self.assertIn('C++',[s['name'] for s in out['skills']]);self.assertEqual(len(out),30)

if __name__=='__main__':unittest.main()
