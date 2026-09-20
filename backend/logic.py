"""Deprecated process-local compatibility helpers for historical Python tests only.

Production uses the injected SessionStore, never this module. These helpers are
not a fallback. Explicit development memory storage uses InMemorySessionStore.
"""
import re
from typing import Any

from backend.profile_schemas import ProfileExtractionResult, UserProfile


DEFAULT_SESSION_ID = "default"

_LEGACY_SESSIONS: dict[str, dict[str, Any]] = {}


def _new_session() -> dict[str, Any]:
    return {
        "age": None,
        "gender": None,
        "income": None,
        "attempts": 0,
        "finalized": False,
    }


def _session_id(session_id: str | None = None) -> str:
    return (session_id or DEFAULT_SESSION_ID).strip() or DEFAULT_SESSION_ID


def _get_session(session_id: str | None = None) -> dict[str, Any]:
    return _LEGACY_SESSIONS.setdefault(_session_id(session_id), _new_session())


def _parse_number(value: str) -> float:
    return float(value.replace(",", ""))


def _parse_income(text: str) -> int | None:
    income_patterns = [
        r"(?:आय|कमाई|इनकम|income|salary|earning|सालाना|वार्षिक)[^\d]{0,20}(\d+(?:[.,]\d+)?)\s*(लाख|हजार|lac|lakh|k)?",
        r"(\d+(?:[.,]\d+)?)\s*(लाख|हजार|lac|lakh|k)\b",
    ]

    for pattern in income_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            amount = _parse_number(match.group(1))
            unit = (match.group(2) or "").lower()

            if unit in {"लाख", "lac", "lakh"}:
                amount *= 100000
            elif unit in {"हजार", "k"}:
                amount *= 1000

            return int(amount)

    return None


def _parse_age(text: str) -> int | None:
    age_patterns = [
        r"(?:उम्र|age)[^\d]{0,10}(\d{1,3})",
        r"(\d{1,3})\s*(?:साल|वर्ष|year|years|yr|yrs)",
    ]

    for pattern in age_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            age = int(match.group(1))
            if 0 <= age <= 120:
                return age

    return None


def _parse_gender(text: str) -> str | None:
    text_lower = text.lower()

    female_keywords = [
        "महिला",
        "औरत",
        "स्त्री",
        "लड़की",
        "लडकी",
        "बेटी",
        "female",
        "woman",
        "lady",
        "girl",
        "aurat",
        "mahila",
        "ladki",
    ]
    male_keywords = [
        "पुरुष",
        "पुरूष",
        "पुरुस",
        "आदमी",
        "लड़का",
        "लडका",
        "male",
        "mard",
        "man",
        "boy",
        "purush",
        "aadmi",
        "ladka",
    ]

    if any(word in text_lower for word in female_keywords):
        return "Female"
    if any(word in text_lower for word in male_keywords):
        return "Male"
    return None


def extract_fields(text: str, session_id: str | None = None):
    """Deprecated legacy regex helper; not used by the speech request path."""
    session = _get_session(session_id)
    if session["finalized"]:
        return

    session["attempts"] += 1
    text_lower = text.lower()

    age = _parse_age(text_lower)
    if age is not None:
        session["age"] = age

    gender = _parse_gender(text_lower)
    if gender is not None:
        session["gender"] = gender

    income = _parse_income(text_lower)
    if income is not None:
        session["income"] = income


def apply_defaults_if_needed(session_id: str | None = None):
    """Legacy compatibility hook: finalize complete input, never invent attributes."""
    session = _get_session(session_id)
    if session["attempts"] >= 2 and all(session[key] is not None for key in ("age", "gender", "income")):
        session["finalized"] = True


def finalize_session(session_id: str | None = None):
    _get_session(session_id)["finalized"] = True


def get_missing_fields(session_id: str | None = None):
    session = _get_session(session_id)
    if session["finalized"]:
        return []

    missing = []
    if session["age"] is None:
        missing.append("उम्र")
    if session["gender"] is None:
        missing.append("लिंग")
    if session["income"] is None:
        missing.append("वार्षिक आय")
    return missing


def eligibility_attributes(session_id: str | None = None):
    session = _get_session(session_id)
    return {"age": session["age"], "gender": session["gender"], "annual_income": session["income"]}


def get_profile(session_id: str | None = None) -> UserProfile:
    return UserProfile.model_validate(eligibility_attributes(session_id))


def merge_profile(updates: ProfileExtractionResult, session_id: str | None = None) -> None:
    # Validate the entire update before touching any field. Null never erases facts.
    validated = ProfileExtractionResult.model_validate(updates)
    session = _get_session(session_id)
    for field, value in validated.model_dump(exclude_none=True).items():
        session["income" if field == "annual_income" else field] = value
    session["attempts"] += 1
    session["finalized"] = False


def find_eligible_schemes(session_id: str | None = None, *, service=None):
    """Preserve scheme-name results for callers; all eligibility comes from the DB."""
    if service is None:
        from backend.db.session import create_session_factory
        from backend.services.eligibility_service import EligibilityService

        factory = create_session_factory()
        try:
            with factory() as database_session:
                return find_eligible_schemes(session_id, service=EligibilityService(database_session))
        finally:
            factory.kw["bind"].dispose()
    results = service.evaluate_all_schemes(eligibility_attributes(session_id))
    return [result.scheme_name for result in results if result.status == "ELIGIBLE"]


def get_session_state(session_id: str | None = None):
    return _get_session(session_id).copy()


def reset_session(session_id: str | None = None):
    _LEGACY_SESSIONS[_session_id(session_id)] = _new_session()
