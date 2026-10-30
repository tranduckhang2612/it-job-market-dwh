import copy,json,unittest
from pathlib import Path
from data_preprocessing.itviec_normalize_data import normalize_itviec as iv
from data_preprocessing.topdev_normalize_data import normalize_topdev as td
from data_preprocessing.staging_cleaning import clean_record

class AnalyticsContractTests(unittest.TestCase):
    def sample(self):
        return td.normalize_job({'id':99,'title':'Backend Developer','description':'Build services.',
            'detail_url':'https://topdev.vn/detail-jobs/backend-99','observed_at':'2026-10-30T10:00:00+07:00',
            'skills_arr':['Python'],'requirements_original':'Docker preferred. English required.\nTốt nghiệp Đại học chuyên ngành Công nghệ thông tin.'},'fixture')
    def test_all_three_output_groups_remove_requirement_labels(self):
        out=self.sample()
        self.assertEqual(len(out),30)
        for field in ('skills','foreign_languages','education_requirements'):
            self.assertTrue(out[field],field)
            for item in out[field]:self.assertNotIn('requirement_type',item)
    def test_old_labelled_input_is_upgraded_without_mutation(self):
        value=self.sample()
        for field in ('skills','foreign_languages','education_requirements'):
            for item in value[field]:item['requirement_type']='preferred'
        before=copy.deepcopy(value);out=clean_record(value)
        self.assertEqual(value,before)
        self.assertNotIn('requirement_type',json.dumps(out))
    def test_repeat_cleaning_is_idempotent(self):
        value=self.sample();self.assertEqual(clean_record(value),value)
    def test_validator_rejects_obsolete_label(self):
        value=self.sample();value['skills'][0]['requirement_type']='required'
        with self.assertRaises(td.CrawlError):td.validate_normalized_job(value)
    def test_versions_match_new_contract(self):
        self.assertEqual(iv.NORMALIZATION_VERSION,'1.2');self.assertEqual(td.NORMALIZATION_VERSION,'1.2')
    def test_fresh_sql_does_not_create_removed_columns(self):
        root=Path(__file__).resolve().parents[1]
        for path in ['itviec_db/itviec_schema.sql','topdev_db/topdev_schema.sql']:
            self.assertNotIn('requirement_type',(root/'data_preprocessing'/path).read_text(encoding='utf-8'))
    def test_analytics_model_has_no_requirement_column(self):
        text=(Path(__file__).resolve().parents[1]/'data_preprocessing/analytics.sql').read_text(encoding='utf-8')
        self.assertNotIn('requirement_type','\n'.join(text.splitlines()[1:]))
        self.assertIn('count(DISTINCT r.job_id)',text)
    def test_presence_keeps_mandatory_and_nice_to_have_skills(self):
        self.assertTrue({'Python','Docker'}.issubset({x['name'] for x in self.sample()['skills']}))

if __name__=='__main__':unittest.main()
