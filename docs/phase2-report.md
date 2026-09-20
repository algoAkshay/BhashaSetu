# Phase 2 implementation and verification — 2026-09-19

Structured extraction and slot filling are implemented and verified offline. **Real Gemini accuracy and the full microphone-to-Gemini-to-PostgreSQL flow remain unverified** because this shell has no GEMINI_API_KEY, DATABASE_URL or TEST_DATABASE_URL, and the browser runtime has no connected browser. This report does not claim that a mocked response demonstrates language understanding.

## A. Baseline before editing

Inspected the repository inventory, server speech route, ASR service, conversation/session helpers, normalization, schemas, pure rules, EligibilityService, tests and documentation. No applicable AGENTS.md was found. The old path extracted with regex, forced an introductory turn and reset completed profiles on the next turn. Sessions stored age, Male/Female gender and income; the adapter mapped income to annual_income and rule normalization case-folded gender.

Baseline full pytest: **90 passed, 1 skipped, 6 warnings, 1,073 subtests passed in 30.23 seconds**. The skip was the opt-in PostgreSQL test. The six warnings concern existing Starlette/httpx/dateutil deprecations and imported test_database helpers collected as tests. Baseline `pip check` already failed with the ten conflicts recorded below.

The user's real IndicConformer and browser transcription verification is accepted as the starting state. ASR was not reconfigured or retuned.

## B–C. Files changed and why

| File | Change and purpose |
| --- | --- |
| `backend/profile_schemas.py` | Added strict UserProfile and ProfileExtractionResult models. |
| `backend/services/profile_extraction_service.py` | Added small provider protocol, official Gemini structured request, validation, safe errors and field-name logging. |
| `backend/config.py` | Added separate LLM settings with a redacted key representation; existing ASR settings retained. |
| `backend/logic.py` | Added typed profile access and atomic validation/non-null merge; marked regex helpers as legacy test-only compatibility. |
| `backend/services/conversation_service.py` | Replaced runtime regex with injected extraction, immediate evaluation, missing-slot prompts, retained profiles/corrections and safe retries. |
| `backend/server.py` | Injected extractor and awaited blocking conversation work in the thread pool; preserved upload, ASR, response and TTS contracts. |
| `requirements.txt` | Added only the direct SDK dependency `google-genai==2.24.0`. |
| `.env.example` | Documented Gemini provider, model, blank key and timeout. |
| `tests/test_profile_extraction.py` | Added offline schema/provider/failure/logging tests and actual SDK serialization through an in-memory HTTP transport. |
| `tests/test_conversation.py` | Added merge, correction, partial/full profile, dynamic missing-rule and deterministic regression coverage. |
| `tests/test_api.py` | Injected extractor fakes; updated deliberate first-turn/reset behavior and added endpoint failure/multi-turn regression. |
| `scripts/test_profile_extraction.py` | Added explicit live smoke command, expected-value comparison and optional existing PostgreSQL evaluation. |
| `README.md` | Added setup, smoke/browser checks, current behavior and limitations; corrected superseded extraction/ASR statements. |
| `ARCHITECTURE.md` | Documented strict separation of language extraction and deterministic decisions. |
| `docs/phase2-preserved-hashes.json` | Recorded pre-edit SHA256 values for 14 protected implementation/data/frontend files. |
| `docs/phase2-report.md` | This audit, measured results and remaining verification. |

Local ignored `.test-deps` gained the SDK and required missing/newer dependencies for verification. The shared `google` namespace required staged installation and copying only the new package files. No global packages were removed; no ASR dependencies were reinstalled. The pre-existing global legacy Google SDK was not added to project requirements or imported by this implementation.

## D. Architecture

Browser voice → unchanged IndicConformer/audio normalization → transcript → Gemini JSON-schema extraction → strict Pydantic validation → process-local non-null merge → unchanged EligibilityService/PostgreSQL rules → Hindi response/gTTS.

The LLM receives only the utterance and current three-field profile. It receives no scheme names, requirements or eligibility results. It returns new explicit facts only. The engine can evaluate a partial profile to derive missing slots from unresolved active rules; it remains the sole source of eligibility decisions. There is no mandatory first turn, regex fallback, Redis, admin, RAG or scraping.

## E. Exact provider, SDK and model

- Provider: Google Gemini Developer API, explicitly `vertexai=False`.
- SDK: official **google-genai 2.24.0**, verified from PyPI and installed/imported locally.
- Model default: **gemini-3.5-flash-lite**, configurable through LLM_MODEL.
- Interface: `client.models.generate_content`, `response_mime_type="application/json"`, `response_json_schema=ProfileExtractionResult.model_json_schema()`; raw JSON is validated again.
- No tools are supplied; automatic function calling is disabled. HTTP timeout is 30,000 ms by default, one attempt, client closed after each request.

