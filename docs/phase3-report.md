# Phase 3: Redis session storage

This report covers the Redis work begun before the subsequent researched-dataset request. The dataset extension is documented separately in `scheme-dataset-integration-report.md`; migration 0002 belongs to that later request, not to Redis storage.

## A–D. Baseline, previous behavior and file changes

Before Redis edits: **148 passed, 1 skipped, 6 warnings, 1,073 subtests passed in 28.64s**. `backend/logic.py` held a module dictionary with age, gender, income, attempts and finalized. Browser IDs isolated entries only within one Python process. Successful turns merged non-null facts and preserved completed profiles for corrections; restart lost everything and there was no TTL.

Changes: `backend/services/session_store.py` adds the small store protocol, Redis implementation, explicit memory implementation and typed snapshots. `backend/config.py` adds validated/redacted settings. `conversation_service.py` receives a store instead of importing legacy memory helpers. `server.py` owns one pool per lifespan, injects the store and returns safe storage/ID errors. `logic.py` labels its remaining helpers as deprecated test-only compatibility. `requirements.txt` adds redis 8.1.0; `.env.example`, README and ARCHITECTURE document configuration. Conversation/API tests inject memory; `test_session_store.py` covers Redis command contracts/TTL/errors; `test_redis_integration.py` is a real-server opt-in. `phase3-preserved-hashes.json` records the starting protected-file hashes.

## E–M. Storage behavior

Key: `bhashasetu:session:{session_id}` by default. IDs must be 1–128 ASCII letters/digits/underscore/hyphen; the existing default ID remains compatible. The hash stores non-null profile fields and `_attempts`/`_finalized` to retain the public response contract. The later dataset phase extends the profile fields using the same hash design. No audio, history, prompts, results or scheme data are stored.

SESSION_TTL_SECONDS defaults to 1800, allowed range 1–604800. Reads of existing keys, merges, touch and completion slide expiry. Missing/expired reads are empty and do not create immortal placeholders. Startup does not clear Redis. Python restarts preserve unexpired state; Redis server restart persistence is a deployment setting.

Merge validates first, then HSET writes only non-null fields, HINCRBY updates attempts, EXPIRE refreshes TTL and HGETALL returns a snapshot in one transaction. Different-field writes preserve each other; same-field corrections follow last Redis write wins. Explicit false is persisted as false. A small WATCH guard prevents completion metadata from overwriting a concurrently changed observed turn; it is not a distributed lock and never determines eligibility. There is no whole-turn serialization or exactly-once delivery guarantee.

Each ID addresses a separate key. A reusable synchronous client/pool belongs to each FastAPI lifespan, with 3-second connect/socket timeouts and no automatic request retry. No per-request client creation or ping occurs. Existing thread-pool execution keeps synchronous Redis calls off the async event loop. The pool is closed on shutdown; keys are not deleted.

Redis refusal/timeout/protocol errors and corrupt stored fields return sanitized 503. Invalid IDs return 422. Stored numeric and boolean text is decoded and validated through UserProfile before eligibility. An unavailable write may have committed before a lost connection; no memory fallback is attempted. Logs contain operation categories/field names, not IDs, URL credentials or profiles. Explicit SESSION_BACKEND=memory provides TTL-aware test/dev state only. Historical logic.py helpers are not a production backend.

## N–O. Evidence

- Redis-focused offline tests: **61 passed, 1 opt-in Redis test skipped**.
- Conversation/API regressions initially: **32 passed**; two additional API tests then proved failure handling and cross-session isolation.
- Intermediate full run before dataset edits: **209 passed, 2 skipped, 6 warnings, 1,073 subtests passed**. The two added API tests passed separately before the expanded dataset work.
- Final combined suite is recorded in the dataset report.
- The explicit integration test covers write/read/merge/correction/isolation, TTL/touch, client recreation, concurrent different-field updates, deletion and expiry. It uses a unique prefix and deletes only its exact keys; never FLUSHDB.
- TEST_REDIS_URL was absent in this shell. No fresh live Redis pass is claimed. The user separately reports verified real Redis in their working environment.
- redis-py 8.1.0 was checked against the installed Python 3.12 metadata, imported successfully and added locally without reinstalling ASR/Gemini. On Python 3.12 it introduces no mandatory extra dependency. Existing pip-check conflicts remain unchanged.

## P–Q. Configuration and commands

```powershell
python -m pip install redis==8.1.0
$env:SESSION_BACKEND = 'redis'
$env:REDIS_URL = 'redis://localhost:6379/0'
$env:SESSION_TTL_SECONDS = '1800'
$env:REDIS_KEY_PREFIX = 'bhashasetu:session'
# Keep DATABASE_URL, Gemini and ASR credentials/configuration from the working app.
python -m uvicorn backend.server:app --reload
python -m pytest -q
python -m pip check
# Optional real test after configuring a disposable service:
$env:TEST_REDIS_URL = 'redis://localhost:6379/15'
python -m pytest tests/test_redis_integration.py -q
```

Use `rediss://` for TLS and supply real credentials securely. The URL is excluded from settings repr and never logged. `.env.example` is not auto-loaded. For memory-only development explicitly set SESSION_BACKEND=memory. Do not use memory when expecting restart/multi-worker sharing.

## R–S. Limits and remaining checks

This is temporary unauthenticated state, not long-term profiles. Clients must use distinct IDs; knowledge of an ID is not authentication. Same-field concurrent turns can arrive out of spoken order. Multiple workers still each load their own ASR model. Real Redis lifecycle/TTL and multi-worker microphone flow need testing in the user's configured environment; no distributed locks, queue, admin or other infrastructure was added.
