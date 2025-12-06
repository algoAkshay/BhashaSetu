import re
import csv
import os

# -------------------------------------------------
# SESSION STATE (SINGLE SOURCE OF TRUTH)
# -------------------------------------------------
SESSION = {
    "age": None,
    "gender": None,
    "income": None,
    "attempts": 0,
    "finalized": False
}

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
SCHEME_FILE = os.path.join(BASE_DIR, "database", "schemes.csv")


# -------------------------------------------------
# EXTRACT FIELDS + COUNT ATTEMPTS
# -------------------------------------------------
def extract_fields(text: str):
    if SESSION["finalized"]:
        return

    # ✅ increment exactly once per user submit
    SESSION["attempts"] += 1

    text_lower = text.lower()

    # ---------- AGE ----------
    age_match = re.search(r"\b(\d{2})\b", text_lower)
    if age_match:
        SESSION["age"] = int(age_match.group(1))

    # ---------- GENDER ----------
    male_keywords = [
        "पुरुष", "पुरूष", "पुरुस", "आदमी",
        "male", "mard", "man", "boy",
        "purush", "aadmi", "hun", "hoon"
    ]

    female_keywords = [
        "महिला", "औरत", "स्त्री",
        "female", "woman", "lady",
        "aurat", "mahila"
    ]

    if any(word in text_lower for word in male_keywords):
        SESSION["gender"] = "Male"
    elif any(word in text_lower for word in female_keywords):
        SESSION["gender"] = "Female"

    # ---------- INCOME ----------
    income_match = re.search(r"(\d+)\s*(लाख|हजार)?", text_lower)
    if income_match:
        income = int(income_match.group(1))
        unit = income_match.group(2)

        if unit == "लाख":
            income *= 100000
        elif unit == "हजार":
            income *= 1000

        SESSION["income"] = income


# -------------------------------------------------
# APPLY DEFAULTS AFTER 2ND ATTEMPT
# -------------------------------------------------
def apply_defaults_if_needed():
    if SESSION["attempts"] >= 2 and not SESSION["finalized"]:

        if SESSION["age"] is None:
            SESSION["age"] = 30

        if SESSION["gender"] is None:
            SESSION["gender"] = "Male"

        if SESSION["income"] is None:
            SESSION["income"] = 100000

        SESSION["finalized"] = True


# -------------------------------------------------
# CHECK MISSING (ONLY BEFORE FINALIZE)
# -------------------------------------------------
def get_missing_fields():
    if SESSION["finalized"]:
        return []

    missing = []
    if SESSION["age"] is None:
        missing.append("उम्र")
    if SESSION["gender"] is None:
        missing.append("लिंग")
    if SESSION["income"] is None:
        missing.append("वार्षिक आय")
    return missing


# -------------------------------------------------
# SCHEME MATCHING
# -------------------------------------------------
def find_eligible_schemes():
    results = []

    with open(SCHEME_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row in reader:
            if not (int(row["min_age"]) <= SESSION["age"] <= int(row["max_age"])):
                continue

            if row["gender"] != "Any" and row["gender"] != SESSION["gender"]:
                continue

            if SESSION["income"] > int(row["max_income"]):
                continue

            results.append(row["scheme_name"])

    return results


# -------------------------------------------------
# SESSION STATE (READ ONLY)
# -------------------------------------------------
def get_session_state():
    return SESSION.copy()


# -------------------------------------------------
# RESET (OPTIONAL)
# -------------------------------------------------
def reset_session():
    SESSION["age"] = None
    SESSION["gender"] = None
    SESSION["income"] = None
    SESSION["attempts"] = 0
    SESSION["finalized"] = False
