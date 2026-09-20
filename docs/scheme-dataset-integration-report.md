# Researched scheme dataset integration

## Result and verification boundary

The six supplied files are integrated through an additive schema migration and an idempotent importer. The active catalogue is **297 schemes: 79 Central and 218 State**, with **610 structured rules**. Another **65 source rule rows are preserved as unresolved manual conditions**, not discarded or guessed. Every supplied scheme has additional conditions that need confirmation, so scalar matches alone cannot produce unconditional ELIGIBLE results.

All counts below were measured with actual migrations, imports and rule evaluation in isolated test databases. **No production database was modified.** DATABASE_URL, TEST_DATABASE_URL and TEST_REDIS_URL are unavailable in this shell; the user's previously verified live services are accepted as context but are not a fresh live pass for this migration/profile expansion. No external research or government website access occurred during the dataset task.

## Previous architecture and baseline

FastAPI delegates ASR to IndicConformer, applicant extraction to a Gemini/Pydantic provider and decisions to a generic PostgreSQL-backed rule engine. Redis session work was already being integrated when the researched-dataset request arrived; that work is retained and documented in `phase3-report.md`.

The actual full suite at the dataset handoff was **209 passed, 2 skipped, 6 warnings, 1,073 subtests passed in 52.01s**. Two additional Redis API tests then passed separately, bringing the pre-dataset test collection to 211 tests. This distinguishes measured runs from the user's approximately 211-test baseline. The original 67-row fixture remains for Phase 1 regression checks; production import now defaults to the researched files.

## Dataset audit

| Source | Master schemes | Rule rows | Needs-review rows |
| --- | ---: | ---: | ---: |
| Central | 79 | 227 | 4 |
| State | 218 | 448 | 1 |
| Total | **297** | **675** | **5** |

No malformed column rows, exact duplicate scheme/rule rows, duplicate source identities or missing rule/review references were found in the supplied files. The 5 review rows reference existing masters, not extra schemes. Confidence totals: VERIFIED 269, LIKELY_ACTIVE 23, NEEDS_REVIEW 5. All 227 Central rule groups are ALL. The actual operator vocabulary is equals, boolean_equals, >=, <= and in; aliases map into the existing engine.

State coverage is the supplied 11 states: Uttar Pradesh 19, Bihar 19, Madhya Pradesh 18, Rajasthan 22, Maharashtra 20, Gujarat 20, Goa 20, Chhattisgarh 20, Jharkhand 20, Odisha 20 and West Bengal 20. Central coverage includes 77 All India rows and two explicitly regional rows. This is not coverage of every Indian scheme or state.

`dataset-audit.json` records complete field/operator/category frequencies, normalized categories, every preserved rule with source file/row/reason/raw data, confidence totals and structural issues. `dataset-import-verification.json` records exact two-run results and SHA256 values for all six supplied CSVs. Source CSVs were not edited.

## Schema and migration

Migration: **`migrations/versions/0002_curated_scheme_metadata.py`**, following 0001. It adds only nullable columns and preserves old IDs/records/rules. Upgrade/downgrade and ORM metadata consistency were exercised on a migrated test database.

Existing `name`, `description`, `state`, `official_url` and `category` fields represent scheme_name, short_description, state_or_ut, official_source_url and normalized category. New columns store government level, department, raw category, confidence, secondary URL, last-checked date, benefit type/summary/amount, application method/URL, confidence notes and other eligibility conditions. JSON `source_metadata` preserves every master cell and matched review row; `manual_conditions` stores unresolved constraints. EligibilityRule gains nullable source_metadata while keeping its generic field/operator/value/value_type model.

No per-condition scheme columns, new service architecture or infrastructure were introduced. The pure engine, not Gemini, still determines statuses. The existing EligibilityService and repositories retain their roles; metadata import uses a single SQLAlchemy transaction.

## Applicant attributes and Gemini

The profile has 19 fields: age, gender, annual_income, state_or_ut, family_annual_income, individual_monthly_income, social_category, occupation, employment_status, student_status, education_level, farmer_status, disability_status, disability_percentage, bpl_status, rural_urban, widow_status, minority_status and marital_status.

These cover recurring source concepts. Rare landholding, institution approval, component eligibility, previous benefits, registrations, banking exclusions and similar conditions stay manual. The generic API keeps legacy state/is_disabled fields for backwards compatibility; the curated dataset uses the new explicit names.