Google's [model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) lists the exact model as stable with structured outputs and a July 2026 update. The [official SDK documentation](https://googleapis.github.io/python-genai/) documents the generate_content schema interface. These were checked on 2026-09-19. Documentation confirms published support, not this account's quota/access or semantic accuracy.

Local transitive additions needed by the SDK: google-auth 2.58.0, websockets 16.1.1 and distro 1.9.0. Existing compatible Pydantic/httpx/etc. were reused.

## F. Structured schema

| Field | Accepted value |
| --- | --- |
| age | Strict integer 0–120 or null |
| gender | Exactly Male, Female or null |
| annual_income | Strict nonnegative integer rupees or null |

All three update keys are required, extras forbidden. Numeric strings, booleans, fractions, negative values, impossible ages, missing keys and prose fail validation. UserProfile defaults absent contextual fields to null, never a guessed personal value. Models are immutable and instances are revalidated at merge. The existing session still exposes `income`; mapping and the existing case-insensitive rule boundary preserve compatibility.

## G–I. Merge, missing fields and corrections

The whole update validates before changing any slot. Non-null new facts replace previous facts; nulls never erase values. Successful turns increment attempts. A complete first turn immediately produces deterministic results. The missing_fields list comes only from NEED_MORE_INFORMATION results, so rules for already excluded schemes do not cause unnecessary questions. Only those fields appear in the Hindi prompt. Schemes with no rules remain unresolved.

Completed sessions no longer reset automatically. An explicit age correction replaces age while preserving gender/income; an income correction replaces only income. Eligibility is recomputed. Reloading the existing page creates a fresh session ID for a new applicant; no frontend changes were needed.

The prompt handles explicit Hindi/Hinglish/English facts and spoken-number normalization. It excludes relatives' facts, vague age/income and assumed monthly-to-annual conversion. Plain income in this annual-income conversation is treated as annual unless another period is stated. Bare numeric answers are permitted only for a single unambiguous remaining slot. Live accuracy of these policies remains to be checked.

## J. Failure behavior

Missing API key, unsupported configuration, provider errors, timeout, rate limits, blocked/malformed output and validation errors produce a safe Hindi retry: `मैं आपकी जानकारी समझ नहीं पाया। कृपया दोबारा बताएं।`

Existing profile, attempts and finalization remain unchanged. The response contains no stale eligibility decisions. The successful speech response shape is retained, including user_text, ai_text, schemes, session, eligibility_results, missing_fields and audio_url. TTS is still attempted and its existing failure behavior returns null audio. Extraction logs contain event categories and field names; no raw upstream exceptions, payloads, transcript or profile values are logged by the new code.

## K. Final verification results

| Check | Actual result |
| --- | --- |
| Full pytest | **148 passed, 1 skipped, 6 warnings, 1,073 subtests passed in 54.94 seconds** |
| Compile | `python -m compileall -q backend tests migrations scripts` succeeded |
| Imports | Server, Gemini SDK, gTTS, Transformers, ONNX Runtime, SQLAlchemy and psycopg succeeded |
| SDK request contract | Actual SDK serialization/response parsing succeeded using httpx.MockTransport; no network |
| Catalogue | Actual migration/seed in isolated test DB produced **67 schemes / 217 rules** |
| Required example downstream | Real unchanged engine with 25/Male/5000 produced **44 ELIGIBLE, 23 NOT_ELIGIBLE, 0 NEED_MORE_INFORMATION** on supplied seed |
| Deterministic regression | Conversation results equal direct EligibilityService results; original operator/CSV regression tests still pass |
| ASR and protected files | All 14 recorded SHA256 hashes match |
| Live PostgreSQL | Skipped: TEST_DATABASE_URL absent; no production URL available in this shell |
| Existing local server | Read-only catalogue check at localhost:8000 received connection refused |
| pip check | Still fails on the same ten pre-existing conflicts; no new Gemini dependency conflict |

ASR SHA256 remains `9A5EBAB1E4FCA30869CB35C34AED7D9C83C0F2923FCCA72AD52CF3DA9803969C`. EligibilityService remains `00F09F70688E9389235E3BBFFB8E4462F17891857536FEB859749229FB8FA6FA`. Models, API schemas, normalizer, pure engine, repositories, migration, seed code/CSV and frontend also match their pre-edit hashes.

The unchanged pip conflicts are: googleapis-common-protos/protobuf, google-ai-generativelanguage/protobuf, grpcio-status/protobuf, proto-plus/protobuf, selenium/typing-extensions, snowflake-connector-python/filelock, snowflake-snowpark-python/protobuf, and Streamlit's packaging, pandas and protobuf constraints. Resolving these unrelated shared-environment conflicts was not attempted by changing the working ASR stack. `pip check` is **not** a clean pass.

