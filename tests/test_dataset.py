"""Supplied CSVs, real migrations/rules, offline provider and storage contracts."""

import csv
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from backend.config import LLMSettings, SessionSettings
from backend.db.import_dataset import (
    DEFAULT_DIRECTORY,
    DatasetError,
    import_dataset,
    load_dataset,
    parse_rule,
)
from backend.db.seed import seed_database
from backend.models import Base, EligibilityRule, Scheme
from backend.profile_schemas import (
    ProfileExtractionResult,
    UserProfile,
)
from backend.rules import evaluate_scheme
from backend.schemas import RuleDefinition
from backend.server import app
from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import GeminiProfileExtractor
from backend.services.session_store import (
    InMemorySessionStore,
    RedisSessionStore,
)
from tests.support import create_test_database as create_database, migrate


@pytest.fixture
def database():
    engine, factory = create_database()

    yield engine, factory

    engine.dispose()


@pytest.fixture
def copied_dataset(tmp_path):
    for group in (
        "central",
        "state",
    ):
        for suffix in (
            "schemes_master",
            "eligibility_rules",
            "needs_review",
        ):
            name = f"{group}_{suffix}.csv"

            shutil.copyfile(
                DEFAULT_DIRECTORY / name,
                tmp_path / name,
            )

    return tmp_path


def edit_csv(path, edit):
    with path.open(
        encoding="utf-8-sig",
        newline="",
    ) as stream:
        reader = csv.DictReader(stream)

        columns = reader.fieldnames
        rows = list(reader)

    edit(rows)

    with path.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=columns,
        )

        writer.writeheader()
        writer.writerows(rows)


def test_supplied_dataset_audit():
    entries, report = load_dataset()

    assert (
        report["central"],
        report["state"],
        report["unique_schemes"],
    ) == (
        79,
        218,
        297,
    )

    assert report["source_rules"] == {
        "central": 227,
        "state": 448,
    }

    assert report["structured_rules"] == 610

    assert (
        len(
            report[
                "rules_preserved_for_review"
            ]
        )
        == 65
    )

    assert report["review_rows"] == 5

    assert report["issues"] == []

    assert (
        report["duplicate_schemes"]
        == report["duplicate_rules"]
        == 0
    )

    # All supplied production schemes currently contain
    # at least one manually-verifiable condition.
    assert all(
        entry.manual
        for entry in entries.values()
    )


def test_import_twice_and_archive_legacy_without_losing_records(
    database,
):
    _, factory = database

    seed_database(factory)

    first = import_dataset(factory)
    second = import_dataset(factory)

    assert (
        first["schemes_before"],
        first["schemes_after"],
        first["active_schemes_after"],
    ) == (
        67,
        364,
        297,
    )

    assert first["legacy_archived"] == 67

    assert (
        first["rules_created"],
        first["active_rules_after"],
        first["all_stored_rules"],
    ) == (
        610,
        610,
        827,
    )

    assert (
        second["created"],
        second["updated"],
        second["unchanged"],
        second["rules_created"],
    ) == (
        0,
        0,
        297,
        0,
    )

    with factory() as session:
        legacy = session.scalars(
            select(Scheme).where(
                Scheme.source_key.startswith(
                    "schemes.csv:"
                )
            )
        ).all()

        assert len(legacy) == 67

        assert all(
            not scheme.is_active
            for scheme in legacy
        )

        assert (
            len(
                session.scalars(
                    select(
                        EligibilityRule
                    )
                ).all()
            )
            == 827
        )


def test_clean_import_counts_and_source_provenance(
    database,
):
    _, factory = database

    report = import_dataset(factory)

    assert (
        report["schemes_before"],
        report["schemes_after"],
        report["all_stored_rules"],
    ) == (
        0,
        297,
        610,
    )

    with factory() as session:
        scheme = session.scalars(
            select(Scheme).where(
                Scheme.government_level
                == "CENTRAL"
            )
        ).first()

        master = (
            scheme.source_metadata[
                "master"
            ]
        )

        assert (
            scheme.official_url
            == master[
                "official_source_url"
            ]
        )

        assert (
            scheme.raw_category
            == master["category"]
        )

        assert (
            scheme.confidence_notes
            == master[
                "confidence_notes"
            ]
        )

        assert (
            scheme.last_checked_date.isoformat()
            == master[
                "last_checked_date"
            ]
        )

        rule = session.scalars(
            select(
                EligibilityRule
            ).where(
                EligibilityRule.scheme_id
                == scheme.id
            )
        ).first()

        assert (
            rule.source_metadata[
                "scheme_name"
            ]
            == scheme.name
        )

        schemes = session.scalars(
            select(Scheme)
        ).all()

        assert (
            sum(
                len(
                    item.source_metadata[
                        "unresolved_rules"
                    ]
                )
                for item in schemes
            )
            == 65
        )


