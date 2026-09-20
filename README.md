# Bhasha Setu

A deterministic, data-driven government-scheme eligibility assistant using curated scheme metadata and structured rules, with explicit handling for missing or complex eligibility conditions.

The supplied CSVs contain **297 schemes: 79 Central and 218 State**. Import produces **610 scalar rules** and preserves **65 rule rows for manual review**. All 297 schemes have additional conditions requiring confirmation. Passing scalar checks alone therefore does not certify eligibility. Source VERIFIED labels are preserved metadata, not independent legal verification.

## Current flow

Browser recording → FastAPI → IndicConformer → Gemini structured extraction → Pydantic validation → Redis temporary profile/slot filling → EligibilityService → PostgreSQL deterministic rules → checks, missing fields and manual conditions → Hindi response/gTTS.

The normal-user page now offers both Hindi-first voice and typed conversation.
Typing uses `/conversation` and joins the same extraction/session/eligibility flow
after the speech-transcription step. Follow-ups ask one relevant question at a time,
with optional skipping; results show plain-language reasons and scheme-specific
manual conditions. The admin panel and shared styles remain unchanged. See
[user UX improvement report](docs/user-ux-improvement-report.md) for behavior,
API additions, validation and limitations.

Gemini only extracts explicit applicant facts. Redis stores temporary profiles; PostgreSQL stores durable schemes and rules. ASR, audio normalization, browser recording and gTTS are preserved.

## Upgrade an existing installation

Use the Python environment where ASR already works. Keep existing database, Gemini and Hugging Face configuration; do not reinstall ASR.

```powershell
python -m pip install redis==8.1.0
python -m pip check
# Keep DATABASE_URL and GEMINI_API_KEY securely configured.
$env:SESSION_BACKEND = 'redis'
$env:REDIS_URL = 'redis://localhost:6379/0'
$env:SESSION_TTL_SECONDS = '1800'
$env:REDIS_KEY_PREFIX = 'bhashasetu:session'

# Offline audit; no database or network needed:
python -m backend.db.import_dataset --audit-only --report docs/dataset-audit.json
python -m alembic upgrade head
python -m backend.db.import_dataset
# A repeat must create no additional records:
python -m backend.db.import_dataset
python -m uvicorn backend.server:app --reload
```

`python -m backend.db.seed` now imports the researched dataset by default. `--csv database/schemes.csv` explicitly selects the legacy importer and is not part of normal deployment. The legacy Python seed function remains for regression fixtures.

On a clean database the result is 297 schemes and 610 rules. Upgrading the old catalogue retains its 67 schemes and 217 rules as **inactive archives**: 364 stored schemes/827 stored rules, but only **297 schemes/610 rules are active**. No prototype scheme remains active alongside the curated catalogue.

For a new installation, use Python 3.10+, PostgreSQL and a reachable Redis server. Create a virtual environment, install requirements-dev.txt (requirements.txt for runtime), securely configure DATABASE_URL/GEMINI_API_KEY and existing ASR access, then run migration/import. `.env.example` is a template, **not automatically loaded**. Open http://127.0.0.1:8000; API docs are at /docs.

## Scheme admin panel

Open `/admin` to manage existing PostgreSQL schemes. Set `ADMIN_PASSWORD` and
`ADMIN_SESSION_SECRET` securely in the server process environment first; the
signing secret must be randomly generated and at least 32 characters long.
Set `ADMIN_COOKIE_SECURE=true` when serving over HTTPS. No new dependencies or
database migrations are needed. Existing database configuration remains required.

The panel provides name search, Central/State, state/UT, confidence, normalized
category and active/archive filters, pagination, scheme details, metadata/source
editing, activation/deactivation and validated eligibility-rule CRUD. Deactivation
keeps scheme data and rules; there is no scheme deletion endpoint. Source links
are clickable but the server never fetches them. Confidence and manual review
conditions are never automatically resolved by editing metadata or rules.

This is lightweight administrative protection for a demo/student project, not
enterprise authentication. One environment password starts an eight-hour signed
HttpOnly, SameSite=Strict session. Admin pages and APIs are protected; writes
also require a CSRF token. Sign out clears the browser cookie. See
[admin setup, operations and verification](docs/admin-panel.md).

**Reimport behavior:** the existing dataset importer remains source-authoritative.
Running it again can overwrite admin metadata, replace edited/added/deleted rules,
reactivate curated schemes and rearchive legacy schemes. Do not use reimport as a
routine startup step after making admin edits; back up changes before an intentional
dataset refresh.

## Dataset policy

The importer reads only the six supplied Central/State master, eligibility-rules and needs-review CSVs. It validates required headers, identities, dates and rules; normalizes empty cells and booleans; preserves original metadata/URLs/confidence; deduplicates; and writes one transaction. State identity includes jurisdiction. Repeated imports reconcile importer-owned rules and metadata. Source removals/renames are not automatically deleted; review/archive them explicitly.

Malformed individual rows are reported while valid rows remain usable. Missing headers/unreadable files abort before writes. Unsupported rules and master conditions not fully represented by scalar rules remain visible manual conditions. Raw categories are retained beside an 18-category display taxonomy; categories never affect eligibility. No websites were fetched or facts invented.

Family annual income and personal monthly income remain separate from personal annual income. Six State income rules map to family_annual_income using their supplied FAMILY/FAMILY_ANNUAL metadata and annual-income rule labels. Three ambiguous self/spouse/family scopes stay manual. There is no assumed income-period conversion.

