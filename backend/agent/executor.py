from backend.logic import (
    extract_fields,
    get_missing_fields,
    find_eligible_schemes
)

def execute(action, user_text):
    """
    Executor runs tools based on planner decision.
    """

    if action == "COLLECT_ELIGIBILITY":
        extract_fields(user_text)
        return {
            "missing": get_missing_fields()
        }

    if action == "FIND_SCHEMES":
        schemes = find_eligible_schemes()
        return {
            "schemes": schemes
        }

    return {}