def test_duplicates_are_removed_without_count_inflation(
    copied_dataset,
    database,
):
    for suffix in (
        "schemes_master",
        "eligibility_rules",
    ):
        edit_csv(
            copied_dataset
            / f"central_{suffix}.csv",
            lambda rows: rows.append(
                rows[0].copy()
            ),
        )

    _, factory = database

    first = import_dataset(
        factory,
        copied_dataset,
    )

    second = import_dataset(
        factory,
        copied_dataset,
    )

    assert (
        first["duplicate_schemes"]
        == first["duplicate_rules"]
        == 1
    )

    assert (
        first["active_schemes_after"]
        == second[
            "active_schemes_after"
        ]
        == 297
    )

    assert (
        first["active_rules_after"]
        == second[
            "active_rules_after"
        ]
        == 610
    )


def test_changed_source_updates_in_place_with_explicit_overwrite(
    copied_dataset,
    database,
):
    _, factory = database

    import_dataset(
        factory,
        copied_dataset,
    )

    with factory() as session:
        original_id = session.scalars(
            select(Scheme).where(
                Scheme.government_level
                == "CENTRAL"
            )
        ).first().id

    edit_csv(
        copied_dataset
        / "central_schemes_master.csv",
        lambda rows: rows[0].update(
            benefit_summary=(
                "Corrected supplied summary"
            )
        ),
    )

    report = import_dataset(
        factory,
        copied_dataset,
        overwrite_existing=True,
    )

    assert report["created"] == 0
    assert report["updated"] == 1

    with factory() as session:
        assert (
            session.get(
                Scheme,
                original_id,
            ).benefit_summary
            == "Corrected supplied summary"
        )


def test_malformed_and_orphan_rules_are_reported_without_losing_valid_rows(
    copied_dataset,
):
    def mutate(rows):
        rows.append(
            {
                **rows[0],
                "scheme_name":
                    "Missing scheme",
            }
        )

        rows.append(
            {
                **rows[0],
                "field": "age",
                "operator": ">=",
                "value":
                    "not a number",
                "value_type":
                    "number",
            }
        )

    edit_csv(
        copied_dataset
        / "central_eligibility_rules.csv",
        mutate,
    )

    entries, report = load_dataset(
        copied_dataset
    )

    assert len(entries) == 297

    assert any(
        issue["reason"]
        == "missing_scheme_reference"
        for issue in report["issues"]
    )

    assert (
        len(
            report[
                "rules_preserved_for_review"
            ]
        )
        == 66
    )

    with (
        copied_dataset
        / "central_eligibility_rules.csv"
    ).open(
        "a",
        encoding="utf-8",
    ) as stream:
        stream.write(
            "broken,column,row\n"
        )

    entries, report = load_dataset(
        copied_dataset
    )

    assert any(
        issue["reason"]
        == "malformed_columns"
        for issue in report["issues"]
    )

    assert all(
        any(
            "Malformed rows"
            in note
            for note in entry.manual
        )
        for key, entry
        in entries.items()
        if key[0] == "CENTRAL"
    )


def test_missing_columns_abort_before_database_writes(
    copied_dataset,
    database,
):
    (
        copied_dataset
        / "central_schemes_master.csv"
    ).write_text(
        "scheme_name\nExample\n",
        encoding="utf-8",
    )

    with pytest.raises(
        DatasetError
    ):
        import_dataset(
            database[1],
            copied_dataset,
        )

    with database[1]() as session:
        assert (
            session.scalars(
                select(Scheme)
            ).all()
            == []
        )


def test_family_income_is_not_individual_income(
    database,
):
    _, factory = database

    import_dataset(factory)

    with factory() as session:
        results = (
            EligibilityService(
                session
            ).evaluate_all_schemes(
                {
                    "state_or_ut":
                        "Uttar Pradesh",
                    "gender":
                        "Female",
                    "annual_income":
                        1000,
                }
            )
        )

        result = next(
            result
            for result in results
            if result.scheme_name
            == (
                "Mukhyamantri "
                "Kanya Sumangala Yojana"
            )
        )

        assert (
            "family_annual_income"
            in result.missing_fields
        )

        assert (
            "annual_income"
            not in result.missing_fields
        )

        assert (
            result.status
            == "NEED_MORE_INFORMATION"
        )


