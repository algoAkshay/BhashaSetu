import csv
import os
import re
from typing import Any


BASE_DIR = os.path.dirname(os.path.dirname(__file__))
SCHEME_FILE = os.path.join(BASE_DIR, "database", "schemes.csv")
DEFAULT_SESSION_ID = "default"

SESSIONS: dict[str, dict[str, Any]] = {}


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
    return SESSIONS.setdefault(_session_id(session_id), _new_session())


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
    session = _get_session(session_id)
    if session["attempts"] >= 2 and not session["finalized"]:
        if session["age"] is None:
            session["age"] = 30
        if session["gender"] is None:
            session["gender"] = "Male"
        if session["income"] is None:
            session["income"] = 100000

        session["finalized"] = True


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


def _load_schemes() -> list[dict[str, str]]:
    with open(SCHEME_FILE, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def find_eligible_schemes(session_id: str | None = None):
    session = _get_session(session_id)
    if session["age"] is None or session["gender"] is None or session["income"] is None:
        return []

    eligible = []
    for scheme in _load_schemes():
        min_age = int(scheme["min_age"])
        max_age = int(scheme["max_age"])
        max_income = int(scheme["max_income"])
        gender = scheme["gender"]

        if (
            min_age <= session["age"] <= max_age
            and (gender == "Any" or gender == session["gender"])
            and session["income"] <= max_income
        ):
            eligible.append(scheme["scheme_name"])

    return eligible


def get_session_state(session_id: str | None = None):
    return _get_session(session_id).copy()


def reset_session(session_id: str | None = None):
    SESSIONS[_session_id(session_id)] = _new_session()
