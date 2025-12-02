from backend.logic import apply_defaults_if_needed, get_session_state

def evaluate_state():
    """
    Evaluator checks if state is incomplete or failed
    and applies recovery logic.
    """

    state = get_session_state()

    if not state["finalized"]:
        apply_defaults_if_needed()

    return get_session_state()
