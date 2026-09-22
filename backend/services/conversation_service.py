"""Validated profile updates and slot filling; decisions belong to the rules engine."""

import logging
from dataclasses import replace

from pydantic import ValidationError

from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import (
    ProfileExtractionError,
    ProfileExtractor,
)
from backend.services.session_store import SessionStore
from backend.services.user_response import (
    QUESTIONS,
    SENSITIVE,
    attributes,
    next_question,
    present_results,
)

logger = logging.getLogger(__name__)


SKIP_PHRASES = {
    "skip",
    "skip this",
    "पता नहीं",
    "नहीं पता",
    "नहीं बताना",
    "अभी नहीं बताना",
    "prefer not to say",
    "i don't know",
    "pata nahi",
    "pata nahin",
}


def process_turn(
    user_text: str,
    session_id: str,
    service: EligibilityService,
    extractor: ProfileExtractor,
    store: SessionStore,
    *,
    skipped_fields=(),
    answer_field=None,
    skip=False,
) -> dict:
    state = store.get(session_id)
    skipped_fields = set(skipped_fields)

    normalized_text = (
        user_text.strip()
        .casefold()
        .rstrip("।.!?")
        .strip()
    )

    if (
        answer_field in QUESTIONS
        and normalized_text in SKIP_PHRASES
    ):
        skip = True
        skipped_fields.add(answer_field)

    try:
        if skip:
            # A skip is not an applicant fact.
            # It must never be extracted as false.
            store.touch(session_id)

        else:
            if answer_field in QUESTIONS:
                updates = extractor.extract_profile(
                    user_text,
                    state.profile,
                    question=QUESTIONS[answer_field],
                )
            else:
                updates = extractor.extract_profile(
                    user_text,
                    state.profile,
                )

            state = store.merge(
                session_id,
                updates,
            )

    except (ProfileExtractionError, ValidationError):
        logger.warning(
            "conversation_extraction_retry",
            extra={"session_id": session_id},
        )

        # Do not publish stale eligibility decisions as if this
        # utterance had been successfully understood.
        retry_question = None

        if answer_field in QUESTIONS:
            retry_question = {
                "field": answer_field,
                "text": QUESTIONS[answer_field],
                "optional": True,
                "sensitive": answer_field in SENSITIVE,
                "scheme_names": [],
            }

        return {
            "user_text": user_text,
            "ai_text": (
                "मैं आपकी जानकारी समझ नहीं पाया। "
                "कृपया दोबारा बताएं।"
            ),
            "schemes": [],
            "potential_schemes": [],
            "eligibility_results": [],
            "missing_fields": [],
            "retry": True,
            "session": state.response_dict(),
            "result_cards": [],
            "skipped_fields": sorted(skipped_fields),
            "next_question": retry_question,
        }

    results = service.evaluate_all_schemes(
        attributes(state.profile)
    )

    eligible = [
        result
        for result in results
        if result.status == "ELIGIBLE"
    ]

    potential = [
        result
        for result in results
        if result.status == "POTENTIALLY_ELIGIBLE"
    ]

    pending = [
        result
        for result in results
        if result.status == "NEED_MORE_INFORMATION"
    ]

    schemes = [
        result.scheme_name
        for result in eligible
    ]

    potential_schemes = [
        result.scheme_name
        for result in potential
    ]

    question = next_question(
        results,
        state.profile,
        skipped_fields,
    )

    if question:
        ai_text = question["text"]

    elif schemes:
        ai_text = (
            "दी गई जानकारी के आधार पर कुछ योजनाओं के लिए "
            "आप पात्र दिखाई देते हैं। नीचे योजनाएँ और कारण देखें।"
        )

    elif potential:
        ai_text = (
            "आपकी जानकारी के आधार पर कुछ योजनाएँ संभावित रूप से "
            "मेल खाती हैं। अंतिम पात्रता के लिए बाकी शर्तों की पुष्टि करें।"
        )

    elif pending:
        ai_text = (
            "कुछ योजनाओं के लिए अभी और जानकारी चाहिए। "
            "नीचे बाकी आवश्यक जानकारी देखें।"
        )

    else:
        ai_text = (
            "दी गई जानकारी से अभी कोई योजना मेल नहीं खाती। "
            "नीचे कारण देखें; आप अपनी जानकारी बदल भी सकते हैं।"
        )

    # Finalize only when the automated conversation has no more
    # information to ask for.
    #
    # POTENTIALLY_ELIGIBLE results may still contain manual conditions,
    # but those cannot be resolved through more profile questions.
    if not pending:
        store.mark_finalized(
            session_id,
            state.attempts,
        )

        state = replace(
            state,
            finalized=True,
        )

    return {
        "user_text": user_text,
        "ai_text": ai_text,
        "schemes": schemes,
        "potential_schemes": potential_schemes,
        "eligibility_results": [
            result.model_dump(mode="json")
            for result in results
        ],
        "missing_fields": (
            [question["field"]]
            if question
            else []
        ),
        "next_question": question,
        "result_cards": present_results(results),
        "skipped_fields": sorted(skipped_fields),
        "session": state.response_dict(),
    }