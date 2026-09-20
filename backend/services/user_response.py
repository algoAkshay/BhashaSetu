"""Presentation and question selection only; never changes eligibility decisions."""
ALIASES = {"state": "state_or_ut", "is_disabled": "disability_status"}
SENSITIVE = {"social_category", "disability_status", "disability_percentage", "minority_status",
             "widow_status", "bpl_status", "marital_status", "gender"}
PRIORITY = ["state_or_ut", "age", "occupation", "family_annual_income", "annual_income",
            "individual_monthly_income", "student_status", "farmer_status", "employment_status",
            "education_level", "rural_urban", "gender", "social_category", "disability_status",
            "disability_percentage", "minority_status", "widow_status", "bpl_status", "marital_status"]
PRIORITY_TIERS = {field: tier for tier, group in enumerate([
    ["state_or_ut"], ["age"], ["occupation"],
    ["family_annual_income", "annual_income", "individual_monthly_income"],
    ["student_status", "farmer_status", "employment_status"], ["education_level"], ["rural_urban"],
    [field for field in PRIORITY if field in SENSITIVE],
]) for field in group}
QUESTIONS = {
    "state_or_ut": "आप किस राज्य या केंद्र शासित प्रदेश में रहते हैं?",
    "age": "आपकी उम्र कितनी है?",
    "occupation": "क्या आप विद्यार्थी, किसान, नौकरीपेशा, स्वरोज़गार या बेरोज़गार हैं?",
    "family_annual_income": "अगर पता हो, आपके परिवार की सालाना आय कितनी है?",
    "annual_income": "आपकी अपनी सालाना आय कितनी है?",
    "individual_monthly_income": "आपकी अपनी महीने की आय कितनी है?",
    "student_status": "क्या आप अभी पढ़ाई कर रहे हैं?",
    "farmer_status": "क्या आप किसान हैं?",
    "employment_status": "क्या आप नौकरी, स्वरोज़गार कर रहे हैं या अभी काम ढूँढ़ रहे हैं?",
    "education_level": "आपने कहाँ तक पढ़ाई की है?",
    "rural_urban": "आप गाँव में रहते हैं या शहर में?",
    "gender": "इस योजना के लिए, यदि बताना चाहें तो अपना लिंग बताएँ।",
    "social_category": "कुछ संभावित योजनाओं के लिए, यदि बताना चाहें तो अपनी सामाजिक श्रेणी बताएँ (जैसे SC, ST, OBC या सामान्य)।",
    "disability_status": "इस संभावित योजना के लिए, यदि बताना चाहें: क्या आप दिव्यांग हैं?",
    "disability_percentage": "यदि बताना चाहें, आपके दिव्यांगता प्रमाणपत्र में कितना प्रतिशत दर्ज है?",
    "minority_status": "इस संभावित योजना के लिए, यदि बताना चाहें: क्या आप अल्पसंख्यक समुदाय से हैं?",
    "widow_status": "इस संभावित योजना के लिए, यदि बताना चाहें: क्या आप विधवा हैं?",
    "bpl_status": "इस संभावित योजना के लिए, यदि बताना चाहें: क्या आपके पास बीपीएल का प्रमाण है?",
    "marital_status": "इस संभावित योजना के लिए, यदि बताना चाहें तो अपनी वैवाहिक स्थिति बताएँ।",
}
LABELS = {
    "state_or_ut": "राज्य", "age": "आयु सीमा", "occupation": "काम / व्यवसाय",
    "family_annual_income": "परिवार की आय सीमा", "annual_income": "अपनी सालाना आय सीमा",
    "individual_monthly_income": "अपनी मासिक आय सीमा", "student_status": "विद्यार्थी होने की शर्त",
    "farmer_status": "किसान होने की शर्त", "employment_status": "रोज़गार की शर्त",
    "education_level": "पढ़ाई की शर्त", "rural_urban": "गाँव / शहर की शर्त", "gender": "लिंग की शर्त",
    "social_category": "सामाजिक श्रेणी", "disability_status": "दिव्यांगता की शर्त",
    "disability_percentage": "दिव्यांगता प्रतिशत", "minority_status": "अल्पसंख्यक समुदाय की शर्त",
    "widow_status": "विधवा होने की शर्त", "bpl_status": "बीपीएल की शर्त", "marital_status": "वैवाहिक स्थिति",
}
STATUS_LABELS = {
    "ELIGIBLE": "दी गई जानकारी के आधार पर आप पात्र दिखाई देते हैं",
    "NEED_MORE_INFORMATION": "इस योजना के लिए थोड़ी और जानकारी चाहिए",
    "NOT_ELIGIBLE": "दी गई जानकारी के आधार पर पात्रता पूरी नहीं होती",
}


def attributes(profile):
    values = profile.model_dump()
    # Compatibility aliases refer to the same explicitly supplied facts.
    for legacy, current in ALIASES.items():
        if values.get(current) is not None:
            values[legacy] = values[current]
    return values


