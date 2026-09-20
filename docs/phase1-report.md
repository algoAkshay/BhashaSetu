# Phase 1 engineering report

Implementation and isolated verification are finished. **Live PostgreSQL execution remains unverified**: the supplied environment has no configured `DATABASE_URL`/`TEST_DATABASE_URL` and no detected PostgreSQL installation/service. The PostgreSQL test is explicitly skipped, not counted as a pass. No real database was provisioned or modified.

## A. Baseline

- Audited source, frontend, data, tests, dependencies and documentation before edits; see [audit](phase1-audit.md).
- Actual flow: FastAPI upload → Whisper base/Hindi → regex extraction → process-local sessions → CSV predicate → Hindi text/gTTS.
- Only application routes were `GET /` and `POST /speech-to-text`, plus static/audio mounts.
- CSV contained **67 schemes**, not the documented 100+. Columns: `scheme_name,min_age,max_age,gender,max_income`; 16 rows restricted gender.
- Existing tests: `python -m unittest discover -s tests -v` → **6 passed, 0 failed**. `python -m pytest -q` could not run because pytest was not installed; the suite is unittest-based.
- Eligibility used inclusive age bounds and income ceiling, plus exact gender or `Any`. It refused all results if any personal field was missing.
- Second-turn defaults fabricated age 30, Male and income 100000. One baseline test asserted this unsafe behavior.
- Planner/executor/evaluator modules were unused. Documentation overstated autonomous reasoning and production readiness.
- No Git metadata or lint configuration was supplied.

## B. Implementation

### Files added

```text
.env.example
alembic.ini
requirements-dev.txt
backend/config.py
backend/models.py
backend/normalization.py
backend/schemas.py
backend/rules.py
backend/api/__init__.py
backend/api/routes.py
backend/db/__init__.py
backend/db/session.py
backend/db/seed.py
backend/repositories/__init__.py
backend/repositories/scheme_repository.py
backend/repositories/rule_repository.py
backend/services/__init__.py
backend/services/eligibility_service.py
backend/services/scheme_service.py
backend/services/conversation_service.py
migrations/env.py
migrations/script.py.mako
migrations/versions/0001_schemes_and_rules.py
tests/__init__.py
tests/support.py
tests/test_rules.py
tests/test_database.py
tests/test_api.py
tests/test_postgresql.py
docs/phase1-audit.md
docs/phase1-report.md
```

### Files changed

`backend/server.py`, `backend/logic.py`, `tests/test_logic.py`, `requirements.txt`, `.gitignore`, `README.md`, `ARCHITECTURE.md`.

### Files removed

Unused `backend/agent/planner.py`, `executor.py`, `evaluator.py`, and the empty package `__init__.py`.

The CSV, frontend, demo assets, `backend/__init__.py`, and historical `backend/tts.py` helper were retained unchanged. The server still uses its original TTS implementation.

### Database and layering

- PostgreSQL-only runtime configuration through `DATABASE_URL`; SQLAlchemy 2, psycopg 3, Alembic and Pydantic 2.
- `schemes`: id, unique nullable import source key, name, nullable metadata fields, active flag and timestamps.
- `eligibility_rules`: id, scheme foreign key with delete cascade, field, operator, JSON value, value type and timestamps.
- Indexes: active scheme filtering, rules by scheme, and unique seed identity; no unused category/state indexes.
- Migration `0001` creates both tables and indexes; deployment runs it explicitly. Startup does not modify data.
- Seed prevalidates the complete CSV, creates all rows in an outer transaction, uses per-scheme savepoints, and skips existing source identities. A corrupt file or database failure leaves no partial import.
- Repositories contain queries and flushes; services coordinate reads, writes and evaluation; HTTP routes validate input and delegate. Active schemes/rules are loaded in two batched queries.
- Pure rule evaluation dispatches eight operators without scheme-name branches or executable expressions. Field/type/operator/value validation runs for repository writes and again on persisted rules during evaluation.
- Per-rule checks expose normalized actual/expected values, pass/fail/unknown, and deduplicated missing fields.
- HTTP additions: `GET /api/schemes`, `GET /api/schemes/{id}`, `POST /api/eligibility`. No write/admin endpoints.

