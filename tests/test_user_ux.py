"""Deterministic UX tests; provider output fixtures do not claim live NLU accuracy."""
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import pytest
from sqlalchemy import func, select

from backend.config import LLMSettings, SessionSettings
from backend.models import Scheme, EligibilityRule
from backend.normalization import FIELD_TYPES
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.conversation_service import process_turn
from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import GeminiProfileExtractor, ProfileExtractor
from backend.services.scheme_service import SchemeService
from backend.services.session_store import InMemorySessionStore, RedisSessionStore
from backend.services.user_response import QUESTIONS, next_question, present_results
from tests.support import test_database as make_database


def update(**values):
    return ProfileExtractionResult(age=None, gender=None, annual_income=None).model_copy(update=values)


def rule(field, value, operator="=="):
    return {"field": field, "value": value, "operator": operator, "value_type": FIELD_TYPES[field]}


@pytest.fixture
def chat():
    engine, factory = make_database()
    with factory() as session:
        service = EligibilityService(session)
        extractor = Mock(spec=ProfileExtractor)
        store = InMemorySessionStore()

        def add(name="Student support", rules=None, **metadata):
            return SchemeService(session).create_with_rules(name=name, rules=rules or [
                rule("age", 18, ">="), rule("state_or_ut", "Uttar Pradesh"),
                rule("student_status", True), rule("family_annual_income", 300000, "<="),
            ], **metadata)

        def turn(text="मेरे बारे में", values=None, **options):
            extractor.extract_profile.return_value = update(**(values or {}))
            return process_turn(text, "ux", service, extractor, store, **options)

        yield add, turn, service, extractor, store
    engine.dispose()


def test_multi_fact_sentence_and_no_repeat(chat):
    add, turn, _, extractor, store = chat
    add()
    sentence = "मैं 24 साल का छात्र हूँ और उत्तर प्रदेश से हूँ।"
    result = turn(sentence, {"age": 24, "state_or_ut": "Uttar Pradesh", "student_status": True})
    assert result["missing_fields"] == ["family_annual_income"]
    assert result["ai_text"] == QUESTIONS["family_annual_income"]
    assert store.get("ux").profile.student_status is True
    assert extractor.extract_profile.call_args.args[0] == sentence
    second = turn("2 lakh", {"family_annual_income": 200000}, answer_field="family_annual_income")
    assert second["missing_fields"] == []
    assert second["session"]["age"] == 24 and second["session"]["state_or_ut"] == "Uttar Pradesh"
    assert second["session"]["income"] is None
    assert extractor.extract_profile.call_args.kwargs["question"] == QUESTIONS["family_annual_income"]


def test_missing_state_is_only_question(chat):
    add, turn, *_ = chat
    add()
    result = turn(values={"age": 24, "student_status": True, "family_annual_income": 200000})
    assert result["missing_fields"] == ["state_or_ut"]
    assert result["ai_text"] == "आप किस राज्य या केंद्र शासित प्रदेश में रहते हैं?"
    second = turn("उत्तर प्रदेश", {"state_or_ut": "Uttar Pradesh"}, answer_field="state_or_ut")
    assert second["schemes"] == ["Student support"]


def test_occupation_only_and_provided_user_type_not_reasked(chat):
    add, turn, *_ = chat
    add(rules=[rule("age", 18, ">="), rule("occupation", "Farmer")])
    result = turn(values={"age": 32})
    assert result["missing_fields"] == ["occupation"]
    assert result["ai_text"] == QUESTIONS["occupation"]
    result = turn(values={"student_status": True})
    assert result["next_question"] is None  # No invented occupation; result stays unresolved.
    assert result["eligibility_results"][0]["status"] == "NEED_MORE_INFORMATION"


