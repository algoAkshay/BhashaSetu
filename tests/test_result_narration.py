from copy import deepcopy

import pytest

from backend.schemas import EligibilityResult, RuleCheck
from backend.services.user_response import (
    SPOKEN_SCHEME_LIMIT, present_results, result_narration,
)


def card(name="Student support", status="ELIGIBLE", reasons=None, manual=None):
    return {"scheme_name": name, "status": status,
            "reasons": ["आयु सीमा: मेल खाता है"] if reasons is None else reasons,
            "manual_conditions": manual or []}


def response(cards, **extra):
    return {"ai_text": "नीचे योजनाएँ और कारण देखें।", "next_question": None,
            "result_cards": cards, **extra}


def test_question_and_retry_are_spoken_unchanged_even_with_results():
    question = "आपकी उम्र कितनी है?"
    assert result_narration(response([card()], ai_text=question, next_question={"field": "age"})) == question
    assert result_narration(response([card()], ai_text="दोबारा बताएँ।", retry=True)) == "दोबारा बताएँ।"


@pytest.mark.parametrize("count", [1, 2, 3])
def test_small_results_speak_total_all_names_and_existing_reason(count):
    cards = [card(f"Scheme {i}") for i in range(count)]
    spoken = result_narration(response(cards))
    assert f"{count} योजनाओं" in spoken
    for item in cards:
        assert item["scheme_name"] in spoken
    assert cards[0]["reasons"][0] in spoken
    assert spoken.endswith("इन योजनाओं की पूरी जानकारी स्क्रीन पर उपलब्ध है।")


def test_pending_is_not_announced_as_eligible_and_manual_note_is_scheme_specific():
    cards = [card("Confirmed"), card("Possible", "NEED_MORE_INFORMATION", manual=["Registered construction worker status"]),
             card("Excluded", "NOT_ELIGIBLE", manual=["Excluded private condition"])]
    spoken = result_narration(response(cards))
    assert "1 योजनाओं में आप पात्र" in spoken
    assert "1 संभावित योजनाओं की पात्रता अभी तय नहीं है" in spoken
    assert spoken.index("Possible") < spoken.index("Registered construction worker status")
    assert "Excluded" not in spoken and "Excluded private condition" not in spoken
    assert "अतिरिक्त शर्त" not in result_narration(response([card()]))


def test_only_pending_results_are_explicitly_uncertain():
    spoken = result_narration(response([card("Pending", "NEED_MORE_INFORMATION")]))
    assert "1 संभावित योजनाओं" in spoken
    assert "आप पात्र दिखाई देते हैं" not in spoken


def test_many_results_keep_existing_order_and_bounded_notes():
    names = ["Zulu", "Alpha", "Middle"] + [f"Unspoken-{i}" for i in range(41)]
    cards = [card(name, manual=["Condition " * 500, "SECOND-CONDITION"]) for name in names]
    spoken = result_narration(response(cards))
    assert "44 योजनाओं" in spoken
    assert f"पहले {SPOKEN_SCHEME_LIMIT} परिणाम" in spoken
    assert "बाकी 41 परिणाम" in spoken
    assert spoken.index("Zulu") < spoken.index("Alpha") < spoken.index("Middle")
    assert "Unspoken-" not in spoken and "SECOND-CONDITION" not in spoken
    assert "शर्त का अंश" in spoken and "पूरी शर्तें" in spoken
    assert len(spoken) < 2500


def test_arbitrarily_long_admin_text_cannot_make_unbounded_audio():
    cards = [card("Name " * 2000, reasons=["Reason " * 2000], manual=["Condition " * 2000]) for _ in range(100)]
    assert len(result_narration(response(cards))) < 3000


def test_missing_reason_is_not_invented_and_no_candidates_use_existing_text():
    assert "मेल खाता" not in result_narration(response([card(reasons=[])]))
    for cards in [[], [card(status="NOT_ELIGIBLE")]]:
        original = response(cards)
        assert result_narration(original) == original["ai_text"]


def test_narration_consumes_existing_engine_presentation_without_mutating_decisions():
    result = EligibilityResult(status="NEED_MORE_INFORMATION", scheme_id=1, scheme_name="Existing result",
        checks=[RuleCheck(field="age", actual=24, operator=">=", expected=18, passed=True)],
        manual_conditions=["Original certificate required"], reason="manual_review_required", missing_fields=[])
    payload = response(present_results([result]))
    before = deepcopy(payload)
    spoken = result_narration(payload)
    assert payload == before
    assert payload["result_cards"][0]["reasons"][0] in spoken
    assert "Original certificate required" in spoken
    # Formatter accepts presentation-only cards: no profile, rules, DB or model needed.
    assert result.status == "NEED_MORE_INFORMATION"