## C. Behavior changes

1. Runtime eligibility reads PostgreSQL; CSV reads exist only in the explicit seed command and regression tests. Missing DB configuration fails explicitly instead of falling back.
2. Missing age/gender/income remain unknown. The former defaults test now asserts null fields and an unfinished session. Other baseline extraction/session/lookup assertions are preserved; lookup uses the migrated test database.
3. A missing required field yields `NEED_MORE_INFORMATION` when no known rule fails. A known failure is conclusive and returns `NOT_ELIGIBLE`, while still listing other missing fields.
4. Unrestricted-gender schemes no longer need a gender value. Complete valid profiles retain the old CSV results.
5. Empty-rule schemes return `NEED_MORE_INFORMATION` with `reason=no_rules`; inactive schemes are excluded from batch evaluation.
6. Strings are trimmed/case-folded, numeric strings are deliberately parsed, malformed numeric inputs are rejected, and booleans are strict. Decimal JSON values use strings to preserve precision.
7. The speech JSON retains its old keys and scheme-name list, adding `eligibility_results` and `missing_fields`. Incomplete sessions remain open for additional information; completed sessions still reset on the next turn.
8. The legacy `apply_defaults_if_needed` Python name remains as a compatibility hook but assigns no personal values. Unused agent modules and inaccurate documentation claims were removed.

## D. Test results and commands

Validation used Python 3.12. Missing Alembic, psycopg and HTTP test dependencies were installed locally into ignored `.test-deps`; no global Python packages were changed. Tests used SQLAlchemy 2.0.54, Alembic 1.20.0, psycopg 3.3.6 and httpx 0.28.1 with the environment's FastAPI 0.139.0/Pydantic 2.13.4.

Exact final test command in this environment:

```powershell
$env:PYTHONPATH = '.test-deps'
python -m unittest discover -s tests -v
```

**62 tests discovered: 61 passed, 0 failures, 0 errors, 1 skipped** (live PostgreSQL). Final run: 13.488 seconds. Review added startup, repository consistency and decimal JSON round-trip checks; the full suite was rerun after the last code change. Very small decimal thresholds are stored in fixed-point form so they remain valid when read back.

Covered all eight operators; age/income boundaries; one/several missing fields; nulls; no defaults; numeric strings and malformed values; unsupported fields/operators; boolean and decimal types; all-pass and multiple-failure outcomes; no-rules and inactive behavior; seed corruption/idempotency; ORM updates and read consistency; atomic rollback; foreign keys and cascades; migration up/down/up and ORM schema comparison; sanitized error responses; FastAPI startup/configuration failures; static UI; and mocked speech/TTS compatibility including temporary-file cleanup.

Regression verification evaluates **1,012 row-boundary profiles against all 67 schemes (67,804 scheme comparisons)**, plus six representative complete profiles, using a frozen copy of the original predicate independent of the new converter.

Additional commands:

```powershell
python -m compileall -q backend tests migrations
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@localhost:5432/bhashasetu'
python -m alembic upgrade head --sql
```

Compilation passed for all **30 Python source files**. AST parsing, conflict-marker/trailing-whitespace checks passed; **28 backend/test modules** imported successfully. Alembic generated PostgreSQL transaction/DDL successfully, including SERIAL keys, JSON values, timezone timestamps, foreign keys and indexes. This command compiles SQL; it does not contact PostgreSQL.

Manual source searches:

```powershell
rg -n 'csv|DictReader|read_csv|schemes\.csv|eval\(|scheme_name\s*==' backend
rg -n 'redis|langchain|faiss|celery|kafka|docker|pgvector' requirements.txt requirements-dev.txt
rg -n '30|100000|Male|except Exception|SELECT|select\(' backend
```

