"""Validated profile updates and slot filling; decisions belong to the rules engine."""
import logging
from dataclasses import replace

from pydantic import ValidationError

from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import ProfileExtractionError, ProfileExtractor
from backend.services.session_store import SessionStore
from backend.services.user_response import next_question, present_results, attributes, QUESTIONS, SENSITIVE

logger = logging.getLogger(__name__)

def process_turn(user_text: str, session_id: str, service: EligibilityService,
                 extractor: ProfileExtractor, store: SessionStore, *, skipped_fields=(),
                 answer_field=None, skip=False) -> dict:
    state = store.get(session_id)
    skipped_fields = set(skipped_fields)
    if answer_field in QUESTIONS and user_text.strip().casefold().rstrip("।.!?") in {
        "skip", "skip this", "पता नहीं", "नहीं पता", "नहीं बताना", "अभी नहीं बताना",
        "prefer not to say", "i don't know", "pata nahi", "pata nahin",
    }:
        skip = True
        skipped_fields.add(answer_field)
    try:
        if skip:
            # A skip is not an applicant fact. It must never be extracted as false.
            store.touch(session_id)
        else:
            if answer_field in QUESTIONS:
                updates = extractor.extract_profile(user_text, state.profile, question=QUESTIONS[answer_field])
            else:
                updates = extractor.extract_profile(user_text, state.profile)
            state = store.merge(session_id, updates)
    except (ProfileExtractionError, ValidationError):
        logger.warning("conversation_extraction_retry")
        # Do not publish stale decisions as if this utterance had been understood.
        # No values, attempt counter or finalized flag change on failure.
        return {
            "user_text": user_text,
            "ai_text": "मैं आपकी जानकारी समझ नहीं पाया। कृपया दोबारा बताएं।",
            "schemes": [], "eligibility_results": [], "missing_fields": [],
            "retry": True,
            "session": state.response_dict(), "result_cards": [],
            "skipped_fields": sorted(skipped_fields),
            "next_question": {"field": answer_field, "text": QUESTIONS[answer_field],
                              "optional": True, "sensitive": answer_field in SENSITIVE, "scheme_names": []}
            if answer_field in QUESTIONS else None,
        }

    results = service.evaluate_all_schemes(attributes(state.profile))
    schemes = [result.scheme_name for result in results if result.status == "ELIGIBLE"]
    pending = [result for result in results if result.status == "NEED_MORE_INFORMATION"]
    question = next_question(results, state.profile, skipped_fields)
    if question:
        ai_text = question["text"]
    elif schemes:
        ai_text = "दी गई जानकारी के आधार पर कुछ योजनाएँ आपके लिए हैं। नीचे योजनाएँ और कारण देखें।"
    elif pending:
        ai_text = "आपकी जानकारी के आधार पर संभावित योजनाएँ नीचे हैं। हर योजना के कारण और बाकी शर्तें देख सकते हैं।"
    else:
        ai_text = "दी गई जानकारी से अभी कोई योजना मेल नहीं खाती। नीचे कारण देखें; आप अपनी जानकारी बदल भी सकते हैं।"
    if not pending:
        store.mark_finalized(session_id, state.attempts)
        state = replace(state, finalized=True)
    return {
        "user_text": user_text, "ai_text": ai_text, "schemes": schemes,
        "eligibility_results": [result.model_dump(mode="json") for result in results],
        "missing_fields": [question["field"]] if question else [],
        "next_question": question, "result_cards": present_results(results),
        "skipped_fields": sorted(skipped_fields),
        "session": state.response_dict(),
    }
