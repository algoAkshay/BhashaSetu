def plan_next_action(state):
    """
    Planner decides what the agent should do next.
    """

    if not state["finalized"]:
        return "COLLECT_ELIGIBILITY"

    return "FIND_SCHEMES"