Findings: CSV loading only in `backend/db/seed.py`; no eval or scheme-name conditions; no forbidden dependency additions; SQL queries only in repositories. Remaining `100000` is the existing lakh conversion factor; `Male` occurs only in gender extraction and seed validation. No fabricated eligibility defaults remain. The broad TTS exception handler is intentional and now logs an error type before returning text-only output; no errors are silently turned into eligibility decisions.

No existing linter was configured, so no lint pass is claimed. `git status --short` and `git diff --check` could not operate because this directory is not a Git checkout; direct file/source checks were used. Source bytecode caches were cleaned after verification. `.test-deps` is an intentionally retained, ignored local dependency directory.

The installed Starlette emits an httpx deprecation warning. The tests pass; this does not affect application runtime. No external Whisper/model/TTS calls were made by tests.

## E. Database verification

| Check | Result |
| --- | --- |
| Actual Alembic migration on isolated SQLite | Upgrade/downgrade/upgrade passed |
| ORM vs migrated schema | No differences |
| PostgreSQL offline DDL compilation | Passed |
| Initial seed | 67 processed, 67 created, 0 skipped, 217 rules, 0 errors |
| Duplicate seed | 67 processed, 0 created, 67 skipped, 0 rules, 0 errors |
| Invalid row or mid-import DB failure | Entire import rolled back |
| Scheme plus invalid rule | Savepoint rolled back; no partial scheme |
| Repository edits then reseed | Edits, extra rules and inactive state preserved |
| Missing configuration via seed CLI | Explicit JSON error; exit code 1 |
| FastAPI lifespan, routes and static UI | Passed against migrated/seeded injected test DB |
| Live PostgreSQL migration/seed/startup | **Not run: no server/connection available** |

Manual evaluation on the migrated/seeded isolated DB:

| Attributes | Eligible | Not eligible | Need more information |
| --- | ---: | ---: | ---: |
| age 32, Male, annual income 150000 | 40 | 27 | 0 |
| age 5, Female, annual income 100000 | 15 | 52 | 0 |
| age 64, Female, annual income 150000 | 28 | 39 | 0 |
| age 64 only | 0 | 39 | 28 |
| no attributes | 0 | 0 | 67 |

For the first seeded scheme, age 64 without income produced `missing_fields=["annual_income"]`. No attributes produced `["age", "annual_income"]`; no gender was requested for that unrestricted-gender scheme.

To close the remaining live verification gap, set `TEST_DATABASE_URL` to a disposable PostgreSQL database and run:

```powershell
python -m unittest tests.test_postgresql -v
```

The opt-in test isolates itself in a random schema and verifies native migration DDL, JSON membership/decimal/boolean rules, timezone timestamps, foreign-key cascades, transactions, seed reruns and downgrade/upgrade. Then configure the application's `DATABASE_URL`, run the documented migration and seed commands, and start Uvicorn.

## F. Remaining limitations

- No live PostgreSQL execution or actual microphone/Whisper/TTS inference was verified in this environment. Those are separate deployment checks; isolated tests and SQL compilation do not substitute for them.
- The bundled data is prototype reference data and is not validated against official policy. No fake metadata or additional eligibility requirements were invented.
- Sessions are process-local and have no expiry or cross-worker consistency. Regex extraction and the existing speech processing/concurrency limitations remain.
- Speech extraction currently provides only age, gender and income. State/disability rules can be evaluated through the structured API but need future extraction support for voice use.
- Rules support flat AND only. Unknown attributes are rejected until registered. No admin write endpoints, rule editor or automatic dataset synchronization exist.
- Rerunning the importer skips existing source keys; changing the source name creates a new import identity. Database edits are the runtime source of truth.
- No Git commit/diff could be created or inspected because no repository metadata was supplied. No real credentials were written; `.env.example` contains placeholders only.

## G. Next phase (not implemented)

Future phases may include LLM structured extraction, proper conversational slot filling, Redis-backed sessions and an admin UI. This phase adds none of those components, semantic search, vector stores, scrapers, queues, Docker or microservices.