@pytest.mark.parametrize("field,value", [("disability_status", True), ("farmer_status", True), ("education_level", "Graduate")])
def test_excluded_candidates_do_not_drive_questions(chat, field, value):
    add, turn, *_ = chat
    add(name="Excluded", rules=[rule("state_or_ut", "Bihar"), rule(field, value)])
    add(name="Local", rules=[rule("state_or_ut", "Maharashtra"), rule("age", 18, ">=")])
    result = turn(values={"state_or_ut": "Maharashtra", "age": 32})
    assert result["next_question"] is None
    assert result["schemes"] == ["Local"]


def test_student_false_excludes_education_question(chat):
    add, turn, *_ = chat
    add(rules=[rule("student_status", True), rule("education_level", "Graduate")])
    result = turn(values={"student_status": False})
    assert result["next_question"] is None
    assert result["eligibility_results"][0]["status"] == "NOT_ELIGIBLE"


def test_sensitive_relevance_then_skip_preserves_unknown(chat):
    add, turn, _, extractor, store = chat
    add(name="Relevant support", rules=[rule("age", 18, ">="), rule("disability_status", True)])
    first = turn()
    assert first["missing_fields"] == ["age"]
    second = turn(values={"age": 32})
    assert second["missing_fields"] == ["disability_status"]
    assert second["next_question"]["sensitive"] and second["next_question"]["optional"]
    assert second["next_question"]["scheme_names"] == ["Relevant support"]
    assert "यदि बताना चाहें" in second["ai_text"]
    extractor.reset_mock()
    before = store.get("ux")
    third = turn("पता नहीं", answer_field="disability_status")
    extractor.extract_profile.assert_not_called()
    assert store.get("ux") == before
    assert third["skipped_fields"] == ["disability_status"]
    assert third["next_question"] is None
    assert third["eligibility_results"][0]["status"] == "NEED_MORE_INFORMATION"
    assert turn(skipped_fields=third["skipped_fields"])["next_question"] is None


def test_sensitive_not_asked_without_positive_relevance(chat):
    add, turn, *_ = chat
    add(rules=[rule("social_category", "SC")])
    result = turn()
    assert result["next_question"] is None
    assert result["eligibility_results"][0]["status"] == "NEED_MORE_INFORMATION"


def test_disability_percentage_requires_explicit_disability(chat):
    add, turn, *_ = chat
    add(rules=[rule("age", 18, ">="), rule("disability_percentage", 40, ">=")])
    assert turn(values={"age": 32})["next_question"] is None
    assert turn(values={"disability_status": True})["missing_fields"] == ["disability_percentage"]


def test_correction_and_manual_conditions_stay_with_scheme(chat):
    add, turn, *_ = chat
    add(manual_conditions=["Registered construction worker status"])
    first = turn(values={"age": 24, "state_or_ut": "Uttar Pradesh", "student_status": True})
    assert "Registered" not in first["ai_text"] and "मैनुअल" not in first["ai_text"]
    assert first["result_cards"][0]["manual_conditions"] == ["Registered construction worker status"]
    second = turn("नहीं मेरी उम्र 26 है", {"age": 26})
    assert second["session"]["age"] == 26
    assert second["session"]["student_status"] is True
    assert second["session"]["state_or_ut"] == "Uttar Pradesh"
    assert "checks" not in second["result_cards"][0]


def test_many_missing_fields_still_one_short_question(chat):
    add, turn, *_ = chat
    add(rules=[rule("age", 18, ">="), rule("state_or_ut", "Bihar"), rule("occupation", "Farmer"),
               rule("student_status", True), rule("family_annual_income", 200000, "<="),
               rule("social_category", "SC"), rule("bpl_status", True), rule("minority_status", True)])
    result = turn()
    assert result["missing_fields"] == ["state_or_ut"]
    assert result["ai_text"] == QUESTIONS["state_or_ut"]
    assert all(len(question) <= 150 for question in QUESTIONS.values())


def test_skip_action_does_not_extract_or_write_profile(chat):
    add, turn, _, extractor, store = chat
    add()
    turn(values={"age": 24})
    before = store.get("ux")
    extractor.reset_mock()
    result = turn(skip=True, answer_field="state_or_ut", skipped_fields=["state_or_ut"])
    extractor.extract_profile.assert_not_called()
    assert store.get("ux") == before
    assert result["missing_fields"] != ["state_or_ut"]
    assert result["eligibility_results"][0]["status"] == "NEED_MORE_INFORMATION"


