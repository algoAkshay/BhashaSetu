# ASR replacement verification report

**Integration implemented; real recognition is not yet verified.** The real IndicConformer smoke test failed because the cached Hugging Face account lacked model access. No actual IndicConformer transcript or successful CPU inference time is claimed.

## A–B. Files changed and reasons

| File | Change and purpose |
| --- | --- |
| `backend/services/asr_service.py` (new) | One provider-independent `ASRService.transcribe(path) -> str`; FFmpeg normalization; lazy model loading and reuse; official IndicConformer calls; explicit Whisper rollback; safe errors and timing logs |
| `backend/config.py` | Added ASR settings and validation without changing `database_url()` |
| `backend/server.py` | Injects the ASR service, delegates transcription, handles ASR errors, guarantees upload cleanup including read failures; removed model-specific code and unconditional transcript printing |
| `requirements.txt` | Adds only the CPU inference dependencies used by this integration |
| `requirements-dev.txt` | Adds pytest so the requested full-suite command is reproducible |
| `.env.example` | Documents provider/model/language/decoder, optional transcript logging, FFmpeg override, and token-free authentication guidance |
| `tests/test_api.py` | Replaces Whisper-specific mocks with injected ASR; preserves old assertions; adds errors/cleanup, `/docs`, successful response keys, required form field, and English transcript regression |
| `tests/test_asr.py` (new) | Tests normalization, documented inference signature, provider selection, lazy/concurrent reuse, logging, failures and temporary-file cleanup |
| `scripts/test_asr.py` (new) | Explicit real-model smoke command with actual transcript and timing output; no model work at import/pytest collection |
| `README.md`, `ARCHITECTURE.md` | Explain real ASR flow, setup, authentication, rollback and verification limits |
| `docs/asr-audit.md`, this report (new) | Record baseline, implementation rationale and measured evidence |

No source files were removed. SHA-256 comparison against the pre-edit snapshot confirmed that models, schemas, normalization/rules engine, repositories, DB session/seed code, migrations, CSV, extraction/session logic, conversation/eligibility/scheme services, frontend and existing database/rule/logic tests remained unchanged. The existing TTS function was retained. No migration, seed or production DB write was performed by this task.

## C. Dependencies

Added to runtime requirements:

- `transformers>=4.45,<5`: official AutoModel/custom-code loader.
- `torch>=2.2`: float32 tensors and the model's TorchScript preprocessing.
- `numpy>=1.26,<2.5`: PCM float32 waveform handling; upper bound also respects the installed retained Whisper/Numba dependency.
- `onnxruntime==1.20.1`: CPU ONNX execution, matching the version on the official model card.

Added `pytest>=8,<10` to development requirements. Retained `openai-whisper` for explicit rollback, plus existing imageio-ffmpeg and all Phase 1 dependencies. Hugging Face Hub is a Transformers dependency and supplies normal authentication. No torchaudio is needed for this implementation's FFmpeg decoder/resampler; no ONNX editing/export package, NeMo or GPU-only runtime was added.

Local import verification passed with Python 3.12, Torch **2.12.1+cpu**, Transformers **4.57.6**, NumPy **2.4.4**, ONNX Runtime **1.20.1**, Hugging Face Hub **0.36.2**. CUDA reports false; available ORT providers are AzureExecutionProvider and CPUExecutionProvider. Dependencies missing in the current Python were installed into the already ignored `.test-deps`, without changing global packages. Pip's initially selected NumPy 2.5.3 in that local folder was removed so the existing compatible NumPy 2.4.4 remains in use.

The official model's gated current source could not be downloaded. The current public model card and indexed official ONNX wrapper establish the implemented API and dependencies; complete real-model dependency compatibility remains pending successful authorized loading.

## D–F. Architecture, model and preprocessing

```text
MediaRecorder upload (same multipart file + session_id)
  → POST /speech-to-text
  → temporary uploaded file
  → ASRService in Starlette's thread pool
      → FFmpeg decode by content
      → mono, 16 kHz, float32 waveform in memory
      → lazy/reused official IndicConformer AutoModel
      → model(waveform[1, samples], "hi", "ctc")
      → transcript string
  → existing process_turn / EligibilityService / PostgreSQL deterministic rules
  → existing gTTS and unchanged successful JSON contract
```