def test_state_filtering_and_central_rules_remain_explainable(
    database,
):
    _, factory = database

    import_dataset(factory)

    with factory() as session:
        service = EligibilityService(
            session
        )

        schemes = {
            scheme.id: scheme
            for scheme
            in service.schemes
            .get_active_schemes()
        }

        results = service.evaluate_all_schemes(
            {
                "state_or_ut":
                    "Uttar Pradesh",
                "age":
                    25,
                "gender":
                    "Female",
            }
        )

        for result in results:
            scheme = schemes[
                result.scheme_id
            ]

            if (
                scheme.government_level
                == "STATE"
                and scheme.state
                != "Uttar Pradesh"
            ):
                assert (
                    result.status
                    == "NOT_ELIGIBLE"
                )

                assert any(
                    check.field
                    == "state_or_ut"
                    and check.passed
                    is False
                    for check
                    in result.checks
                )

            assert (
                result.manual_conditions
            )

        assert any(
            schemes[
                result.scheme_id
            ].government_level
            == "CENTRAL"
            and result.status
            == "NEED_MORE_INFORMATION"
            for result in results
        )

        # With the supplied dataset every scheme has unresolved
        # manual conditions, therefore no result should be
        # conclusively ELIGIBLE from this partial profile.
        assert all(
            result.status
            != "ELIGIBLE"
            for result in results
        )


@pytest.mark.parametrize(
    "field,kind,value",
    [
        (
            "age",
            "integer",
            25,
        ),
        (
            "gender",
            "string",
            "Female",
        ),
        (
            "annual_income",
            "decimal",
            100000,
        ),
        (
            "family_annual_income",
            "decimal",
            200000,
        ),
        (
            "individual_monthly_income",
            "decimal",
            10000,
        ),
        (
            "social_category",
            "string",
            "SC",
        ),
        (
            "student_status",
            "boolean",
            True,
        ),
        (
            "farmer_status",
            "boolean",
            True,
        ),
        (
            "disability_status",
            "boolean",
            False,
        ),
        (
            "disability_percentage",
            "decimal",
            40,
        ),
        (
            "bpl_status",
            "boolean",
            False,
        ),
        (
            "rural_urban",
            "string",
            "Rural",
        ),
        (
            "minority_status",
            "boolean",
            True,
        ),
    ],
)
def test_generic_rules_with_expanded_fields(
    field,
    kind,
    value,
):
    scheme = SimpleNamespace(
        id=1,
        name="Fixture",
        is_active=True,
        manual_conditions=[],
    )

    definition = RuleDefinition(
        field=field,
        operator="==",
        value=value,
        value_type=kind,
    )

    result = evaluate_scheme(
        scheme,
        [definition],
        {field: value},
    )

    assert (
        result.status
        == "ELIGIBLE"
    )

    assert (
        result.checks[0].passed
        is True
    )

    unknown = evaluate_scheme(
        scheme,
        [definition],
        {},
    )

    assert (
        unknown.status
        == "NEED_MORE_INFORMATION"
    )

    assert (
        unknown.missing_fields
        == [field]
    )

    # All machine-checkable conditions pass, but an additional
    # condition still requires external/manual verification.
    scheme.manual_conditions = [
        (
            "Registration duration "
            "must be confirmed manually"
        )
    ]

    manual_result = evaluate_scheme(
        scheme,
        [definition],
        {field: value},
    )

    assert (
        manual_result.status
        == "POTENTIALLY_ELIGIBLE"
    )

    assert (
        manual_result.reason
        == "manual_review_required"
    )


def test_boolean_parsing_and_complex_rule_policy():
    definition = parse_rule(
        {
            "field":
                "student_status",
            "operator":
                "boolean_equals",
            "value":
                "FALSE",
        },
        {},
    )

    assert (
        definition.value
        is False
    )

    with pytest.raises(
        ValueError
    ):
        parse_rule(
            {
                "field":
                    "student_status",
                "operator":
                    "boolean_equals",
                "value":
                    "SC students only",
            },
            {},
        )

    with pytest.raises(
        ValueError
    ):
        parse_rule(
            {
                "field":
                    "social_category",
                "operator":
                    "in",
                "value":
                    (
                        "SC|ST|other "
                        "eligible categories"
                    ),
            },
            {},
        )


