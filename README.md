# Bhasha Setu

A Hindi-first voice/text assistant for finding government schemes using explicit profile facts and deterministic rules. It is a student project, not an official determination of eligibility or an application-submission service.

## Current reliability status

The repository is **not yet ready to freeze as a passing reference implementation**. The existing engine emits `POTENTIALLY_ELIGIBLE`, but `backend/schemas.py` currently excludes it from the response status type. That mismatch breaks some evaluations; an existing API test also predates the `potential_schemes` response field. This maintenance pass preserves eligibility behavior and reports these failures rather than hiding them. See [maintenance notes](docs/repository-reliability.md).

## Request flow

Typed text goes to `POST /conversation`. Recorded audio goes to `POST /speech-to-text`, where FFmpeg normalizes it and the existing IndicConformer model transcribes Hindi. Both paths then share:

```text
Text -> Gemini profile extraction -> validated explicit updates
     -> Redis temporary profile -> PostgreSQL schemes/rules
     -> deterministic evaluation -> progressive question and result cards
     -> optional bounded Hindi gTTS -> browser
```

Gemini extracts structured facts; it does not independently decide scheme eligibility. Later explicit corrections replace earlier facts; absent facts do not erase answers. The browser asks one relevant question at a time, supports skipping and displays scheme-specific manual conditions. Text requests never initialize ASR.

## Eligibility results

Rules combine typed field/operator/value checks using AND. Supported operators are `==`, `!=`, `>`, `>=`, `<`, `<=`, `IN`, `NOT_IN`. The engine's current branches are:

| Status | Meaning |
| --- | --- |
| `ELIGIBLE` | Structured checks pass with no unresolved conditions |
| `POTENTIALLY_ELIGIBLE` | Structured checks pass, but scheme-specific manual conditions remain |
| `NEED_MORE_INFORMATION` | Required facts or rules are missing |
| `NOT_ELIGIBLE` | A known rule fails; missing facts do not hide that failure |

The status/schema mismatch noted above remains unresolved. Confidence labels such as VERIFIED describe researched source metadata, not guaranteed approval. Explanations and narration come from existing evaluation results, not independent AI eligibility reasoning.

## REST API and administration

| Route | Purpose |
| --- | --- |
| `GET /` | Plain HTML/CSS/JavaScript frontend |
| `GET /health` | Process liveness only |
| `POST /conversation` | Typed conversation with session/question context |
| `POST /speech-to-text` | Multipart audio and session/context |
| `DELETE /session/{session_id}` | Delete that temporary session |
| `GET /api/schemes`, `GET /api/schemes/{id}` | Catalogue and details |
| `POST /api/eligibility` | Deterministic evaluation of supplied attributes |
| `/admin`, `/admin/login`, `/api/admin/*` | Protected metadata, source and rule management |
| `GET /docs` | FastAPI API documentation |

Admin uses a single environment password, a signed HttpOnly/SameSite cookie, CSRF checks and failed-login throttling. Redis shares cooldowns in the normal configuration; explicit in-memory development mode keeps local counters. Successful login resets failures. This is lightweight protection, not enterprise authentication. Deactivation is soft; there is no default scheme hard-delete workflow.

## PostgreSQL, Redis and privacy

PostgreSQL stores durable Scheme/EligibilityRule data through SQLAlchemy and Alembic migrations. Redis stores temporary profile fields, attempt/finalization metadata and a default 1,800-second sliding TTL. It does not serialize whole conversation turns. No automatic memory fallback is used.

The frontend uses cryptographic session IDs and provides an explicit delete-session action. Successful deletion clears the UI and starts a new ID. Extraction text can be sent to the configured Gemini provider; deletion of the local session does not erase provider-held data or generated audio.

## Speech and audio

The default STT provider is `ai4bharat/indic-conformer-600m-multilingual`, Hindi CTC, loaded lazily once per process. This project integrates an existing model; it did not train it. The remote-code loader requires a reviewed immutable `ASR_MODEL_REVISION`. No usable revision is invented or bundled. Text remains usable without loading the voice model. Whisper is an explicit rollback option.

Uploads are read in bounded chunks, with byte/duration limits, FFmpeg timeout and temporary-file cleanup. TTS uses a separate two-worker pool, per-network timeouts and a four-second audio wait budget. Failure returns null audio without discarding text. Final narration summarizes up to three existing results. Completed generated MP3s expire after a configurable TTL; in-progress files are excluded. Storage is ephemeral unless the operator configures otherwise.