Default model: **ai4bharat/indic-conformer-600m-multilingual**. [Official model usage](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) documents the chosen AutoModel interface and Hindi CTC call. The [official indexed ONNX wrapper](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual/blob/main/model_onnx.py) handles TorchScript/ONNX CPU/CUDA selection internally. The integration does not invent model-specific `.transcribe`/device-map methods or move the outer module as if it controlled ONNX placement.

The singleton service holds one initialized recognizer per process. A lock prevents duplicate first loads and concurrent inference on that shared model. It does not preload/download on application import or ordinary startup. Multiple Uvicorn workers would each hold their own model.

FFmpeg is invoked directly from FFMPEG_BINARY or imageio-ffmpeg. It uses actual container contents, including WebM/Opus with a misleading `.wav` suffix. Output is raw float32 through stdout, not another on-disk recording. A 60-second decode timeout handles malformed/stalled inputs. Uploaded temporary files are removed on success, read failure, model-load failure or inference failure, and the UploadFile is closed.

ASR returns only text. It does not guess age, transliterate keywords, normalize entities, select schemes or modify extraction. Invalid/no-speech audio returns 422. Model access/configuration/inference errors return 503 before conversation processing. Hindi is explicit; no automatic language detection or silent provider fallback is used. Whisper rollback also forces Hindi and uses CPU-safe precision.

## G. Baseline and final tests

The user reported **66 passed, 1061 subtests passed, 0 skipped** from a separate configured environment. This session did not inherit that environment's database URLs. PostgreSQL 17 is running locally.

Reproduced baseline **before source changes** (see `asr-audit.md`):

```powershell
$env:PYTHONPATH = '.test-deps'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -q --tb=short
```

**64 passed, 1 failed, 1 skipped, 1061 subtests passed** in 23.76 seconds. Failure: the existing Whisper mock expected `(path, language)` but the route had changed to `fp16=False, temperature=0`. Skip: TEST_DATABASE_URL absent in this process. An earlier unittest run also exposed raw Hindi print failures under cp1252; removing unconditional transcript prints resolves that issue.

Final full suite:

```powershell
$env:PYTHONPATH = '.test-deps'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_OFFLINE = '1'
python -m pytest -q --tb=short
```

**90 passed, 0 failed, 1 skipped, 1073 subtests passed** in **29.92 seconds**. Offline mode demonstrates that normal tests do not download the model. The single skip is the same unchanged PostgreSQL test lacking TEST_DATABASE_URL; it was not disabled or replaced. All existing eligibility/database tests that ran passed.

New checks cover official AutoModel loading arguments and `[1, samples]` float32 input; model lazy loading, warmup and concurrency; explicit Whisper CPU/Hindi rollback; no fallback; gated-access and inference errors; secret-safe logs; optional transcript logs; real FFmpeg stereo downmix/16 kHz resampling and WebM/Opus decoding; error cleanup; unchanged multipart input and successful JSON keys; and Hindi/English mocked transcript processing through the unchanged conversation layer.

Additional checks passed:

```powershell
python -m compileall -q backend tests migrations scripts
python scripts/test_asr.py --help
```

Fresh process imports of Transformers AutoModel/PreTrainedModel, Torch, NumPy, ONNX Runtime, Whisper, ASRService and the FastAPI app succeeded. There is no repository linter configuration; no lint pass is claimed. Existing warnings remain for Starlette/httpx/anyio and pytest's collection of three imported `test_database` helpers, plus a third-party dateutil deprecation warning. None is a test failure.

The updated app's lifespan and `/`, `/docs`, static frontend and speech routes pass TestClient checks using the unchanged isolated database setup. Read-only requests to the already running server at `127.0.0.1:8000` also returned:

- `/`: HTTP 200.
- `/docs`: HTTP 200.
- `/api/schemes`: **67 schemes, 217 rules**.
- `/api/eligibility` for age 32, Male, annual income 150000: HTTP 200, **40 eligible out of 67**.

Those existing-server responses confirm that the running catalogue/eligibility behavior remains available; they do not establish that this separate server process has reloaded the new ASR implementation. A fresh PostgreSQL-backed startup/full live DB test in this agent process remains unverified without its database environment. No database credentials were read from another process or modified.

## H–I. Real transcripts and CPU performance

