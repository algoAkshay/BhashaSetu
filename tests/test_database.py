import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.db.seed import DEFAULT_CSV, SeedError, seed_database
from backend.models import Base, EligibilityRule, Scheme
from backend.normalization import RuleValidationError
from backend.repositories.rule_repository import RuleRepository
from backend.repositories.scheme_repository import SchemeRepository
from backend.services.eligibility_service import EligibilityService
from backend.services.scheme_service import SchemeService
from tests.support import ROOT, migrate, test_database
from tests.test_rules import rule


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = test_database()
        self.addCleanup(self.engine.dispose)

    def test_seed_and_reseed_counts_and_metadata(self):
        first = seed_database(self.factory)
        second = seed_database(self.factory)
        self.assertEqual((first.processed, first.created, first.skipped, first.rules_created), (67, 67, 0, 217))
        self.assertEqual((second.processed, second.created, second.skipped, second.rules_created), (67, 0, 67, 0))
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EligibilityRule)), 217)
            scheme = SchemeRepository(session).get_active_schemes()[0]
            self.assertIsNone(scheme.state)
            self.assertIsNone(scheme.official_url)
            self.assertTrue(all(rule.scheme_id == scheme.id for rule in scheme.rules))

    def test_active_schemes_and_repository_operations(self):
        with self.factory.begin() as session:
            service = SchemeService(session)
            first = service.create_with_rules(name="First", rules=[rule()])
            second = service.create_with_rules(name="Second", rules=[rule(value=60)])
            repository = SchemeRepository(session)
            repository.set_active(second, False)
            repository.update(first, name="Renamed")
            self.assertEqual([scheme.name for scheme in repository.get_active_schemes()], ["Renamed"])
            self.assertEqual(repository.get_by_id(first.id).name, "Renamed")
            self.assertIsNone(repository.get_by_id(999999))
            rules = RuleRepository(session)
            second_rule = rules.get_rules_for_scheme(second.id)[0]
            self.assertEqual(second_rule.value, 60)
            rules.update(second_rule, rule(value=65))
            self.assertEqual(rules.get_rules_for_scheme(second.id)[0].value, 65)
            rules.delete(second_rule)
            self.assertEqual(rules.get_rules_for_scheme(second.id), [])
            result = EligibilityService(session).evaluate_all_schemes({"age": 20})
            self.assertEqual([item.scheme_name for item in result], ["Renamed"])

    def test_scheme_and_rules_rollback_together(self):
        with self.factory.begin() as session:
            service = SchemeService(session)
            with self.assertRaises(RuleValidationError):
                service.create_with_rules(name="Bad", rules=[rule(), rule(operator="BAD")])
            self.assertEqual(SchemeRepository(session).get_active_schemes(), [])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EligibilityRule)), 0)

    def test_outer_transaction_can_rollback_multiple_schemes(self):
        with self.assertRaises(RuntimeError), self.factory.begin() as session:
            service = SchemeService(session)
            service.create_with_rules(name="First", rules=[rule()])
            service.create_with_rules(name="Second", rules=[rule()])
            raise RuntimeError("Rollback")
        with self.factory() as session:
            self.assertEqual(SchemeRepository(session).get_active_schemes(), [])

    def test_repository_edits_visible_in_same_session_and_reseed_preserves_them(self):
        seed_database(self.factory)
        with self.factory.begin() as session:
            repository = SchemeRepository(session)
            scheme = repository.get_by_id(1)
            original_count = len(scheme.rules)
            RuleRepository(session).create(scheme.id, rule("state", "==", "Bihar", "string"))
            self.assertEqual(len(repository.get_by_id(scheme.id).rules), original_count + 1)
            repository.update(scheme, name="Edited name")
            repository.set_active(scheme, False)
        report = seed_database(self.factory)
        self.assertEqual(report.skipped, 67)
        with self.factory() as session:
            scheme = SchemeRepository(session).get_by_id(1)
            self.assertEqual(scheme.name, "Edited name")
            self.assertFalse(scheme.is_active)
            self.assertEqual(len(scheme.rules), original_count + 1)

    def test_corrupt_seed_is_atomic_and_reports_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "schemes.csv"
            source.write_text("scheme_name,min_age,max_age,gender,max_income\nValid,18,60,Any,200000\nBad,sixty,70,Any,200000\n", encoding="utf-8")
            with self.assertRaises(SeedError) as caught:
                seed_database(self.factory, source)
        self.assertEqual(caught.exception.report.processed, 2)
        self.assertIn("Row 3", caught.exception.report.errors[0])
        with self.factory() as session:
            self.assertEqual(SchemeRepository(session).get_active_schemes(), [])

    def test_seed_database_failure_rolls_back_previous_rows(self):
        original = SchemeService.create_with_rules
        calls = 0

        def fail_second(service, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SQLAlchemyError("simulated failure")
            return original(service, **kwargs)

        with patch.object(SchemeService, "create_with_rules", fail_second), self.assertRaises(SeedError) as caught:
            seed_database(self.factory)
        self.assertEqual(caught.exception.report.created, 0)
        self.assertEqual(caught.exception.report.rules_created, 0)
        with self.factory() as session:
            self.assertEqual(SchemeRepository(session).get_active_schemes(), [])

    def test_missing_seed_file_reports_error(self):
        with self.assertRaises(SeedError) as caught:
            seed_database(self.factory, ROOT / "database" / "does-not-exist.csv")
        self.assertEqual(caught.exception.report.created, 0)
        self.assertTrue(caught.exception.report.errors)

    def test_invalid_csv_variants(self):
        rows = ["Bad,60,18,Any,100", "Bad,18,60,Unknown,100", "Bad,18,60,Any,-1",
                "Bad,18,60,Any", "Bad,18,60,Any,100,extra",
                "Bad,18,60,Any,100\nBad,18,60,Any,100"]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "bad.csv"
            for row in rows:
                source.write_text("scheme_name,min_age,max_age,gender,max_income\n" + row + "\n", encoding="utf-8")
                with self.subTest(row=row), self.assertRaises(SeedError):
                    seed_database(self.factory, source)
            source.write_text("wrong,header\n", encoding="utf-8")
            with self.assertRaises(SeedError):
                seed_database(self.factory, source)

    def test_foreign_key_enforced(self):
        with self.assertRaises(IntegrityError), self.factory.begin() as session:
            RuleRepository(session).create(99999, rule())

    def test_cascade_delete(self):
        with self.factory.begin() as session:
            scheme = SchemeService(session).create_with_rules(name="Delete", rules=[rule()])
            session.delete(scheme)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(EligibilityRule)), 0)

    def test_migration_matches_models_and_indexes(self):
        with self.engine.connect() as connection:
            self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
            inspector = inspect(connection)
            self.assertIn("ix_schemes_is_active", [index["name"] for index in inspector.get_indexes("schemes")])
            self.assertIn("ix_eligibility_rules_scheme_id", [index["name"] for index in inspector.get_indexes("eligibility_rules")])

    def test_migration_downgrade_upgrade(self):
        with self.engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.downgrade(config, "base")
            self.assertNotIn("schemes", inspect(connection).get_table_names())
            migrate(connection)
            self.assertIn("eligibility_rules", inspect(connection).get_table_names())


class RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine, cls.factory = test_database()
        seed_database(cls.factory)
        with DEFAULT_CSV.open(encoding="utf-8", newline="") as stream:
            cls.rows = list(csv.DictReader(stream))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def compare_legacy(self, age, gender, income):
        # Frozen pre-refactor predicate, independent of the new rule converter.
        expected = [row["scheme_name"] for row in self.rows
                    if int(row["min_age"]) <= age <= int(row["max_age"])
                    and (row["gender"] == "Any" or row["gender"] == gender)
                    and income <= int(row["max_income"])]
        with self.factory() as session:
            results = EligibilityService(session).evaluate_all_schemes(
                {"age": age, "gender": gender, "annual_income": income})
        actual = [result.scheme_name for result in results if result.status == "ELIGIBLE"]
        self.assertEqual(actual, expected)

    def test_representative_complete_profiles(self):
        for age, gender, income in [(32, "Male", 150000), (5, "Female", 100000),
                                    (64, "Female", 150000), (18, "Male", 0),
                                    (100, "Male", 200000), (45, "Female", 900000)]:
            with self.subTest(age=age, gender=gender, income=income):
                self.compare_legacy(age, gender, income)

    def test_every_csv_row_boundaries(self):
        for row in self.rows:
            for age in {int(row["min_age"]) - 1, int(row["min_age"]), int(row["max_age"]), int(row["max_age"]) + 1}:
                if age < 0:
                    continue
                for gender in ("Male", "Female"):
                    for income in (int(row["max_income"]), int(row["max_income"]) + 1):
                        with self.subTest(scheme=row["scheme_name"], age=age, gender=gender, income=income):
                            self.compare_legacy(age, gender, income)

    def test_unrestricted_gender_no_longer_requires_gender(self):
        with self.factory() as session:
            results = EligibilityService(session).evaluate_all_schemes({"age": 32, "annual_income": 150000})
        self.assertTrue(any(result.status == "ELIGIBLE" for result in results))
        self.assertTrue(any(result.missing_fields == ["gender"] for result in results))
