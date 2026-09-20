# Normal-user experience improvement

## Previous problem

The conversation service collected every missing field across unresolved schemes
and joined them into one long prompt. A generic manual-verification warning was
appended on many turns. The landing page exposed recording controls with little
guidance, and displayed only eligible scheme names. There was no typed conversation
endpoint or normal-user text entry in the inspected frontend.

## Landing page and input

The page retains the existing card layout, colours and plain JavaScript stack.
New normal-user-only CSS improves spacing, button sizes, mobile layout and focus
visibility; shared CSS and every admin asset remain unchanged.

Hindi guidance invites users to describe themselves naturally, with optional
starting points: age, state/UT, user type and known family annual income. A prominent
24-year-old student example demonstrates a complete natural sentence. The page
explicitly says all information is not needed at once and explains the four short
steps from input to schemes with reasons. There is no additional profile form.

Visible **बोलकर बताएँ** and **लिखकर बताएँ** controls switch between recording and
one text box. Both modes use the same conversation ID and Redis profile. Recording
can be reviewed before sending. Microphone failures suggest typing; request
failures leave typed text/recording available for retry. Buttons prevent overlapping
turns. Replies remain readable when audio playback fails or autoplay is blocked.

## Progressive questions

1. Extract explicitly supplied facts, including multiple facts in one sentence.
2. Merge through the existing session store; null never erases prior answers and
   explicit corrections replace old values.
3. Evaluate through the unchanged `EligibilityService` and deterministic engine.
4. Select one missing attribute from `NEED_MORE_INFORMATION` candidates only.
   A scheme with any known failed rule cannot cause follow-up questions.
5. Ask a short Hindi question. Re-evaluate after the next answer.

Priority tiers are state/UT, age, occupation/user type, income, student/farmer/
employment status, education, rural/urban residence, then sensitive attributes.
Within a tier, prefer the field useful to more candidates, with stable ordering
for ties. Only fields actually required by surviving structured rules are eligible.
Already supplied values, including false and zero, are never treated as missing.
The broad user-type question is not repeated when student/farmer status is true or
employment status is already supplied. No occupation is inferred to fill the gap;
any occupation-dependent unresolved schemes remain unresolved.

The frontend sends the current question's field as `answer_field`. The server maps
it to approved question text and supplies it to Gemini as `previous_question`,
separate from the utterance and current profile. This supports short explicit
answers such as “yes” or “2 lakh” in the correct field context without changing
provider architecture or the extraction schema. Family annual, personal annual
and personal monthly income remain distinct. The original transcript/text is
returned unmodified.

## Sensitive fields and skipping

Sensitive questions require a surviving candidate with at least one passed check
and that sensitive field as its last missing structured attribute. A scheme merely
existing in the catalogue does not trigger its sensitive question. Disability
percentage additionally requires explicitly supplied disability status true.
Optional wording explains that disclosure is the user's choice, and the page
names up to two relevant candidate schemes.

Every follow-up has **अभी नहीं बताना / पता नहीं**. Skip bypasses extraction, touches
the existing session TTL, and neither inserts false/zero nor increments profile
attempts. Recognized standalone replies such as “पता नहीं”, “pata nahi”, “skip”
and “prefer not to say” also skip the current question. Skipped attributes remain
unknown; affected schemes retain their genuine engine results. An unrelated known
failed rule can still correctly make a scheme NOT_ELIGIBLE.

Skip preferences and the current question are small client conversation state,
sent with each request. They are not profile facts and are not added to Redis.
The existing Redis hash schema, merge transactions and expiry architecture remain
unchanged. A page reload starts a new conversation as before; skip choices are not
remembered across page reloads. Users can still explicitly volunteer a skipped
fact later. Unrecognized refusals are not automatically categorized; the explicit
skip button remains available.

## Results

The main reply is one concise question, or an invitation to inspect results when
no useful question remains. There is no generic complex-condition warning on
every conversational turn. Users can open results without answering more questions.

Cards are grouped into ELIGIBLE, NEED_MORE_INFORMATION and NOT_ELIGIBLE with the
requested plain Hindi labels and up to three concise reasons derived from engine
checks. Each group initially shows five cards, with a button for more. Excluded
schemes are collapsed. Manual conditions appear only inside the relevant candidate
card's expandable additional-conditions section, preserving the original source
wording. The frontend does not render raw rule operators, values or missing-field
lists, and inserts user/source strings as text rather than HTML.