Age remains a strict integer 0–120; money fields are strict nonnegative integer rupees; disability percentage must be finite and within 0–100; booleans reject string coercions. Existing Male/Female spelling is retained. New fields are nullable and default to unknown if omitted; original three required extraction keys remain compatible with existing fixtures. Redis decodes each supported type through UserProfile, preserving false values and non-null-only updates.

The narrow Gemini prompt now describes the additional fields, explicit negative statements and separate income scopes. It forbids sensitive inference from name/surname/location/language/occupation/gender and forbids fabricating qualifications. Gemini still receives no scheme rules and never decides eligibility. The adapter, SDK, model configuration and failure behavior remain. Mocked extraction tests validate the expanded contract; they do not prove live language accuracy.

## Rule conversion and uncertainty

| Rule disposition | Count | Treatment |
| --- | ---: | --- |
| Typed scalar rules | **610** | Stored and evaluated with generic operators |
| Specialized fields | 37 | Original expression and notes preserved for manual review |
| Qualified/ambiguous categorical values | 24 | Preserved whole; not forced into misleading exact matches |
| Ambiguous self/spouse/family income scope | 3 | Preserved without guessing ownership |
| Fractional age threshold | 1 | Preserved; no rounding into integer-age rules |
| Malformed supplied rows skipped | **0** | None in supplied source |

The 610 rules include 18 fields listed in the audit. Source residence_type maps to rural_urban. Six State annual-income rules map to family_annual_income based on their FAMILY/FAMILY_ANNUAL master data and annual-income rule label. Monthly personal income remains separate and is never multiplied into annual income. A source annual-turnover or household-only condition outside the recurring primary profile stays manual.

Master conditions not fully represented by the scalar rules are also preserved: family, domicile, district, landholding, descriptive status/education constraints and other eligibility text. Non-VERIFIED source/rule confidence and needs-review rows explicitly add uncertainty. All 297 schemes have at least one such condition. No API permits an applicant to silently waive these conditions.

Results retain per-rule actual/operator/expected/passed and missing_fields. `manual_conditions` and `reason=manual_review_required` are additive. A known failing scalar rule still yields NOT_ELIGIBLE. Otherwise an unresolved mandatory condition yields NEED_MORE_INFORMATION even when all scalar checks pass. An ordinary manually created/legacy test scheme with complete rules and no additional conditions can still produce ELIGIBLE. Speech mentions manual confirmation; detailed conditions remain visible in JSON without redesigning the frontend.

## Import, deduplication and categories

The importer validates required headers before any database writes. It normalizes empty cells to null metadata, casts rules by declared field/type/operator, normalizes boolean text, and preserves raw provenance. Individual malformed rows are reported; missing headers/unreadable files abort. Malformed rule-column layouts conservatively mark rule completeness unresolved for the affected source file. Unsupported requirements never become an implicit pass.

State identities include state and name. Central names are unique within the supplied Central master and resolve within that file. Stable source-key hashes isolate curated imports from legacy/manual records. Exact duplicate identities/rules do not inflate counts. Metadata changes update existing IDs; importer-owned rules are reconciled by normalized signature. Repeating the same import creates nothing. Source removals/renames do not automatically delete prior identities; archive/review those explicitly. Concurrent operator-run imports are not a supported scheduling mechanism.

The display taxonomy contains 18 categories: Agriculture, Education, Scholarship, Women & Child, Health, Disability, Pension, Employment, Skill Development, Entrepreneurship, MSME, Housing, Food Security, Labour, Social Welfare, Tribal Welfare, Minority Welfare and Financial Assistance. Original categories remain in raw_category and source metadata. Classification is deterministic display grouping only, never eligibility logic.

## Legacy handling and exact database counts

The old Hindi-named 67 rows have no exact name matches against the English researched names, which does **not** imply 67 distinct new schemes. Clear conceptual overlaps include PM-KISAN, PM Ujjwala, Jan Dhan and Atal Pension. The old generic housing/pension names can correspond to multiple researched components. Their broad age/income thresholds lack researched provenance; for example the old PM-KISAN row includes age/income limits that must not be carried into the curated row. No guessed bilingual merge is performed.

All 67 legacy entries are retained **only as inactive archives**, preserving 217 historical rule rows. None is retained as an active unique scheme. Unrelated manually authored records are not deleted. The legacy CSV/helper remain regression fixtures, not the default dataset.

| Scenario | Before | Stored schemes after | Active unique Central/State | Active total | Active rules | All stored rules |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Clean import | 0 | 297 | 79 / 218 | 297 | 610 | 610 |
| Upgrade old catalogue | 67 | 364 | 79 / 218 | 297 | 610 | 827 |
| Repeat clean import | 297 | 297 | 79 / 218 | 297 | 610 | 610 |
| Repeat upgrade import | 364 | 364 | 79 / 218 | 297 | 610 | 827 |

