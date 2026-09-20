# Phase 1 audit (before implementation)

Baseline command: `python -m unittest discover -s tests -v` — **6 passed, 0 failed**.
`python -m pytest -q` was unavailable (pytest is not installed); the existing suite uses unittest.

- Entry point: `backend.server:app`. Routes: `GET /`, `POST /speech-to-text`, static frontend and audio mounts.
- Runtime: uploaded audio → temporary file → lazily loaded Whisper base model (Hindi) → regex field extraction → process-local session dictionary → CSV eligibility filtering → gTTS → JSON and audio URL.
- CSV: `database/schemes.csv`, **67 rows**, 67 scheme names. Columns exactly `scheme_name,min_age,max_age,gender,max_income`. Sixteen gender restrictions; other rows use `Any`.
- Eligibility: inclusive minimum/maximum age, inclusive maximum income, exact gender unless `Any`. No scheme-name branches. All three personal fields were required even for unrestricted gender schemes.
- Sessions: dictionary keyed by browser session ID, age/gender/income initially null, attempt counter and finalized flag. First turn always asks for details. Second turn fabricated age 30, Male, income 100000 when absent, then finalized; next request resets finalized sessions.
- Six tests cover extraction, lookup, independent sessions, and fabricated defaults. The latter test must intentionally change.
- `backend/agent/{planner,executor,evaluator}.py` are not imported by the server. `backend/tts.py` is also unused; the server has its own TTS helper.
- Dependencies: FastAPI, Uvicorn, python-multipart, openai-whisper, gTTS, imageio-ffmpeg. No DB/configuration layer or migrations. No lint configuration.
- Frontend consumes scheme name strings, response text and audio URLs; it does not render rule explanations.
- README/ARCHITECTURE incorrectly claim an active planner/executor/evaluator architecture, autonomous reasoning, production readiness, and 100+ schemes. Executable behavior is deterministic extraction and filtering.
- Inspected all source, tests, dependency/configuration files and documentation. Images and `test.mp3` are reference/demo assets, not executable dependencies. Nested `BhashaSetu` directory is empty.
- This supplied directory has no `.git`; git status/diff are unavailable.
- Environment: Python 3.12, SQLAlchemy 2.0.43, Pydantic 2.13.4, FastAPI 0.139.0. Alembic, psycopg and httpx initially absent. No DATABASE_URL/TEST_DATABASE_URL or local PostgreSQL installation/service detected.

Implementation plan: retain extraction, browser and STT/TTS; add SQLAlchemy models, Alembic, explicit repositories and service, a pure typed rules engine, atomic CSV seeding, thin read/evaluation APIs, and a compatibility adapter for scheme-name results. Remove unused agent modules. Replace fabricated defaults with exposed missing requirements. Verify complete-user equivalence against the frozen CSV semantics, test isolated DB/API flows, and provide an opt-in PostgreSQL migration test.