An authenticated attempt to fetch the official model configuration returned **GatedRepoError**. The explicit real-model smoke command was then attempted on the intentionally existing `test.mp3`, with model/code cache paths inside the workspace:

```powershell
$env:PYTHONPATH = '.test-deps'
$env:PYTHONIOENCODING = 'utf-8'
$env:HF_HUB_CACHE = Join-Path (Get-Location) '.test-deps\hf-models'
$env:HF_MODULES_CACHE = Join-Path (Get-Location) '.test-deps\hf-modules'
python scripts/test_asr.py test.mp3
```

Actual outcome: **exit 1**, provider `indicconformer`, configured language `hi`, decoder `ctc`, model initialization failed due to denied access. The failed attempt took **10.581 seconds** including imports/access checking. That is **not a successful initialization or inference latency measurement**. No model weights were successfully loaded and **no transcript was produced**.

| Requested utterance | Actual IndicConformer transcript |
| --- | --- |
| मेरी उम्र पैंसठ साल है। | Not obtained: model access blocked |
| मेरी वार्षिक आय एक लाख रुपये है। | Not obtained: model access blocked |
| Meri age sixty five hai, main male hoon. | Not obtained: model access blocked |
| Meri annual income one lakh rupees hai. | Not obtained: model access blocked |
| Combined Hinglish age/gender/income sentence | Not obtained: model access blocked |

Successful model initialization time, warm utterance latency and model memory consumption are **not available**. No conclusion that the 600M model is practical or impractical on this CPU is supported yet. No smaller model was substituted.

The browser skill was used to attempt local UI/microphone verification. Browser discovery returned no connected browsers (`[]`), so **no browser microphone test was performed**. HTTP/UI and synthetic-codec tests are explicitly separate from a human microphone/accuracy test.

## J–L. Access requirements, limitations and Whisper removal

Accept the conditions on the official model page using the account that will download the model, obtain any required approval, then use `hf auth login` or securely supplied HF_TOKEN. The cached login found in this session did not have access. No token was displayed, embedded or added to repository files.

Remaining limits: gated-model compatibility and recognition accuracy still need a real run; CPU speed/memory is unmeasured; model initialization may download substantial assets; one local model serializes inference; only Hindi CTC is validated; the original regex extractor still may not understand number words such as `पैंसठ`/`sixty five`; sessions remain process-local. None of these limitations was addressed with ASR keyword hacks or Phase 2 extraction changes.

**Keep openai-whisper for now.** It is not yet safe to remove the rollback/comparison option because IndicConformer has not completed end-to-end verification. It runs only when ASR_PROVIDER=whisper is explicitly selected, never as an automatic fallback.

## M. Exact local commands

From the project root, activate the Python environment you use for the working PostgreSQL application. Retain its existing DATABASE_URL and TEST_DATABASE_URL; do not recreate or reseed the database.

```powershell
# For a fresh CPU environment only (skip if working Torch is already installed):
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-dev.txt

# First accept the official model's conditions in your browser, then authenticate:
hf auth login

$env:ASR_PROVIDER = 'indicconformer'
$env:ASR_MODEL_ID = 'ai4bharat/indic-conformer-600m-multilingual'
$env:ASR_LANGUAGE = 'hi'
$env:ASR_DECODER = 'ctc'
$env:ASR_LOG_TRANSCRIPTS = 'false'
$env:PYTHONIOENCODING = 'utf-8'

# Existing DATABASE_URL and TEST_DATABASE_URL must remain set in this terminal.
$env:HF_HUB_OFFLINE = '1'
python -m pytest -q
Remove-Item Env:HF_HUB_OFFLINE

# Real model download/inference occurs only in the following explicit commands.
python scripts/test_asr.py test.mp3
python scripts/test_asr.py path/to/hindi.wav path/to/hinglish.webm
python -m uvicorn backend.server:app --reload
```

Open `http://127.0.0.1:8000`, allow microphone access, and record the five phrases above. Compare the actual transcript to what was spoken; HTTP 200 alone is insufficient. The smoke script prints initialization and warm-recording timing and supports multiple inputs without reloading.

To roll back explicitly, set ASR_PROVIDER to `whisper` and ASR_MODEL_ID to `base`, then restart the server. No schema change or database rollback is needed.
