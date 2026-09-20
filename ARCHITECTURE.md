# Bhasha Setu architecture

Browser → IndicConformer → Gemini structured facts → Pydantic → Redis profile → EligibilityService → PostgreSQL rules → explainable Hindi response/gTTS.

## Responsibilities

- server.py owns HTTP/lifecycle, upload cleanup and thread-pool delegation; ASR/audio/TTS remain unchanged.
- profile_extraction_service.py understands explicit language only. profile_schemas.py validates nullable applicant facts.
- session_store.py owns Redis hashes/TTL/partial updates and the explicit memory test/dev backend.
- conversation_service.py merges, asks for missing slots and builds responses; it does not decide eligibility.
- EligibilityService and repositories retain their existing coordination/query roles.
- rules.py performs generic AND comparisons plus explicit manual uncertainty. normalization.py defines field types.
- import_dataset.py audits supplied CSVs and imports metadata/rules transactionally. seed.py defaults to this importer; legacy seeding remains for regression fixtures.
- logic.py is deprecated historical Python compatibility code, not imported by the production conversation path.

## PostgreSQL data and migration

Scheme retains existing name/description/state/category/official_url fields. Additive migration 0002 adds nullable government level, department, raw category, confidence, secondary source URL, checked date, benefit/application fields, notes, other conditions, manual_conditions JSON and source_metadata JSON. EligibilityRule retains generic field/operator/value/value_type plus source_metadata. Existing IDs/rows/rules/indexes survive migration; no dedicated scheme column is added for each applicant attribute.

The source JSON retains all master/review cells. Rule provenance preserves original aliases, confidence and notes. Only supplied CSVs are used; source VERIFIED labels are not independent verification. Rule aliases translate into existing ==, !=, <, <=, >, >=, IN and NOT_IN operators. No scheme-name branches, arbitrary expressions or grouped-rule framework is introduced.

Central names resolve within the Central file; State identities include state. Stable source-key hashes separate curated records from manual/legacy data. One transaction updates metadata and reconciles importer-owned rules. Duplicates are ignored, changed source rules replace old imported rules, and missing/renamed source identities are not automatically deleted. Malformed individual rows are reported; missing required headers/unreadable CSVs abort before mutation.

The original 67 Hindi prototype records have broad age/gender/income criteria with no researched provenance. They are archived, not blindly appended, guessed equivalent by name, or destroyed. Active curated counts are 297 schemes/610 scalar rules. Sixty-five source rules and unencoded master constraints remain manual. All 297 schemes currently need some manual confirmation.

## Explicit facts and uncertainty

The profile adds recurring state/UT, family annual income, personal monthly income, social category, occupation, employment, student/education/farmer status, disability/percentage, BPL, rural/urban and widow/minority/marital status. Existing age/gender/personal annual income remain. Rare institution/subsidy/exclusion conditions stay manual. Income scopes are never copied or annualized by assumption.

Gemini uses the existing official JSON-schema adapter. Sensitive facts require explicit statements and cannot be inferred from names/surnames/language/location or related attributes. False is meaningful, null stays unknown, and corrections replace only explicit facts. New fields may be omitted by older three-field fixtures/clients and default to unknown. The LLM receives no scheme rules and makes no eligibility decisions.

The pure engine retains every scalar check. A known failure gives NOT_ELIGIBLE. Otherwise missing fields, missing rules or unresolved manual conditions give NEED_MORE_INFORMATION. ELIGIBLE requires passing rules and no outstanding conditions. Manual conditions are exposed even alongside a known failure. A conservative result does not mean the applicant is rejected by the government; it means the prototype cannot settle all requirements. The admin panel edits stored scheme metadata and structured rules; it does not approve eligibility or automatically resolve manual conditions.

## Scheme administration

`/admin` uses the existing vanilla frontend conventions and protected HTML outside
the static mount. `backend/api/admin.py` delegates queries and writes through
`AdminService` and the existing repositories. Pydantic admin inputs whitelist
editable fields; rule inputs reuse `RuleDefinition`. There are no schema changes.
Schemes are soft-deactivated via `is_active`; only rules support hard deletion.
Imported manual conditions and source identities are preserved.

A single environment password (`ADMIN_PASSWORD`) and signing secret
(`ADMIN_SESSION_SECRET`) provide lightweight demo authentication. An eight-hour
signed HttpOnly/SameSite=Strict cookie protects pages and APIs; mutation requests
also require a session-specific CSRF header. `ADMIN_COOKIE_SECURE` enables HTTPS
cookie protection. Logout clears the cookie; secret rotation invalidates all
sessions. No user table, registration or external auth infrastructure is added.
See [admin operations and limitations](docs/admin-panel.md), including the existing
source-authoritative importer's ability to overwrite manual edits on reimport.

## Redis lifecycle and concurrency

SESSION_BACKEND defaults to redis. One reusable synchronous redis-py client/pool is created per app lifespan without opening a socket/pinging. Existing conversation work runs in a thread pool; shutdown closes the client. The key is REDIS_KEY_PREFIX + ':' + validated session ID (default bhashasetu:session:{id}). IDs accept 1–128 ASCII letters/digits/underscore/hyphen.

Hashes hold only non-null profile fields plus _attempts/_finalized compatibility metadata. Integers and finite percentages are decoded and validated via UserProfile; booleans use true/false strings. No transcripts/audio/history/prompts/scheme data/results are stored. Invalid stored data fails the whole read safely rather than supplying corrupt inputs to the engine.

Merge validates before mutation. HSET writes explicit fields only, followed by HINCRBY, EXPIRE and HGETALL in one transaction. Different-field concurrent updates survive; same-field updates use Redis write order. WATCH protects completion metadata from a concurrently changed key and is not a distributed lock. Metadata never controls eligibility. Whole turns are not serialized and exactly-once processing is not provided; a lost response after commit can leave an applied update despite HTTP failure.

Sliding TTL defaults to 1800 seconds. Existing-key reads, merge, touch and completion refresh it; missing/expired reads return an empty profile. Startup never clears keys. Python restarts preserve Redis state until TTL expiry; Redis server durability is deployment-dependent. Failures/corruption produce sanitized 503, invalid IDs 422. No silent fallback. Explicit memory mode has matching TTL/merge behavior but remains local to one process.

## Verification boundaries

Normal pytest uses actual Alembic migrations and deterministic engine on isolated databases, Redis command mocks/injected memory, and mocked external services. TEST_DATABASE_URL and TEST_REDIS_URL gate live tests owning only temporary schemas/prefixes. This shell has neither URL; prior user-reported live verification is not fresh evidence for migration 0002 or expanded profiles.

See docs/scheme-dataset-integration-report.md, docs/dataset-audit.json, docs/dataset-import-verification.json and docs/phase3-report.md. This is an interview/student prototype, not a legally authoritative government portal.