## Dataset coverage

Repository CSVs contain **79 Central schemes and 218 currently curated State schemes covering 11 states/UTs**, for 297 unique researched schemes. This is not complete India-wide state coverage.

Represented jurisdictions: Bihar, Chhattisgarh, Goa, Gujarat, Jharkhand, Madhya Pradesh, Maharashtra, Odisha, Rajasthan, Uttar Pradesh and West Bengal. The count is computed from unique `state_or_ut` values in `database/state_schemes_master.csv`; the state eligibility/review files use the same field and do not add jurisdictions.

The researched source contains 675 eligibility rows: 610 structured rules and 65 preserved for review. All 297 entries have additional manual conditions. These are repository counts, not a live production database audit.

### Safe re-import

`python -m backend.db.import_dataset --audit-only` validates source files without database writes.

A normal import creates missing schemes and leaves identical records unchanged. If existing metadata, activation or rules differ, it **skips the entire conflicting scheme** and reports `skipped_updates` and `conflicts`, including changed field names and whether rules differ. This deliberately treats source changes and stored/admin edits conservatively; it does not guess which should win. One conflicting scheme does not prevent importing other new records.

To deliberately replace conflicting records after reviewing the report and backing up the target database:

```sh
python -m backend.db.import_dataset --overwrite-existing
```

That flag can overwrite admin changes, reactivate curated schemes and reconcile/delete stored rules absent from CSV. It is never automatic. Initial legacy migration archives prototype records; later active legacy records are preserved/reported unless overwrite is explicit. Source CSV facts are not modified by the importer.

## Local setup

Supported/tested runtime: **Python 3.12** (`.python-version`, Railpack and CI agree). Python 3.10/3.11 compatibility is not claimed. Runtime and test direct dependencies are pinned to versions observed in the existing working environment. Transitive dependencies are not fully locked, and a fresh Linux build still needs CI validation.

```sh
git clone https://github.com/algoAkshay/BhashaSetu.git
cd BhashaSetu
python -m venv .venv
```

Activate `.venv` using `source .venv/bin/activate` on POSIX, or `.\.venv\Scripts\Activate.ps1` in PowerShell.

```sh
python -m pip install -r requirements-dev.txt
```

Provision a new local PostgreSQL database and Redis. Copy `.env.example` to a private `.env` and configure the process environment: the app does **not** automatically load this file. Use your IDE environment settings or shell variables. Never commit credentials.

With DATABASE_URL pointing to the intended development database:

```sh
python -m alembic upgrade head
python -m backend.db.import_dataset --audit-only
# Explicit initialization of a new development catalogue:
python -m backend.db.seed
python -m uvicorn backend.server:app --host 127.0.0.1 --port 8000
```

Open localhost:8000. Do not seed on every startup. Startup checks database tables but does not initialize ASR or prove Redis/provider readiness. For voice, obtain Hugging Face model access, supply authentication and set a reviewed revision.

## Configuration

See `.env.example` for defaults and placeholders; no real credentials belong there.