def test_gemini_receives_question_as_context_not_profile_fact():
    with patch("google.genai.Client") as constructor:
        model = constructor.return_value.__enter__.return_value.models
        model.generate_content.return_value.text = json.dumps(update(family_annual_income=200000).model_dump())
        result = GeminiProfileExtractor(LLMSettings(api_key="offline")).extract_profile(
            "2 lakh", UserProfile(age=24), question=QUESTIONS["family_annual_income"])
    sent = json.loads(model.generate_content.call_args.kwargs["contents"])
    assert sent["utterance"] == "2 lakh"
    assert sent["previous_question"] == QUESTIONS["family_annual_income"]
    assert sent["current_profile"]["family_annual_income"] is None
    assert result.annual_income is None and result.family_annual_income == 200000


def test_redis_prior_answers_survive_progressive_turn(chat):
    add, _, service, extractor, _ = chat
    add(manual_conditions=["Original document check"])
    client = MagicMock()
    pipe = client.pipeline.return_value.__enter__.return_value
    before = {"age": "24", "state_or_ut": "Uttar Pradesh", "student_status": "true", "_attempts": "1"}
    after = {**before, "family_annual_income": "200000", "_attempts": "2", "_finalized": "0"}
    pipe.execute.side_effect = [[before, True], [1, 2, True, after]]
    extractor.extract_profile.return_value = update(family_annual_income=200000)
    result = process_turn("2 lakh", "redis-ux", service, extractor,
                          RedisSessionStore(client, SessionSettings()), answer_field="family_annual_income")
    assert result["session"]["age"] == 24 and result["session"]["student_status"] is True
    assert result["session"]["family_annual_income"] == 200000
    assert pipe.hset.call_args.kwargs["mapping"] == {"family_annual_income": 200000, "_finalized": "0"}


def test_full_catalogue_counts_unchanged_by_conversation():
    from backend.db.seed import seed_database
    from backend.db.import_dataset import import_dataset
    engine, factory = make_database()
    try:
        seed_database(factory)
        import_dataset(factory)
        with factory() as session:
            def counts():
                return (session.scalar(select(func.count()).select_from(Scheme)),
                        session.scalar(select(func.count()).select_from(Scheme).where(Scheme.is_active.is_(True))),
                        session.scalar(select(func.count()).select_from(EligibilityRule)),
                        session.scalar(select(func.count()).select_from(EligibilityRule).join(Scheme).where(Scheme.is_active.is_(True))))
            assert counts() == (364, 297, 827, 610)
            extractor = Mock(spec=ProfileExtractor)
            extractor.extract_profile.return_value = update(age=24, student_status=True, family_annual_income=200000)
            response = process_turn("मैं 24 साल का छात्र हूँ", "catalogue", EligibilityService(session), extractor, InMemorySessionStore())
            assert response["missing_fields"] == ["state_or_ut"]
            assert len(response["result_cards"]) == 297
            extractor.extract_profile.return_value = update(age=32, state_or_ut="Maharashtra", gender="Female", annual_income=100000)
            second_example = process_turn("मैं महाराष्ट्र की 32 साल की महिला हूँ", "second-example",
                                          EligibilityService(session), extractor, InMemorySessionStore())
            assert second_example["missing_fields"] == ["occupation"]
            assert not second_example["next_question"]["sensitive"]
            assert counts() == (364, 297, 827, 610)
    finally:
        engine.dispose()


def test_protected_admin_data_engine_and_migrations_unchanged():
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads((root / "docs/user-ux-preserved-hashes.json").read_text(encoding="utf-8-sig"))
    for filename, expected in baseline.items():
        assert hashlib.sha256((root / filename).read_bytes()).hexdigest().upper() == expected, filename
