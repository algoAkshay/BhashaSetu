import csv
from pathlib import Path
import shutil

import pytest
from sqlalchemy import select

from backend.admin_schemas import SchemePatch
from backend.db.import_dataset import DEFAULT_DIRECTORY, import_dataset
from backend.models import Scheme, EligibilityRule
from backend.services.admin_service import AdminService
from tests.support import create_test_database


@pytest.fixture
def catalogue(tmp_path):
    for file in DEFAULT_DIRECTORY.glob('*.csv'):
        shutil.copyfile(file, tmp_path / file.name)
    engine, factory = create_test_database()
    yield factory, tmp_path
    engine.dispose()


def test_identical_import_remains_idempotent(catalogue):
    factory, directory = catalogue
    first = import_dataset(factory, directory)
    second = import_dataset(factory, directory)
    assert (first['created'], first['rules_created']) == (297, 610)
    assert (second['created'], second['updated'], second['unchanged']) == (0, 0, 297)
    assert second['conflicts'] == []


def test_changed_source_is_reported_and_requires_explicit_overwrite(catalogue):
    factory, directory = catalogue
    import_dataset(factory, directory)
    path = directory / 'central_schemes_master.csv'
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    name = rows[0]['scheme_name']
    original = rows[0]['short_description']
    rows[0]['short_description'] = 'Changed source fixture, not repository scheme data.'
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    report = import_dataset(factory, directory)
    assert report['skipped_updates'] == 1
    assert 'description' in report['conflicts'][0]['fields']
    with factory() as session:
        scheme = session.scalar(select(Scheme).where(Scheme.name == name))
        assert scheme.description == original
    report = import_dataset(factory, directory, overwrite_existing=True)
    assert report['updated'] == 1
    with factory() as session:
        assert session.scalar(select(Scheme).where(Scheme.name == name)).description == rows[0]['short_description']


def test_admin_metadata_activation_and_rule_deletion_are_preserved(catalogue):
    factory, directory = catalogue
    import_dataset(factory, directory)
    with factory.begin() as session:
        scheme = session.scalar(select(Scheme).order_by(Scheme.id))
        scheme_id = scheme.id
        admin = AdminService(session)
        admin.update_scheme(scheme_id, SchemePatch(description='Admin fixture', is_active=False))
        rule = session.scalar(select(EligibilityRule).where(EligibilityRule.scheme_id == scheme_id))
        rule_id = rule.id
        admin.delete_rule(scheme_id, rule_id)
    report = import_dataset(factory, directory)
    assert report['skipped_updates'] == 1
    assert report['conflicts'][0]['rules_differ']
    with factory() as session:
        scheme = session.get(Scheme, scheme_id)
        assert scheme.description == 'Admin fixture' and not scheme.is_active
        assert session.get(EligibilityRule, rule_id) is None