See [integration report](docs/scheme-dataset-integration-report.md), [audit](docs/dataset-audit.json), and [two-run import evidence](docs/dataset-import-verification.json).

## Applicant extraction

Retain LLM_PROVIDER=gemini, LLM_MODEL=gemini-3.5-flash-lite, securely supplied GEMINI_API_KEY and LLM_TIMEOUT_MS=30000. The existing official google-genai adapter remains.

The profile supports age, gender, state/UT, personal annual income, family annual income, personal monthly income, social category, occupation, employment, student/education/farmer status, disability/percentage, BPL, rural/urban residence, widow/minority/marital status. Rare scheme-specific conditions remain manual.

Sensitive attributes require explicit applicant statements. Names, surnames, location, language, occupation or gender cannot establish caste/category, disability, minority or widow status. Unknowns remain null; explicit false remains false; corrections replace only explicit values. Age is an integer 0–120; money fields are nonnegative integer rupees; disability percentage is 0–100. Gender remains Male/Female; rural/urban is Rural/Urban. The original three extraction keys remain required; new nullable fields may be omitted by older clients/mocks and default to unknown. Schema validity is not proof of semantic accuracy.

Missing credentials, invalid output and provider failures preserve profile values and return a Hindi retry. Normal tests never contact Gemini. The live smoke command remains:

```powershell
python scripts/test_profile_extraction.py 'मैं पच्चीस साल का लड़का हूं और मेरी वार्षिक आय पाँच हजार है' --eligibility
```

Verify extracted 25/Male/5000. The researched catalogue also requires additional facts/manual checks; old prototype eligibility counts no longer apply. --expected accepts exact expected JSON; --current-profile supplies known context.

## Redis sessions

Keys use bhashasetu:session:{session_id}. Hashes contain non-null profile fields plus _attempts and _finalized for API compatibility, never audio/history/prompts/results/scheme copies. IDs accept 1–128 letters, digits, underscore or hyphen. The omitted-ID default remains default; callers should always send distinct IDs.

HSET updates only explicit fields. Null never erases; false is stored explicitly. HSET, attempt increment, expiry and snapshot retrieval share a transaction. Different-field updates preserve each other; same-field corrections use last Redis write wins. Completion metadata uses an optimistic WATCH guard, not a distributed lock, and never controls eligibility.

The sliding TTL defaults to 1,800 seconds. Existing-key reads, merges and touch refresh it. Missing/expired reads return empty profiles without creating immortal keys. Startup never clears Redis. State survives Python restarts until expiry; Redis server restart durability depends on Redis persistence settings. Extraction failures leave values/counters unchanged but the successful initial read refreshes TTL.

One synchronous client/pool is reused per app lifespan and closed at shutdown. Conversation runs in the existing thread pool. Redis failure/corrupt values return sanitized 503; invalid IDs return 422. There is no fallback. SESSION_BACKEND=memory explicitly selects local test/dev storage with matching TTL semantics, without cross-process persistence. rediss:// supports TLS; never commit Redis credentials. See [Phase 3 report](docs/phase3-report.md).

## ASR and audio

Keep ASR_PROVIDER=indicconformer, ASR_MODEL_ID=ai4bharat/indic-conformer-600m-multilingual, ASR_LANGUAGE=hi, ASR_DECODER=ctc and ASR_LOG_TRANSCRIPTS=false. Retain cached Hugging Face login or securely supplied HF_TOKEN. The official AutoModel/Hindi CTC and FFmpeg mono 16 kHz path are unchanged. FFMPEG_BINARY is optional; otherwise imageio-ffmpeg supplies it. Do not co-install CPU/GPU ONNX Runtime packages.

`python scripts/test_asr.py test.mp3` remains an explicit live check. Whisper is an explicit rollback via ASR_PROVIDER=whisper and ASR_MODEL_ID=base, never automatic. gTTS failure retains text with null audio_url. Historical setup details are in [ASR report](docs/asr-report.md).

## API and explainability

Existing routes /, /speech-to-text, /api/schemes, /api/schemes/{id} and /api/eligibility remain. Speech keeps multipart file/session_id, all response keys, and session.income. Additional known profile fields appear without replacing old keys.

Scheme responses add researched metadata/provenance. Eligibility preserves checks/missing_fields and adds manual_conditions plus manual_review_required reason. A known failed scalar rule gives NOT_ELIGIBLE. Otherwise missing data, absent rules or mandatory manual conditions give NEED_MORE_INFORMATION. ELIGIBLE requires all checks passing and no unresolved conditions. Wrong-state checks remain explainable; archived schemes are excluded. The frontend is unchanged; detailed conditions are available in API JSON and speech mentions manual confirmation.

## Verification and limits

```powershell
python -m pytest -q
python -m compileall -q backend tests migrations scripts
python -m pip check
# Only after securely configuring disposable integration URLs:
python -m pytest tests/test_postgresql.py tests/test_redis_integration.py -q
```

Ordinary tests use actual migrations on isolated SQLite databases, Redis command mocks/injected memory, and mocked Gemini/ASR/TTS. TEST_DATABASE_URL and TEST_REDIS_URL gate live integrations. Tests own temporary schemas/prefixes and never flush Redis.

The user reports prior live verification in their working environment. This run lacks integration URLs, so new migration/profile changes are verified offline only. Sessions remain temporary and unauthenticated; overlapping whole turns are not serialized. No perfect language understanding, qualification hierarchy, legal correctness, exhaustive scheme coverage or production reliability is claimed. Admin, scraping, RAG, vectors and distributed locks remain out of scope.