def test_redis_roundtrip_new_fields_and_false_values():
    client = MagicMock()

    pipe = (
        client.pipeline
        .return_value
        .__enter__
        .return_value
    )

    raw = {
        "state_or_ut":
            "Bihar",
        "family_annual_income":
            "150000",
        "student_status":
            "false",
        "farmer_status":
            "true",
        "social_category":
            "SC",
        "disability_percentage":
            "40.5",
    }

    pipe.execute.return_value = [
        raw,
        True,
    ]

    store = RedisSessionStore(
        client,
        SessionSettings(),
    )

    profile = store.get(
        "abc"
    ).profile

    assert (
        profile.student_status
        is False
    )

    assert (
        profile.farmer_status
        is True
    )

    assert (
        profile.family_annual_income
        == 150000
    )

    assert (
        profile.disability_percentage
        == 40.5
    )

    update = ProfileExtractionResult(
        age=None,
        gender=None,
        annual_income=None,
        student_status=False,
    )

    pipe.execute.return_value = [
        1,
        1,
        True,
        raw,
    ]

    store.merge(
        "abc",
        update,
    )

    assert (
        pipe.hset.call_args.kwargs[
            "mapping"
        ]
        == {
            "student_status":
                "false",
            "_finalized":
                "0",
        }
    )

    memory = (
        InMemorySessionStore()
    )

    memory.merge(
        "abc",
        ProfileExtractionResult(
            age=25,
            gender=None,
            annual_income=None,
            social_category="SC",
        ),
    )

    state = memory.merge(
        "abc",
        update,
    )

    assert (
        state.profile.social_category
        == "SC"
    )

    assert (
        state.profile.student_status
        is False
    )


def test_expanded_gemini_schema_with_offline_mock():
    fields = {
        "age": None,
        "gender": None,
        "annual_income": None,
        "state_or_ut": "Bihar",
        "social_category": "SC",
        "disability_status": True,
        "disability_percentage": 40,
        "bpl_status": False,
        "family_annual_income":
            150000,
    }

    with patch(
        "google.genai.Client"
    ) as constructor:
        client = (
            constructor
            .return_value
            .__enter__
            .return_value
        )

        client.models.generate_content.return_value.text = (
            json.dumps(
                fields
            )
        )

        result = GeminiProfileExtractor(
            LLMSettings(
                api_key="offline"
            )
        ).extract_profile(
            (
                "I live in Bihar, explicitly belong to SC, "
                "have 40 percent disability, am not BPL; "
                "family annual income is 150000"
            )
        )

    assert (
        result.social_category
        == "SC"
    )

    assert (
        result.bpl_status
        is False
    )

    assert (
        result.widow_status
        is None
    )

    assert (
        result.minority_status
        is None
    )

    assert (
        result.annual_income
        is None
    )

    assert (
        result.family_annual_income
        == 150000
    )

    with pytest.raises(
        ValidationError
    ):
        UserProfile(
            disability_percentage=101
        )

    with pytest.raises(
        ValidationError
    ):
        UserProfile(
            bpl_status="false"
        )


def test_curated_api_metadata_and_uncertainty_contract(
    database,
):
    _, factory = database

    import_dataset(factory)

    app.state.session_factory = (
        factory
    )

    try:
        with TestClient(app) as client:
            schemes = client.get(
                "/api/schemes"
            ).json()

            assert (
                len(schemes)
                == 297
            )

            assert all(
                scheme[
                    "government_level"
                ]
                in {
                    "CENTRAL",
                    "STATE",
                }
                for scheme
                in schemes
            )

            assert all(
                "manual_conditions"
                in scheme
                and "official_url"
                in scheme
                for scheme
                in schemes
            )

            results = client.post(
                "/api/eligibility",
                json={
                    "attributes": {
                        "state_or_ut":
                            "Bihar",
                        "student_status":
                            True,
                    }
                },
            ).json()

            assert (
                len(results)
                == 297
            )

            assert all(
                result[
                    "manual_conditions"
                ]
                for result
                in results
            )

    finally:
        del app.state.session_factory


def test_migration_metadata_and_upgrade_preserve_legacy(
    database,
):
    engine, factory = database

    # Round-trip to the prior schema,
    # seed actual legacy records,
    # then upgrade safely.
    with engine.begin() as connection:
        from alembic import command
        from alembic.config import Config

        config = Config(
            str(
                Path(
                    __file__
                ).resolve().parents[1]
                / "alembic.ini"
            )
        )

        config.attributes[
            "connection"
        ] = connection

        command.downgrade(
            config,
            "0001",
        )

        connection.exec_driver_sql(
            (
                "INSERT INTO schemes "
                "(id, name, is_active) "
                "VALUES "
                "(1, 'Preserved', 1)"
            )
        )

        migrate(connection)

        assert (
            compare_metadata(
                MigrationContext.configure(
                    connection
                ),
                Base.metadata,
            )
            == []
        )

    with factory() as session:
        assert (
            session.get(
                Scheme,
                1,
            ).name
            == "Preserved"
        )

        assert (
            session.get(
                Scheme,
                1,
            ).source_metadata
            is None
        )