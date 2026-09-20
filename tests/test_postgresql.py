"""Opt-in live test. Requires CREATE SCHEMA on a disposable TEST_DATABASE_URL."""
import os
import unittest
import uuid

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from backend.db.seed import seed_database
from backend.models import Base, EligibilityRule, Scheme
from backend.normalization import RuleValidationError
from backend.repositories.scheme_repository import SchemeRepository
from backend.services.eligibility_service import EligibilityService
from backend.services.scheme_service import SchemeService
from tests.support import ROOT, migrate
from tests.test_rules import rule


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for live PostgreSQL verification")
class PostgreSQLTests(unittest.TestCase):
    def test_migration_seed_reseed_json_rules_transactions_and_downgrade(self):
        url = make_url(os.environ["TEST_DATABASE_URL"]).set(drivername="postgresql+psycopg")
        schema = "test_bhashasetu_" + uuid.uuid4().hex
        # Only this generated identifier is interpolated; no user input in SQL.
        engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        scoped = create_engine(url, hide_parameters=True, connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 5})
        try:
            with scoped.begin() as connection:
                migrate(connection)
                self.assertEqual(compare_metadata(MigrationContext.configure(connection), Base.metadata), [])
            factory = sessionmaker(scoped, expire_on_commit=False)
            first, second = seed_database(factory), seed_database(factory)
            self.assertEqual((first.created, first.rules_created, second.skipped, second.rules_created), (67, 217, 67, 0))
            with factory.begin() as session:
                service = SchemeService(session)
                with self.assertRaises(RuleValidationError):
                    service.create_with_rules(name="Invalid", rules=[rule(), rule(operator="BAD")])
                self.assertEqual(session.scalar(select(func.count()).select_from(Scheme)), 67)
                scheme = service.create_with_rules(name="JSON test", rules=[
                    rule("state", "IN", ["Bihar", "Uttar Pradesh"], "string"),
                    rule("annual_income", "<=", "200000.25", "decimal"),
                    rule("is_disabled", "==", True, "boolean")])
                scheme_id = scheme.id
            with factory() as session:
                results = EligibilityService(session).evaluate_all_schemes(
                    {"age": 64, "annual_income": "200000.25", "state": "Bihar", "is_disabled": True})
                self.assertEqual(next(result.status for result in results if result.scheme_id == scheme_id), "ELIGIBLE")
                self.assertIsNotNone(SchemeRepository(session).get_by_id(scheme_id).created_at.tzinfo)
            with factory.begin() as session:
                session.delete(session.get(Scheme, scheme_id))
            with factory() as session:
                self.assertEqual(session.scalar(select(func.count()).select_from(EligibilityRule).where(EligibilityRule.scheme_id == scheme_id)), 0)
            from backend.db.import_dataset import import_dataset
            researched, repeated = import_dataset(factory), import_dataset(factory)
            self.assertEqual((researched["active_schemes_after"], researched["active_rules_after"]), (297, 610))
            self.assertEqual((researched["legacy_archived"], repeated["created"], repeated["rules_created"]), (67, 0, 0))
            with scoped.begin() as connection:
                config = Config(str(ROOT / "alembic.ini"))
                config.attributes["connection"] = connection
                command.downgrade(config, "base")
                self.assertIsNone(connection.scalar(text("SELECT to_regclass('schemes')")))
                migrate(connection)
        finally:
            scoped.dispose()
            with engine.begin() as connection:
                connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            engine.dispose()
