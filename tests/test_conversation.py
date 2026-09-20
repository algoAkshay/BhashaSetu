"""Slot filling with fake extraction and the unchanged real deterministic engine."""
from unittest.mock import Mock, patch

import pytest

from backend.db.seed import seed_database
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.conversation_service import process_turn
from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import ProfileExtractor, ProfileExtractionError
from backend.services.session_store import InMemorySessionStore
from tests.support import test_database as create_test_database


@pytest.fixture
def conversation():
    engine, factory = create_test_database()
    seed_database(factory)
    store = InMemorySessionStore()
    extractor = Mock(spec=ProfileExtractor)
    with factory() as session:
        yield EligibilityService(session), extractor, session, store
    engine.dispose()


def turn(conversation, age=None, gender=None, income=None, text="test utterance", sid="slots"):
    service, extractor, _, store = conversation
    extractor.extract_profile.return_value = ProfileExtractionResult(
        age=age, gender=gender, annual_income=income)
    return process_turn(text, sid, service, extractor, store)


def test_complete_first_turn_matches_direct_eligibility(conversation):
    result = turn(conversation, 25, "Male", 5000,
                  "मैं पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है")
    direct = conversation[0].evaluate_all_schemes({"age": 25, "gender": "Male", "annual_income": 5000})
    assert result["eligibility_results"] == [r.model_dump(mode="json") for r in direct]
    assert result["schemes"] == [r.scheme_name for r in direct if r.status == "ELIGIBLE"]
    assert result["session"]["finalized"]
    assert result["missing_fields"] == []
    assert len(direct) == 67 and sum(len(r.checks) for r in direct) == 217


def test_income_only_prompt_and_nonnull_merge(conversation):
    first = turn(conversation, 25, "Male")
    assert first["missing_fields"] == ["annual_income"]
    assert "सालाना आय" in first["ai_text"]
    assert "उम्र" not in first["ai_text"] and "लिंग" not in first["ai_text"]
    second = turn(conversation, income=100000)
    assert second["session"] == dict(age=25, gender="Male", income=100000, attempts=2, finalized=True)
    assert conversation[1].extract_profile.call_args.args[1] == UserProfile(age=25, gender="Male")


def test_age_gender_income_in_three_turns(conversation):
    first = turn(conversation, age=65)
    assert first["missing_fields"] == ["annual_income"]
    second = turn(conversation, gender="Female")
    assert second["missing_fields"] == ["annual_income"]
    third = turn(conversation, income=100000)
    assert third["session"]["age"] == 65
    assert third["session"]["gender"] == "Female"
    assert third["session"]["finalized"]


def test_nulls_never_erase_or_fabricate(conversation):
    first = turn(conversation)
    assert all(first["session"][f] is None for f in ["age", "gender", "income"])
    turn(conversation, age=25)
    third = turn(conversation)
    assert third["session"]["age"] == 25
    assert third["session"]["income"] is None
    assert third["session"]["gender"] is None
    assert not third["session"]["finalized"]


def test_corrections_after_finalization_preserve_other_fields(conversation):
    turn(conversation, 25, "Male", 100000)
    second = turn(conversation, age=26, text="नहीं मेरी उम्र 26 है")
    assert second["session"]["age"] == 26
    assert second["session"]["income"] == 100000
    third = turn(conversation, income=200000, text="income one lakh nahi two lakh hai")
    assert third["session"]["age"] == 26
    assert third["session"]["gender"] == "Male"
    assert third["session"]["income"] == 200000
    assert third["session"]["finalized"]


def test_invalid_update_cannot_partially_mutate_session(conversation):
    turn(conversation, 25, "Male", 100000)
    before = conversation[3].get("slots").response_dict()
    # Even a provider violating the typed contract cannot sneak unvalidated data in.
    conversation[1].extract_profile.return_value = ProfileExtractionResult.model_construct(
        age=-20, gender="Female", annual_income=200000)
    result = process_turn("bad response", "slots", *conversation[:2], conversation[3])
    assert result["session"] == before
    assert result["eligibility_results"] == []
    assert "दोबारा" in result["ai_text"]


def test_provider_failure_preserves_state_and_does_not_evaluate(conversation):
    turn(conversation, age=25)
    before = conversation[3].get("slots").response_dict()
    conversation[1].extract_profile.side_effect = ProfileExtractionError("provider_unavailable")
    with patch.object(conversation[0], "evaluate_all_schemes") as evaluate:
        result = process_turn("failed", "slots", *conversation[:2], conversation[3])
    evaluate.assert_not_called()
    assert result["session"] == before
    assert result["schemes"] == []


def test_missing_slots_follow_active_rules_not_static_checklist(conversation):
    service, _, session, _ = conversation
    schemes = service.schemes.get_active_schemes()
    for scheme in schemes[1:]:
        scheme.is_active = False
    first = schemes[0]
    for rule in list(first.rules):
        if rule.field != "age":
            first.rules.remove(rule)
    session.flush()
    result = turn(conversation, age=25)
    assert result["missing_fields"] == []
    assert result["session"]["gender"] is None and result["session"]["income"] is None
    assert result["session"]["finalized"]


def test_no_rules_remains_unresolved(conversation):
    service, _, session, _ = conversation
    schemes = service.schemes.get_active_schemes()
    for scheme in schemes[1:]:
        scheme.is_active = False
    schemes[0].rules.clear()
    session.flush()
    result = turn(conversation, 25, "Male", 100000)
    assert result["eligibility_results"][0]["reason"] == "no_rules"
    assert not result["session"]["finalized"]
    assert any("नियम" in reason for reason in result["result_cards"][0]["reasons"])


def test_sessions_stay_isolated_and_regex_is_not_called(conversation):
    with patch("backend.logic.extract_fields", side_effect=AssertionError("legacy path called")):
        turn(conversation, age=25)
        other = turn(conversation, gender="Female", sid="other")
    assert other["session"]["age"] is None
    assert conversation[3].get("slots").profile.gender is None


def test_zero_is_known_not_missing(conversation):
    result = turn(conversation, 0, "Male", 0)
    assert result["session"]["age"] == 0
    assert result["session"]["income"] == 0
    assert result["missing_fields"] == []
