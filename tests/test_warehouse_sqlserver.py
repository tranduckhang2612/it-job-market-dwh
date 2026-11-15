"""Run against disposable databases on an SQL Server with create/drop privileges."""
import os
import unittest
import uuid
from datetime import datetime
from unittest.mock import patch

from data_preprocessing.warehouse import job_values as warehouse

SQLSERVER_TEST_DSN = os.environ.get("IT_JOB_WAREHOUSE_TEST_DSN")


def sample():
    jobs = {
        "topdev:1": ("topdev", "Python Developer", "ABC", "https://example.test/1", "run1",
                     datetime(2020, 12, 29, 20)),
        "itviec:2": ("itviec", "Backend Developer", "XYZ", "https://example.test/2", "run2",
                     datetime(2020, 12, 29, 21)),
    }
    labels = {
        "programming_language": ("Python", "programming_language", None),
        "framework": ("Django", "framework_library", None),
        "core_skill": ("Git", "tool", None),
        "role": ("Backend Developer", "", None),
        "city": ("Hồ Chí Minh", "", None),
        "foreign_language": ("English", "", "B2"),
        "level": ("Junior", "", None),
        "soft_skill": ("Teamwork", "", None),
    }
    groups = {group: [(job, *label) for job in jobs] for group, label in labels.items()}
    groups["programming_language"].append(("topdev:1", "SQL", "programming_language", None))
    groups["foreign_language"] += [("topdev:1", "English", "", "C1"), ("topdev:1", "English", "", "C1")]
    return jobs, groups