def next_question(results, profile, skipped=()):
    known = profile.model_dump()
    pending = [r for r in results if r.status == "NEED_MORE_INFORMATION"]
    candidates = {}
    for result in pending:
        missing = {ALIASES.get(f, f) for f in result.missing_fields}
        for field in missing:
            if field not in QUESTIONS or field in skipped or known.get(field) is not None:
                continue
            # User type has already been supplied; do not ask the same broad question again.
            if field == "occupation" and (profile.student_status is True or profile.farmer_status is True
                                           or profile.employment_status is not None):
                continue
            if field == "disability_percentage" and profile.disability_status is not True:
                continue
            if field in SENSITIVE:
                # Ask only when it can settle a structured candidate's last missing slot,
                # with positive evidence of relevance, not because a catalogue rule exists.
                if missing != {field} or not any(c.passed is True for c in result.checks):
                    continue
            candidates.setdefault(field, []).append(result.scheme_name)
    if not candidates:
        return None
    # Within a broad priority tier, prefer the question useful to more candidates.
    field = min(candidates, key=lambda f: (PRIORITY_TIERS[f], -len(candidates[f]), PRIORITY.index(f)))
    return {"field": field, "text": QUESTIONS[field], "optional": True,
            "sensitive": field in SENSITIVE,
            "scheme_names": candidates[field][:2] if field in SENSITIVE else []}


def present_results(results):
    """No raw operators/profile values in the normal-user presentation contract."""
    cards = []
    for result in results:
        reasons = []
        relevant = [c for c in result.checks if c.passed is False] if result.status == "NOT_ELIGIBLE" else [c for c in result.checks if c.passed is True]
        for check in relevant:
            label = LABELS.get(ALIASES.get(check.field, check.field), "एक पात्रता शर्त")
            reason = f"{label}: " + ("मेल खाता है" if check.passed else "मेल नहीं खाता")
            if reason not in reasons:
                reasons.append(reason)
        if result.status == "NEED_MORE_INFORMATION":
            if result.missing_fields:
                reasons.append("कुछ जानकारी अभी बाकी है; बिना उसके पात्रता तय नहीं की गई है।")
            elif result.reason == "no_rules":
                reasons.append("इस योजना के नियम अभी उपलब्ध नहीं हैं।")
            elif result.manual_conditions:
                reasons.append("दर्ज जानकारी मेल खाती है; नीचे दी गई अतिरिक्त शर्तों की पुष्टि बाकी है।")
        cards.append({"scheme_id": result.scheme_id, "scheme_name": result.scheme_name,
                      "status": result.status, "label": STATUS_LABELS[result.status],
                      "reasons": reasons[:3], "manual_conditions": result.manual_conditions
                      if result.status != "NOT_ELIGIBLE" else [],
                      "_rank": (len(result.missing_fields), -sum(c.passed is True for c in result.checks))})
    cards.sort(key=lambda card: (list(STATUS_LABELS).index(card["status"]), card["_rank"], card["scheme_name"]))
    for card in cards:
        del card["_rank"]
    return cards


# Keep result audio useful without reading an entire catalogue or long source notes.
SPOKEN_SCHEME_LIMIT = 3
SPOKEN_NAME_CHAR_LIMIT = 160
SPOKEN_DETAIL_CHAR_LIMIT = 220


def _spoken_excerpt(text, limit):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def result_narration(response):
    """Read existing presentation results only; never evaluate, rank or infer facts."""
    if response.get("next_question") or response.get("retry"):
        return response["ai_text"]
    cards = [card for card in response.get("result_cards", [])
             if card["status"] in {"ELIGIBLE", "NEED_MORE_INFORMATION"}]
    if not cards:
        return response["ai_text"]
    eligible_count = sum(card["status"] == "ELIGIBLE" for card in cards)
    pending_count = len(cards) - eligible_count
    parts = []
    if eligible_count:
        parts.append(f"दी गई जानकारी के आधार पर {eligible_count} योजनाओं में आप पात्र दिखाई देते हैं।")
    if pending_count:
        parts.append(f"{pending_count} संभावित योजनाओं की पात्रता अभी तय नहीं है; कुछ जानकारी या पुष्टि बाकी है।")
    if len(cards) > SPOKEN_SCHEME_LIMIT:
        parts.append(f"पहले {SPOKEN_SCHEME_LIMIT} परिणाम सुनिए।")
    for position, card in enumerate(cards[:SPOKEN_SCHEME_LIMIT], 1):
        parts.append(f"योजना {position}, {_spoken_excerpt(card['scheme_name'], SPOKEN_NAME_CHAR_LIMIT)}।")
        if card["status"] == "NEED_MORE_INFORMATION":
            parts.append("यह अभी संभावित योजना है, पात्रता की पुष्टि नहीं हुई है।")
        if card.get("reasons"):
            # The reason is already derived by present_results from engine output.
            parts.append(_spoken_excerpt(card["reasons"][0], SPOKEN_DETAIL_CHAR_LIMIT))
        if card.get("manual_conditions"):
            condition = " ".join(card["manual_conditions"][0].split())
            prefix = "इस योजना की अतिरिक्त शर्त का अंश: " if len(condition) > SPOKEN_DETAIL_CHAR_LIMIT else "इस योजना की अतिरिक्त शर्त की पुष्टि ज़रूरी है: "
            parts.append(prefix + _spoken_excerpt(condition, SPOKEN_DETAIL_CHAR_LIMIT))
            if len(card["manual_conditions"]) > 1 or len(condition) > SPOKEN_DETAIL_CHAR_LIMIT:
                parts.append("आवेदन से पहले इसकी पूरी शर्तें स्क्रीन पर देखें।")
    remaining = len(cards) - SPOKEN_SCHEME_LIMIT
    if remaining > 0:
        parts.append(f"बाकी {remaining} परिणाम और सभी योजनाओं की पूरी जानकारी स्क्रीन पर उपलब्ध है।")
    else:
        parts.append("इन योजनाओं की पूरी जानकारी स्क्रीन पर उपलब्ध है।")
    return " ".join(parts)