Manual conditions never become automatically satisfied; all 297 imported schemes
still have their original manual requirements. Confidence is untouched. A result
with no structured rules remains unresolved and explains this on its own card.

## API and changed files

`POST /conversation` accepts `text`, `session_id`, optional `answer_field`,
`skipped_fields`, and `skip`. Input validates message length (maximum 4,000), session
IDs and known question fields. A skip must identify the skipped question.

`POST /speech-to-text` keeps its existing file and session fields and accepts an
optional JSON `context` form field with `answer_field` and `skipped_fields`.
Both routes call the same conversation and existing gTTS function. Existing audio
cleanup and ASR error behavior remain in place.

Responses add `next_question`, `result_cards` and `skipped_fields`. Top-level
`missing_fields` now contains only the selected follow-up field (zero or one).
The existing detailed `eligibility_results` remains available for compatibility;
it is not rendered directly to normal users. Public `/api/eligibility` results
and eligibility semantics are unchanged. Existing tests asserting the old giant
list or exact response-key set were updated for this intentional contract change.

Modified existing files:

- `frontend/index.html`: Hindi guidance, input modes, conversation and results.
- `frontend/script.js`: shared voice/text handling, skip and readable result cards.
- `backend/server.py`: typed endpoint, voice context and shared response handling.
- `backend/services/conversation_service.py`: progressive turn orchestration.
- `backend/services/profile_extraction_service.py`: optional previous-question context.
- `tests/test_api.py`: updated response assertions and mixed-mode/input checks.
- `tests/test_conversation.py`: updated one-question and per-result warning assertions.
- `README.md`: links and short description of the new normal-user flow.

New files:

- `frontend/user.css`: styles loaded only by the normal-user page.
- `backend/conversation_schemas.py`: bounded conversation input validation.
- `backend/services/user_response.py`: question selection and result presentation.
- `tests/test_user_ux.py`: progressive/sensitive/skip/provider-context/storage tests.
- `docs/user-ux-preserved-hashes.json`: pre-edit protected-file hashes.
- `docs/user-ux-improvement-report.md`: this report.

## Verification and limits

Final full-suite result: **269 passed, 2 skipped, 1,106 subtests passed** in
49.40 seconds, with six existing warnings. All ten admin tests pass unchanged.
All **29 protected files** match their pre-edit SHA-256 hashes. `node --check`
passes for the normal-user JavaScript; HTML/JavaScript element references and
UTF-8 Hindi text were also checked. No rendered-browser verification is claimed.

Tests cover multi-fact Hindi input using mocked extraction, known-field suppression,
state-only and user-type questions, irrelevant disability/farmer/education rules,
sensitive relevance, optional skipping, corrections, preservation of answers,
concise Hindi, card-specific manual conditions, mixed voice/text HTTP turns,
validation and unchanged admin behavior. A Redis adapter test confirms a progressive
answer writes only its explicit field while previously decoded fields survive;
the existing real-Redis test remains available behind `TEST_REDIS_URL`.

An isolated migrated database is seeded with the legacy and researched catalogue.
Before and after conversation it retains **364 stored schemes / 297 active /
67 archived**, and **827 rules / 610 active-scheme / 217 archived-scheme rules**.
No importer, dataset, rule-engine, model, migration, admin auth/CRUD/UI, shared CSS,
ASR provider or Redis store implementation was changed. Protected-file hashes are
recorded for verification. The gTTS function itself is unchanged.

No live PostgreSQL/Redis connection variables or connected browser were available
in this execution environment. Production PostgreSQL was never accessed or
modified; the user's verified live counts are not represented as fresh live-query
evidence. Rendered mobile/browser interaction and live microphone/Gemini/gTTS
accuracy still need a manual check. Offline provider fixtures establish the data
contract, not actual language-understanding accuracy.

The selector is intentionally a small deterministic heuristic, not an information-
gain optimizer. It cannot resolve free-text/manual conditions, and conservatively
avoids some narrow questions without enough candidate evidence. It does not infer
student/farmer/category/disability facts from other attributes. State/user-type
normalization still depends on explicit Gemini extraction and existing stored rules.
Profile retention remains governed by the existing Redis TTL, not permanent history.

Manual checks: try the two supplied Hindi examples; switch between speaking and
typing within one page; answer a short follow-up; correct age; skip a question;
expand scheme reasons/conditions; check microphone denial and audio autoplay;
repeat at mobile width and verify the unchanged admin page.