@unittest.skipUnless(SQLSERVER_TEST_DSN, "Set IT_JOB_WAREHOUSE_TEST_DSN for SQL Server integration")
class WarehouseSqlServerTests(unittest.TestCase):
    def setUp(self):
        import pyodbc

        self.pyodbc = pyodbc
        self.admin = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=True)
        self.addCleanup(self.admin.close)
        self.admin.execute("USE master")
        self.db = "dw_test_" + uuid.uuid4().hex
        self.admin.execute(f"CREATE DATABASE [{self.db}]")
        self.addCleanup(self.drop_database)
        self.conn = pyodbc.connect(SQLSERVER_TEST_DSN, autocommit=False)
        self.addCleanup(self.conn.close)
        self.conn.execute(f"USE [{self.db}]")
        self.conn.commit()

    def drop_database(self):
        self.admin.execute(f"ALTER DATABASE [{self.db}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; "
                           f"DROP DATABASE [{self.db}];")

    def rows(self, query):
        return [tuple(row) for row in self.conn.cursor().execute(query)]

    def table_names(self):
        return {name for (name,) in self.rows("SELECT name FROM sys.tables WHERE schema_id=SCHEMA_ID('dw')")}

    def columns(self, object_name):
        return {name for (name,) in self.rows(f"SELECT name FROM sys.columns WHERE object_id=OBJECT_ID('dw.{object_name}')")}

    def test_new_schema_all_groups_and_idempotent_load(self):
        first = warehouse.apply(self.conn, sample())
        self.assertEqual(first["jobs"], 2)
        self.assertEqual(first["links"], {g: {"programming_language": 3, "foreign_language": 4}.get(g, 2)
                                          for g in warehouse.GROUPS})
        self.assertEqual(self.table_names(), {"fact_job", "fact_job_necessary_skill", "fact_job_role_level_city",
                                              "dim_date", "dim_necessary_skill"}
                         | {"dim_" + g for g in warehouse.GROUPS})
        for group in warehouse.GROUPS:
            ids = self.rows(f"SELECT {group}_key FROM dw.dim_{group}")
            self.assertEqual(len(set(ids)), first["links"][group])
            kept = set(warehouse.DIM_FIELDS[group])
            self.assertEqual(self.columns("dim_" + group), {
                group + "_key", "fact_job_key", "name", "created_at_utc",
            } | kept)
            self.assertEqual(self.columns("job_" + group), {
                "job_id", "source", "title", "company_name", "observed_at_utc", "name",
            } | kept)
        self.assertEqual(self.rows("SELECT DISTINCT name,programming_language FROM dw.job_framework"),
                         [("Django", "Python")])
        python_rows = self.rows(
            "SELECT d.programming_language_key, f.job_id, d.name FROM dw.dim_programming_language d "
            "JOIN dw.fact_job f ON f.fact_job_key=d.fact_job_key WHERE d.name=N'Python' "
            "ORDER BY d.programming_language_key"
        )
        self.assertEqual([r[1:] for r in python_rows], [("topdev:1", "Python"), ("itviec:2", "Python")])
        self.assertNotEqual(python_rows[0][0], python_rows[1][0])
        before = {g: self.rows(f"SELECT * FROM dw.dim_{g} ORDER BY {g}_key") for g in warehouse.GROUPS}
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, sample())["data_writes"], 0)
        self.assertEqual({g: self.rows(f"SELECT * FROM dw.dim_{g} ORDER BY {g}_key") for g in warehouse.GROUPS}, before)
        self.assertEqual(self.rows("SELECT observed_date_key, SUM(job_posting_count) FROM dw.fact_job "
                                   "GROUP BY observed_date_key"), [(20201229, 2)])
        self.assertEqual(first["role_level_city_combinations"], 2)

    def profile_sample(self):
        jobs, groups = sample()
        groups["role"].append(("topdev:1", "Data Engineer", "", None))
        groups["level"] = [("topdev:1", "Junior", "", None), ("topdev:1", "Senior", "", None)]
        groups["city"].append(("topdev:1", "Hà Nội", "", None))
        return jobs, groups

    def test_role_city_combines_only_within_a_job_without_level_or_metadata(self):
        result = warehouse.apply(self.conn, self.profile_sample())
        self.assertEqual(result["role_level_city_combinations"], 5)
        self.assertEqual(self.rows(
            "SELECT j.job_id,COUNT(*) FROM dw.job_role_level_city v "
            "JOIN dw.fact_job j ON j.fact_job_key=v.fact_job_key "
            "GROUP BY j.job_id ORDER BY j.job_id"
        ), [("itviec:2", 1), ("topdev:1", 4)])
        expected = {(role, city) for role in ("Backend Developer", "Data Engineer")
                    for city in ("Hà Nội", "Hồ Chí Minh")}
        self.assertEqual(set(self.rows(
            "SELECT role_name,city_name FROM dw.job_role_level_city "
            "WHERE fact_job_key=(SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'topdev:1')"
        )), expected)
        self.assertEqual(self.rows(
            "SELECT SUM(combination_count),COUNT(DISTINCT fact_job_key) FROM dw.fact_job_role_level_city"
        ), [(5, 2)])
        self.assertEqual(self.columns("fact_job_role_level_city"), {
            "fact_job_role_level_city_key", "fact_job_key", "role_key", "city_key", "combination_count",
        })
        self.assertEqual(self.columns("job_role_level_city"), {
            "fact_job_role_level_city_key", "fact_job_key", "role_key", "city_key",
            "role_name", "role_category", "city_name", "combination_count",
        })
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.dim_level"), [(2,)])
        before = self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key")
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, self.profile_sample())["data_writes"], 0)
        self.assertEqual(self.rows(
            "SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key"
        ), before)

    def test_role_level_city_missing_all_dimensions_keeps_the_job(self):
        jobs, groups = sample()
        for group in warehouse.PROFILE_GROUPS:
            groups[group] = []
        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["role_level_city_combinations"], 2)
        self.assertEqual(self.rows(
            "SELECT role_key,city_key FROM dw.fact_job_role_level_city"
        ), [(None, None), (None, None)])
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["data_writes"], 0)

    def test_role_level_city_backfill_only_leaves_other_dw_tables_unchanged(self):
        warehouse.apply(self.conn, self.profile_sample())
        before = self.rows("SELECT * FROM dw.dim_role ORDER BY role_key")
        before_skills = self.rows("SELECT * FROM dw.fact_job_necessary_skill ORDER BY fact_job_necessary_skill_key")
        self.conn.execute("DELETE FROM dw.fact_job_role_level_city")
        self.conn.commit()
        result = warehouse.refresh_role_level_city(self.conn)
        self.assertEqual(result, {"jobs": 2, "role_level_city_combinations": 5, "data_writes": 5})
        self.assertEqual(self.rows("SELECT * FROM dw.dim_role ORDER BY role_key"), before)
        self.assertEqual(self.rows(
            "SELECT * FROM dw.fact_job_necessary_skill ORDER BY fact_job_necessary_skill_key"
        ), before_skills)
        self.conn.commit()
        self.assertEqual(warehouse.refresh_role_level_city(self.conn)["data_writes"], 0)

    def test_role_level_city_removes_changed_occurrences_and_preserves_other_job_fact_ids(self):
        jobs, groups = self.profile_sample()
        warehouse.apply(self.conn, (jobs, groups))
        unchanged = self.rows(
            "SELECT * FROM dw.fact_job_role_level_city WHERE fact_job_key="
            "(SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'itviec:2')"
        )
        self.conn.commit()
        groups["city"] = [row for row in groups["city"] if row[:2] != ("topdev:1", "Hà Nội")]
        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["role_level_city_combinations"], 3)
        self.assertEqual(self.rows(
            "SELECT * FROM dw.fact_job_role_level_city WHERE fact_job_key="
            "(SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'itviec:2')"
        ), unchanged)
        self.conn.commit()
        groups["city"].append(("topdev:1", "Đà Nẵng", "", None))
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["role_level_city_combinations"], 5)
        self.conn.commit()
        del jobs["topdev:1"]
        groups = {group: [row for row in rows if row[0] != "topdev:1"] for group, rows in groups.items()}
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["role_level_city_combinations"], 1)
        self.assertEqual(self.rows("SELECT * FROM dw.fact_job_role_level_city"), unchanged)

    def test_role_level_city_rejects_cross_job_keys_and_duplicate_combinations(self):
        warehouse.apply(self.conn, sample())
        own = self.rows(
            "SELECT fact_job_key,role_key,city_key FROM dw.job_role_level_city "
            "WHERE fact_job_key=(SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'topdev:1')"
        )[0]
        foreign_role = self.rows(
            "SELECT role_key FROM dw.job_role_level_city "
            "WHERE fact_job_key=(SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'itviec:2')"
        )[0][0]
        self.conn.commit()
        query = "INSERT INTO dw.fact_job_role_level_city(fact_job_key,role_key,city_key) VALUES(?,?,?)"
        with self.assertRaises(self.pyodbc.IntegrityError):
            self.conn.execute(query, own[0], foreign_role, own[2])
        self.conn.rollback()
        with self.assertRaises(self.pyodbc.IntegrityError):
            self.conn.execute(query, *own)
        self.conn.rollback()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.fact_job_role_level_city"), [(2,)])

    def legacy_profile_fact(self):
        warehouse.apply(self.conn, self.profile_sample())
        self.conn.execute("DROP VIEW dw.job_role_level_city")
        self.conn.execute("ALTER TABLE dw.fact_job_role_level_city DROP CONSTRAINT UQ_fact_job_role_level_city_combination")
        self.conn.execute("ALTER TABLE dw.fact_job_role_level_city ADD level_key int NULL")
        self.conn.execute("CREATE UNIQUE INDEX UX_dim_level_job_level ON dw.dim_level(fact_job_key,level_key)")
        self.conn.execute("ALTER TABLE dw.fact_job_role_level_city ADD CONSTRAINT FK_fact_job_role_level_city_level "
                          "FOREIGN KEY (fact_job_key,level_key) REFERENCES dw.dim_level(fact_job_key,level_key)")
        self.conn.execute("CREATE INDEX IX_fact_job_role_level_city_level ON dw.fact_job_role_level_city(level_key,fact_job_key)")
        self.conn.execute("ALTER TABLE dw.fact_job_role_level_city ADD CONSTRAINT UQ_fact_job_role_level_city_combination "
                          "UNIQUE(fact_job_key,role_key,level_key,city_key)")
        self.conn.execute("UPDATE f SET level_key=(SELECT MIN(level_key) FROM dw.dim_level l "
                          "WHERE l.fact_job_key=f.fact_job_key) FROM dw.fact_job_role_level_city f")
        self.conn.execute("INSERT INTO dw.fact_job_role_level_city(fact_job_key,role_key,city_key,level_key) "
                          "SELECT f.fact_job_key,f.role_key,f.city_key,l.level_key "
                          "FROM dw.fact_job_role_level_city f JOIN dw.dim_level l ON l.fact_job_key=f.fact_job_key "
                          "WHERE l.level_key<>f.level_key")
        self.conn.execute("CREATE VIEW dw.job_role_level_city AS SELECT f.*,l.name AS level_name "
                          "FROM dw.fact_job_role_level_city f LEFT JOIN dw.dim_level l "
                          "ON l.fact_job_key=f.fact_job_key AND l.level_key=f.level_key")
        self.conn.commit()
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.fact_job_role_level_city"), [(9,)])

    def test_remove_level_migration_keeps_lowest_fact_ids_and_dim_level(self):
        self.legacy_profile_fact()
        kept = self.rows("SELECT MIN(fact_job_role_level_city_key),fact_job_key,role_key,city_key "
                         "FROM dw.fact_job_role_level_city GROUP BY fact_job_key,role_key,city_key "
                         "ORDER BY MIN(fact_job_role_level_city_key)")
        levels = self.rows("SELECT * FROM dw.dim_level ORDER BY level_key")
        self.conn.commit()
        result = warehouse.refresh_role_level_city(self.conn)
        self.assertEqual(result["role_level_city_combinations"], 5)
        self.assertEqual(self.rows("SELECT fact_job_role_level_city_key,fact_job_key,role_key,city_key "
                                   "FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key"), kept)
        self.assertEqual(self.rows("SELECT * FROM dw.dim_level ORDER BY level_key"), levels)
        self.assertNotIn("level_key", self.columns("fact_job_role_level_city"))
        self.assertNotIn("level_name", self.columns("job_role_level_city"))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sys.foreign_keys WHERE parent_object_id="
                                   "OBJECT_ID('dw.fact_job_role_level_city') AND name="
                                   "'FK_fact_job_role_level_city_level'"), [(0,)])
        self.conn.commit()
        self.assertEqual(warehouse.refresh_role_level_city(self.conn)["data_writes"], 0)

    def test_remove_level_migration_failure_restores_column_duplicates_and_view(self):
        self.legacy_profile_fact()
        before = self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key")
        definition = self.rows("SELECT OBJECT_DEFINITION(OBJECT_ID('dw.job_role_level_city'))")
        self.conn.commit()
        with patch.object(warehouse, "sync_role_level_city", side_effect=RuntimeError("migration failure")):
            with self.assertRaisesRegex(RuntimeError, "migration failure"):
                warehouse.refresh_role_level_city(self.conn)
        self.assertIn("level_key", self.columns("fact_job_role_level_city"))
        self.assertEqual(self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key"), before)
        self.assertEqual(self.rows("SELECT OBJECT_DEFINITION(OBJECT_ID('dw.job_role_level_city'))"), definition)

    def test_changing_level_occurrences_does_not_change_role_city_fact(self):
        jobs, groups = self.profile_sample()
        warehouse.apply(self.conn, (jobs, groups))
        before = self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key")
        self.conn.commit()
        groups["level"] = []
        warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key"), before)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.dim_level"), [(0,)])

    def test_role_level_city_failure_rolls_back_existing_dimensions_and_facts(self):
        warehouse.apply(self.conn, self.profile_sample())
        before = self.rows("SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key")
        old_cities = self.rows("SELECT * FROM dw.dim_city ORDER BY city_key")
        self.conn.commit()
        jobs, groups = self.profile_sample()
        groups["city"] = []
        with patch.object(warehouse, "sync_role_level_city", side_effect=RuntimeError("injected_profile_failure")):
            with self.assertRaisesRegex(RuntimeError, "injected_profile_failure"):
                warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(self.rows(
            "SELECT * FROM dw.fact_job_role_level_city ORDER BY fact_job_role_level_city_key"
        ), before)
        self.assertEqual(self.rows("SELECT * FROM dw.dim_city ORDER BY city_key"), old_cities)

    def test_necessary_skills_use_one_conformed_dimension_and_stable_ids(self):
        first = warehouse.apply(self.conn, sample())
        self.assertEqual(first["necessary_skills"], 11)
        self.assertEqual(self.rows(
            "SELECT COUNT(*),SUM(skill_mention_count),COUNT(necessary_skill_key),"
            "COUNT(fact_job_key) FROM dw.fact_job_necessary_skill"
        ), [(11, 11, 11, 11)])
        self.assertEqual(self.rows(
            "SELECT skill_type,name,category FROM dw.dim_necessary_skill "
            "ORDER BY skill_type,name"
        ), [("core_skill", "Git", "tool"),
            ("foreign_language", "English", ""),
            ("framework", "Django", "framework_library"),
            ("programming_language", "Python", "programming_language"),
            ("programming_language", "SQL", "programming_language")])
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM dw.fact_job_necessary_skill f "
            "JOIN dw.fact_job j ON j.fact_job_key=f.fact_job_key "
            "JOIN dw.dim_necessary_skill d ON d.necessary_skill_key=f.necessary_skill_key"
        ), [(11,)])
        self.assertEqual(self.columns("job_necessary_skill"), {
            "fact_job_necessary_skill_key", "fact_job_key", "necessary_skill_key",
            "job_id", "source", "title", "company_name", "job_url", "crawl_run_id",
            "observed_at_utc", "observed_date_key", "skill_type", "skill_name",
            "category", "context_no", "level_text", "skill_mention_count",
        })
        before = self.rows(
            "SELECT fact_job_necessary_skill_key,fact_job_key,necessary_skill_key,"
            "context_no,level_text FROM dw.fact_job_necessary_skill "
            "ORDER BY fact_job_necessary_skill_key"
        )
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, sample())["data_writes"], 0)
        self.assertEqual(self.rows(
            "SELECT fact_job_necessary_skill_key,fact_job_key,necessary_skill_key,"
            "context_no,level_text FROM dw.fact_job_necessary_skill "
            "ORDER BY fact_job_necessary_skill_key"
        ), before)

    def test_removed_skill_occurrence_removes_its_fact_row(self):
        jobs, groups = sample()
        warehouse.apply(self.conn, (jobs, groups))
        self.conn.commit()
        groups["programming_language"] = [
            row for row in groups["programming_language"]
            if row[:2] != ("topdev:1", "SQL")
        ]
        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["necessary_skills"], 10)
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM dw.fact_job_necessary_skill f "
            "JOIN dw.dim_necessary_skill d "
            "ON d.necessary_skill_key=f.necessary_skill_key "
            "WHERE d.skill_type=N'programming_language' AND d.name=N'SQL'"
        ), [(0,)])
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["data_writes"], 0)

    def test_fact_examples_return_one_sentence_each(self):
        warehouse.apply(self.conn, sample())
        cursor = self.conn.cursor().execute(
            warehouse.DW_DDL.with_name("fact_examples.sql").read_text(encoding="utf-8")
        )
        fact = [tuple(row) for row in cursor.fetchall()]
        self.assertEqual(fact, [
            ("Vào ngày 29/12/2020 (observed_date_key = 20201229), nguồn topdev (source) ghi nhận tin "
             "tuyển dụng Python Developer của công ty ABC (fact_job_key = 1), với số tin là 1 "
             "(job_posting_count).",)
        ])
        self.assertTrue(cursor.nextset())
        sale = [tuple(row) for row in cursor.fetchall()]
        self.assertEqual(sale, [
            ("Vào ngày 29/12/2020 (OrderDateKey), khách hàng 21768 (CustomerKey) đã mua sản phẩm "
             "Adjustable Race có mã 310 (ProductKey) với tổng số tiền giao dịch là $3,578.27 (SalesAmount).",)
        ])
        self.assertFalse(cursor.nextset())

    def test_role_reclassification_updates_category_without_changing_ids(self):
        jobs, groups = sample()
        expected = {
            "AI Engineer": "Data/AI",
            "Penetration Tester": "Security",
            "Firmware Developer": "Embbed",
            "Front-end Developer": "Web/App",
            "Unlisted role": "Software Engineer",
        }
        groups["role"] = [("topdev:1", name, "other", None) for name in expected]
        groups["role"].append(("itviec:2", "AI Engineer", "", None))
        warehouse.apply(self.conn, (jobs, groups))
        ids = self.rows("SELECT role_key, fact_job_key, name FROM dw.dim_role ORDER BY role_key")
        self.conn.execute("UPDATE dw.dim_role SET category=CASE WHEN role_key % 2 = 0 THEN N'other' ELSE N'' END")
        self.conn.commit()

        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["data_writes"], 6)
        self.assertEqual(self.rows("SELECT role_key, fact_job_key, name FROM dw.dim_role ORDER BY role_key"), ids)
        categories = self.rows("SELECT name, category FROM dw.dim_role ORDER BY role_key")
        self.assertEqual(len(categories), 6)
        self.assertTrue(all(expected[name] == category for name, category in categories))
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["data_writes"], 0)

    def test_core_skill_category_backfill_keeps_ids_and_duplicate_occurrences(self):
        jobs, groups = sample()
        groups["core_skill"] = [
            ("topdev:1", "LLM", "other", None),
            ("topdev:1", "LLM", "data_ai", None),
            ("itviec:2", "LLM", "other", None),
            ("topdev:1", "NLP", "other", None),
            ("topdev:1", "Computer Vision", "other", None),
            ("topdev:1", "Kafka", "other", None),
            ("topdev:1", "Unlisted tool", "tool", None),
        ]
        warehouse.apply(self.conn, (jobs, groups))
        ids = self.rows("SELECT core_skill_key, fact_job_key, name FROM dw.dim_core_skill ORDER BY core_skill_key")
        self.conn.execute("UPDATE dw.dim_core_skill SET category=N'other' WHERE name<>N'Unlisted tool'")
        self.conn.commit()

        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["data_writes"], 6)
        self.assertEqual(result["links"]["core_skill"], 7)
        self.assertEqual(self.rows("SELECT core_skill_key, fact_job_key, name FROM dw.dim_core_skill "
                                   "ORDER BY core_skill_key"), ids)
        self.assertEqual(self.rows("SELECT name,category,COUNT(*) FROM dw.job_core_skill "
                                   "GROUP BY name,category ORDER BY name"),
                         [("Computer Vision", "data_ai", 1), ("Kafka", "data_streaming", 1),
                          ("LLM", "data_ai", 3), ("NLP", "data_ai", 1), ("Unlisted tool", "tool", 1)])
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["data_writes"], 0)

    def test_framework_language_backfill_keeps_existing_id(self):
        jobs, groups = sample()
        groups["framework"].append(("topdev:1", "MVVM", "framework_library", None))
        warehouse.apply(self.conn, (jobs, groups))
        ids = self.rows("SELECT framework_key, name FROM dw.dim_framework ORDER BY framework_key")
        self.conn.execute("UPDATE dw.dim_framework SET programming_language=NULL WHERE name=N'Django'")
        self.conn.commit()
        self.assertEqual(warehouse.apply(self.conn, (jobs, groups))["data_writes"], 2)
        self.assertEqual(self.rows("SELECT framework_key, name FROM dw.dim_framework ORDER BY framework_key"), ids)
        self.assertEqual(self.rows("SELECT DISTINCT name,programming_language FROM dw.dim_framework ORDER BY name"),
                         [("Django", "Python"), ("MVVM", None)])

    def test_removed_job_and_occurrences_do_not_leave_orphans(self):
        jobs, groups = sample()
        warehouse.apply(self.conn, (jobs, groups))
        old = self.rows("SELECT fact_job_key FROM dw.fact_job WHERE job_id=N'topdev:1'")
        self.conn.commit()
        del jobs["itviec:2"]
        groups = {g: [r for r in rows if r[0] == "topdev:1"] for g, rows in groups.items()}
        groups["foreign_language"].pop()
        result = warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(result["jobs"], 1)
        self.assertEqual(result["links"]["foreign_language"], 2)
        self.assertEqual(self.rows("SELECT fact_job_key FROM dw.fact_job"), old)
        self.assertEqual(self.rows("SELECT level_text FROM dw.job_foreign_language ORDER BY level_text"),
                         [("B2",), ("C1",)])
        self.assertEqual(result["necessary_skills"], 6)
        self.assertEqual(self.rows(
            "SELECT COUNT(*) FROM dw.fact_job_necessary_skill s "
            "LEFT JOIN dw.fact_job f ON f.fact_job_key=s.fact_job_key "
            "WHERE f.fact_job_key IS NULL"
        ), [(0,)])

    def create_legacy_schema(self):
        c = self.conn.cursor()
        c.execute("CREATE SCHEMA dw")
        c.execute("""
            CREATE TABLE dw.fact_job(
                job_key int IDENTITY PRIMARY KEY, job_id nvarchar(256) NOT NULL UNIQUE,
                source nvarchar(128) NOT NULL, title nvarchar(max), company_name nvarchar(max),
                job_url nvarchar(max), crawl_run_id nvarchar(256), observed_at_utc datetime2(6) NOT NULL
            );
            INSERT INTO dw.fact_job(job_id,source,title,observed_at_utc) VALUES
                (N'topdev:1',N'topdev',N'One','20201229'), (N'itviec:2',N'itviec',N'Two','20201230');
        """)
        for group in warehouse.GROUPS:
            category = "category" if "category" in warehouse.DIM_FIELDS[group] else ""
            level = "N'B2'" if "level_text" in warehouse.DIM_FIELDS[group] else "NULL"
            c.execute(f"""
                CREATE TABLE dw.dim_{group} (
                    dim_id int IDENTITY PRIMARY KEY, name nvarchar(512) NOT NULL,
                    category nvarchar(128) NOT NULL, created_at_utc datetime2(6) NOT NULL,
                    updated_at_utc datetime2(6) NOT NULL, UNIQUE(name, category)
                );
                CREATE TABLE dw.bridge_job_{group} (
                    job_key int NOT NULL REFERENCES dw.fact_job(job_key) ON DELETE CASCADE,
                    dim_id int NOT NULL REFERENCES dw.dim_{group}(dim_id), context_no int NOT NULL,
                    level_text nvarchar(max), PRIMARY KEY(job_key, dim_id, context_no)
                );
                INSERT INTO dw.dim_{group}(name,category,created_at_utc,updated_at_utc)
                    VALUES(N'Shared',N'{category}','20201229','20201229');
                INSERT INTO dw.bridge_job_{group}(job_key,dim_id,context_no,level_text) VALUES
                    (1,1,0,NULL), (2,1,0,{level}), (2,1,1,{level});
            """)
            c.execute(f"""CREATE VIEW dw.job_{group} AS
                SELECT f.job_id,f.source,f.title,f.company_name,f.observed_at_utc,d.name,d.category,b.level_text
                FROM dw.bridge_job_{group} b JOIN dw.fact_job f ON f.job_key=b.job_key
                JOIN dw.dim_{group} d ON d.dim_id=b.dim_id;""")
        self.conn.commit()

    def test_migration_preserves_fact_ids_and_every_legacy_occurrence(self):
        self.create_legacy_schema()
        queries = {}
        for group in warehouse.GROUPS:
            fields = ("job_id", "source", "title", "company_name", "observed_at_utc", "name",
                      *(f for f in warehouse.DIM_FIELDS[group] if f != "programming_language"))
            queries[group] = f"SELECT {','.join(fields)} FROM dw.job_{group} ORDER BY job_id"
        before = {group: self.rows(query) for group, query in queries.items()}
        warehouse.setup(self.conn.cursor())
        self.conn.commit()
        self.assertEqual(self.table_names(), {"fact_job", "fact_job_necessary_skill", "fact_job_role_level_city",
                                              "dim_date", "dim_necessary_skill"}
                         | {"dim_" + g for g in warehouse.GROUPS})
        self.assertEqual(self.rows("SELECT fact_job_key,observed_date_key,job_posting_count FROM dw.fact_job "
                                   "ORDER BY fact_job_key"), [(1, 20201229, 1), (2, 20201230, 1)])
        for group in warehouse.GROUPS:
            self.assertEqual(self.rows(queries[group]), before[group])
            self.assertEqual(len(self.rows(f"SELECT DISTINCT {group}_key FROM dw.dim_{group}")), 3)

    def test_migration_stops_before_dropping_populated_column(self):
        self.create_legacy_schema()
        before = self.table_names()
        self.conn.execute("UPDATE dw.dim_city SET category=N'not empty'")
        self.conn.commit()
        with self.assertRaisesRegex(self.pyodbc.Error, "Nonempty category in dim_city"):
            warehouse.apply(self.conn, sample())
        self.assertEqual(self.table_names(), before)
        self.assertEqual(self.rows("SELECT category FROM dw.dim_city"), [("not empty",)])

    def test_invalid_load_rolls_back_migration_and_rows(self):
        self.create_legacy_schema()
        before = self.table_names()
        self.conn.commit()
        jobs, groups = sample()
        groups["soft_skill"] = [("topdev:1", "x" * 513, "", None)]
        with self.assertRaisesRegex(self.pyodbc.Error, "truncat"):
            warehouse.apply(self.conn, (jobs, groups))
        self.assertEqual(self.table_names(), before)
        self.assertEqual(self.rows("SELECT job_key,title FROM dw.fact_job ORDER BY job_key"), [(1, "One"), (2, "Two")])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM dw.bridge_job_soft_skill"), [(3,)])