| Variables | Purpose |
| --- | --- |
| `DATABASE_URL` | Required PostgreSQL connection |
| `GEMINI_API_KEY`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_TIMEOUT_MS` | Profile extraction; confirm access to configured model |
| `SESSION_BACKEND`, `REDIS_URL`, `SESSION_TTL_SECONDS`, `REDIS_KEY_PREFIX` | Redis sessions; explicit memory mode for local development |
| `ASR_PROVIDER`, `ASR_MODEL_ID`, `ASR_MODEL_REVISION`, `ASR_LANGUAGE`, `ASR_DECODER` | Speech provider/model and immutable revision |
| `HF_TOKEN`, `HF_HOME`, `FFMPEG_BINARY` | Library model authentication/cache and optional FFmpeg override |
| `ASR_LOG_TRANSCRIPTS` | Off by default; keep off for privacy |
| `MAX_AUDIO_UPLOAD_BYTES`, `MAX_AUDIO_DURATION_SECONDS` | Upload/decode limits |
| `CONVERSATION_REQUESTS_PER_MINUTE`, `SPEECH_REQUESTS_PER_MINUTE` | Process-local request limits |
| `AUDIO_CLEANUP_TTL_SECONDS` | Completed MP3 retention, default 3600 seconds |
| `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET`, `ADMIN_COOKIE_SECURE` | Enable admin; independent random signing secret, Secure cookies on HTTPS |
| `ADMIN_LOGIN_ATTEMPTS`, `ADMIN_LOGIN_COOLDOWN_SECONDS` | Failed-login threshold/cooldown |
| `TEST_DATABASE_URL`, `TEST_REDIS_URL` | Optional disposable integration services only |

## Tests and CI

```sh
python -m pytest tests/test_import_safety.py tests/test_integrity_portability.py -q
python -m pytest -q
```

Normal tests use isolated migrated SQLite databases and mocked Gemini/ASR/TTS. Optional PostgreSQL/Redis tests use dedicated temporary schemas/keys. Never point test URLs at production. Node runs the small frontend privacy test; symlink coverage may skip on Windows without symlink permission.

GitHub Actions installs Python 3.12 and pinned dependencies, starts disposable PostgreSQL/Redis services and runs pytest. Test fixtures apply their own migrations; no global reseed is required. HF/Transformers are offline; no Gemini key, model download or paid API is required. The workflow follows [GitHub's Python testing guidance](https://docs.github.com/en/actions/tutorials/build-and-test-code/python). CI is expected to expose the unresolved failures above, not hide them.

Integrity snapshots normalize CRLF to LF and use sorted POSIX paths. Other bytes remain significant. Legacy stage hash files are historical records, not silently regenerated baselines.

## Legacy paths and limitations

`backend/logic.py` remains a test/compatibility layer, not the production conversation path. `backend/agent/*` and `backend/tts.py` are unused legacy paths and retained as historical code; active TTS is in `backend/server.py`. See maintenance notes for classification. No Java/Spring source was found; no conversion was started.

Catalogue correctness/freshness, manual conditions, extraction/transcription errors and external-service availability limit results. There are no measured accuracy/scaling claims. Model cold start and memory must be tested on the deployment host. The current failing eligibility contracts prevent treating this backend as a frozen passing reference.

## Railway Deployment

Deployment configuration is prepared, but no live Railway deployment is claimed.
Use **Railpack**, Railway's current native builder and successor to Nixpacks.
`railpack.json` selects Python 3.12, installs runtime FFmpeg, sets a writable
Linux model cache and starts the existing app with one worker. Docker is unnecessary.

1. Create a Railway project and add PostgreSQL and Redis services.
2. Add one app service from the existing Bhasha Setu GitHub repository, with the current source and `railpack.json` committed. Set its root to the directory containing `requirements.txt` and `backend/`.
3. Set `DATABASE_URL` and `REDIS_URL` using Railway references to those services. Set `GEMINI_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET` and model-access `HF_TOKEN` privately. Enable `ADMIN_COOKIE_SECURE=true`, keep `SESSION_BACKEND=redis`, and retain the existing ASR defaults. Do not upload `.env`.
4. Set the Railway pre-deploy command to `python -m alembic upgrade head`. Never auto-seed on startup. Initialize a **new empty** catalogue separately with `python -m backend.db.seed` in the deployed environment; do not re-import an existing edited database.
5. Deploy using Railpack and one replica. The configured start command runs `python -m uvicorn backend.server:app --host 0.0.0.0 --port "$PORT" --workers 1`; Railway supplies PORT. Logs go to stdout/stderr.
6. Generate an HTTPS domain and set the Railway healthcheck path to `/health`. Verify HTTP 200 with `{"status":"ok"}`; this checks process liveness only.
7. Test text input, a follow-up question, a final result and audio/fallback. Then test one Hindi voice recording and a second recording to check model reuse. Finally test admin login/logout.

ASR loads on the first voice request, not startup. `HF_HOME` is set to
`/tmp/bhashasetu-huggingface`; the ephemeral model cache can require re-download
on redeployment. The 600M model and ML dependencies need substantial memory/disk;
measure cold-start behavior on Railway before relying on voice. Generated audio
is also ephemeral; old URLs can disappear, and completed generated audio expires under AUDIO_CLEANUP_TTL_SECONDS. Failed/late TTS cleanup remains in place.

See [the practical deployment checklist](docs/railway-deployment-checklist.md)
for exact service references, FFmpeg verification and resource limitations.
Builder reference: [Railway build configuration](https://docs.railway.com/builds/build-configuration).