Both repeat runs report **created=0, updated=0, unchanged=297, rules_created=0**. The upgrade first run archives exactly 67 legacy records; a repeat archives zero more. Stored archive counts are not represented as extra active scheme coverage.

## Test evidence and dependencies

Full combined suite: **238 passed, 2 skipped, 6 warnings, 1,073 subtests passed in 37.90s**. The focused dataset/extraction suite passed 72 tests. Tests cover CSV loading, headers/malformed/orphan rows, duplicates, metadata updates, two-run idempotency, archive preservation, Central/State handling, state mismatches, expanded fields, distinct income scopes, boolean false, missing/manual conditions, Redis field encoding, mocked Gemini, API metadata/results and migration preservation. Original rules/CSV/ASR regression tests still pass.

Compilation succeeded. Optional PostgreSQL and Redis tests are skipped because their integration URLs are absent. They were extended to exercise curated import/idempotency and new Redis fields when configured. No real Gemini call occurs in pytest.

`pip check` still reports the same ten pre-existing shared-environment conflicts: protobuf constraints from Google common protos, legacy generativelanguage, grpcio-status, proto-plus, Snowpark and Streamlit; Selenium typing-extensions; Snowflake connector filelock; Streamlit packaging and pandas. No new Redis/Gemini dependency conflict was introduced and no ASR dependency was reinstalled. The dataset phase adds no package dependency. The preceding Redis phase adds only redis==8.1.0.

Warnings are existing Starlette/httpx/anyio/dateutil deprecations and three test_database helpers collected as returning-value tests. No test failure is hidden by skipping it; the only skips are explicit real-service gates.

## Changed files and Git/security status

Dataset implementation changes: models.py; schemas.py; normalization.py; rules.py; profile_schemas.py; profile_extraction_service.py; session_store.py type encoding; conversation_service.py field labels/manual message; db/import_dataset.py (new); db/seed.py default CLI; migration 0002 (new). Tests: test_dataset.py (new), test_profile_extraction.py, test_postgresql.py, test_redis_integration.py. README/ARCHITECTURE and this report/audit/evidence were updated or added; .gitignore protects local recordings and model caches. The preceding Redis integration files are listed in phase3-report.md.

`git status --short` reports **not a git repository** for this supplied workspace. Consequently a genuine `git diff --stat` cannot be produced; this file inventory is not presented as Git output. No repository was initialized and nothing was committed. Source CSVs, frontend, ASR code and TTS behavior remain unchanged. Environment files, credentials, local audio, virtual environments and caches are excluded; no secret value is included in these reports.

## Commands and remaining verification

Use the working environment and keep credentials secure:

```powershell
python -m backend.db.import_dataset --audit-only --report docs/dataset-audit.json
python -m alembic upgrade head
python -m backend.db.import_dataset
python -m backend.db.import_dataset
python -m pytest -q
python -m compileall -q backend tests migrations scripts
python -m pip check
python -m uvicorn backend.server:app --reload
```

Set DATABASE_URL for the target PostgreSQL database and keep existing Gemini/ASR settings. Redis defaults are SESSION_BACKEND=redis, REDIS_URL=redis://localhost:6379/0, SESSION_TTL_SECONDS=1800 and REDIS_KEY_PREFIX=bhashasetu:session. Use secure rediss:// credentials for a managed server. `.env.example` is not loaded automatically.

To reproduce this agent's local offline environment, set PYTHONPATH=.test-deps and PYTHONIOENCODING=utf-8 before running Python. To verify real services, securely set disposable TEST_DATABASE_URL and TEST_REDIS_URL, then run the respective tests. Apply migration/import to the user's configured database through the commands above, and inspect a real expanded-profile microphone conversation. Production import, live PostgreSQL migration, expanded Redis persistence and expanded Gemini semantics remain to be verified there.

## Known limitations

This is curated source data, not independent or legally authoritative government verification. All current schemes need some manual confirmation; the unchanged frontend does not add a detailed review UI. Generic string categories do not model qualification/occupation hierarchies or complex OR/component logic. Profile gender remains the existing Male/Female contract. Sessions are temporary/unauthenticated, whole concurrent turns are not serialized, and Redis server persistence depends on deployment settings. Source renames/removals need explicit review. No admin, scraper, RAG, vector database, queue, distributed lock or authentication platform was added.