Offline tests cover requested cases A–N: Hindi/Hinglish/English and number variants as mocked provider contract fixtures; partial/no facts; ambiguous/relative/monthly-income fixtures; merge/null preservation; corrections; income-only prompts; impossible values; failure/no state corruption; unchanged rules; and the speech API. These test fixtures do not prove the live model will return those values.

## L. Real LLM smoke result

Ran the explicit script with the requested Hindi example. It printed provider/model/input, returned `extraction_failed: missing_api_key`, and exited 1 in approximately 0.001 seconds. **No real Gemini call or exact-value success is claimed.** Credentials were not present and no key was printed or committed.

## M. Browser E2E result

Browser skill/runtime initialization succeeded, but URL selection reported no browser; discovery returned an empty list. The three real microphone sequences could not be run. The user-verified ASR starting state is preserved; the new Gemini flow is not yet browser-verified. API tests cover mocked transcription plus mocked extraction, not actual microphone/model services.

## N. Known limitations

Schema validity cannot prove semantic correctness. Model access, quota, rate limits, latency and Hindi/Hinglish accuracy need real measurement. Only age, Male/Female gender and annual income are extracted; state/disability rules would require explicit extraction support. Unsupported or ambiguous gender stays unknown. Sessions remain process-local, without persistence/expiry or coordination of overlapping same-session turns. No sophisticated multi-applicant dialogue is implemented. The seed remains prototype data, not verified current government policy.

## O. Environment variables

| Variable | Requirement/default |
| --- | --- |
| DATABASE_URL | Retain existing migrated/seeded PostgreSQL URL; needed by server and --eligibility |
| GEMINI_API_KEY | Required for live extraction; supply securely in process environment |
| LLM_PROVIDER | gemini (default and only runtime provider) |
| LLM_MODEL | gemini-3.5-flash-lite |
| LLM_TIMEOUT_MS | 30000; allowed 1000–120000 |
| TEST_DATABASE_URL | Optional disposable PostgreSQL database for integration test |
| ASR_PROVIDER / ASR_MODEL_ID | Retain indicconformer / ai4bharat/indic-conformer-600m-multilingual |
| ASR_LANGUAGE / ASR_DECODER | Retain hi / ctc |
| ASR_LOG_TRANSCRIPTS | false by default |
| HF_TOKEN / FFMPEG_BINARY | Retain existing setup only if already needed; cached HF login is supported |

`.env.example` is not loaded automatically. Restart Uvicorn from the configured working environment after installation/configuration.

## P. Exact local commands

Use the Python environment where the user's ASR already works; do not reinstall ASR.

```powershell
Set-Location 'C:\Users\LENOVO\Downloads\BhashaSetu-main (4)\BhashaSetu-main'
python -m pip install google-genai==2.24.0
python -m pip check
$env:LLM_PROVIDER = 'gemini'
$env:LLM_MODEL = 'gemini-3.5-flash-lite'
$env:LLM_TIMEOUT_MS = '30000'
$geminiSecret = Read-Host 'Gemini API key' -AsSecureString
$env:GEMINI_API_KEY = [System.Net.NetworkCredential]::new('', $geminiSecret).Password
Remove-Variable geminiSecret
# Keep the existing DATABASE_URL and ASR/Hugging Face configuration.
python scripts/test_profile_extraction.py 'मैं पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है' --eligibility
python -m pytest -q
python -m compileall -q backend tests migrations scripts
python -m uvicorn backend.server:app --reload
```

The script also accepts `--expected` for strict complete-JSON comparison and `--current-profile` for contextual extraction; use `--help` for arguments. Inspect exactly 25/Male/5000 and downstream statuses against the actual local catalogue, not merely an HTTP success. On the unchanged supplied seed, expect 44 eligible and 23 ineligible results.

To reproduce this agent's offline verification environment specifically (SDK/test dependencies were installed into the ignored local overlay):

```powershell
$env:PYTHONPATH = '.test-deps'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -q --tb=short
python -m compileall -q backend tests migrations scripts
python -m pip check
```

For PostgreSQL integration, securely set TEST_DATABASE_URL to a disposable database with schema-creation permission, then run `python -m pytest tests/test_postgresql.py -q`. That test manages its own temporary schema.

## Q. Remaining manual verification

1. Configure Gemini in the actual server environment; verify the live example produces exactly age 25, Male, annual_income 5000 and that the existing PostgreSQL engine produces the expected per-rule results.
2. Run the three browser sequences in README: full Hindi profile, age 65 then remaining fields, and Hinglish age → gender → income. Inspect actual speech-response JSON, missing prompts, retained slots, corrected values and Hindi audio.
3. Run the opt-in PostgreSQL test with TEST_DATABASE_URL. The existing user-verified PostgreSQL state is not a new Phase 2 integration pass.
4. Resolve the pre-existing shared-environment pip conflicts separately if a fully clean dependency check is required; this phase leaves working ASR packages untouched.
