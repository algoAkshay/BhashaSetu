# ASR replacement audit — 2026-09-19

Audited source, services, rules/repositories/migrations, CSV, frontend, tests, configuration and documentation before implementation. Phase 1 data/business code will remain unchanged; baseline hashes were recorded for later comparison.

## Reproduced baseline

The user's separate verification was 66 passed, 1061 subtests, zero skipped. This process uses `C:\Program Files\Python312\python.exe` and did not inherit DATABASE_URL or TEST_DATABASE_URL. PostgreSQL 17 is running; its configuration was not changed. A cached Hugging Face login file exists; its contents were not printed.

Initially pytest was absent. Existing unittest suite with `.test-deps` on PYTHONPATH: 62 tests, 58 passed, 3 errors, 1 skipped. Two errors were caused by unconditionally printing Hindi to a cp1252 console. One was an existing mismatch between the Whisper mock `(path, language)` and the current route's `fp16`/`temperature` arguments.

After installing pytest locally and setting PYTHONIOENCODING=utf-8, **before any source edits**:

```powershell
$env:PYTHONPATH = '.test-deps'
$env:PYTHONIOENCODING = 'utf-8'
python -m pytest -q --tb=short
```

Result: **64 passed, 1 failed, 1 skipped, 1061 subtests passed**, 23.76 seconds. The failure is the same outdated Whisper mock. PostgreSQL integration skips because TEST_DATABASE_URL is absent in this process. Three imported `test_database` helpers are collected by pytest and emit return-value warnings; those pre-existing helpers are not eligibility test failures.

## Existing audio path

- MediaRecorder requests `audio/webm` when available; the browser selects its default format otherwise. Upload is multipart `file` plus `session_id` to `/speech-to-text`.
- WebM blobs are named `voice.webm`; other formats are named `voice.wav` regardless of actual container. Backend must decode by content.
- Server saves an upload, lazily loads Whisper `base`, and calls `transcribe(path, fp16=False, temperature=0)`. Unlike the earlier implementation it no longer supplies `language=hi`, enabling language detection.
- Server prints detected language and transcript unconditionally, then runs unchanged `process_turn`, `EligibilityService` and gTTS.
- FFmpeg setup respects FFMPEG_BINARY or copies imageio-ffmpeg into a temporary Windows shim and modifies PATH at module import. Available bundled binary is FFmpeg 7.1.
- Installed Torch is 2.12.1+cpu, CUDA unavailable. Transformers, torchaudio and ONNX Runtime were initially absent.
- No Git metadata or AGENTS.md was found in the supplied workspace.

## Official inference contract

Checked the [official model card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual) and the indexed [official model implementation](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual/blob/main/model_onnx.py). The model card specifies AutoModel loading with trust_remote_code and a `(waveform, language, decoder)` call, mono 16 kHz input, `hi` and `ctc`.

The published ONNX wrapper uses Torch, NumPy, Transformers, huggingface_hub and ONNX Runtime. It handles device selection internally with torch.cuda.is_available and CPU/CUDA execution providers; moving the outer Transformers module is not how ONNX sessions are configured. Torchaudio in the example handles decoding/resampling; this application will perform that step using its existing FFmpeg dependency. CPU ONNX Runtime suffices for this Windows CPU machine. No GPU runtime or NeMo installation is planned.

The repository is gated. Access terms and normal Hugging Face authentication must be satisfied before a real model run; mocked tests cannot establish recognition quality.

## Implementation plan

Add central ASR settings and an ASRService with lazy, reused model loading; normalize uploads to mono 16 kHz float32 in memory via FFmpeg; call the documented model API; retain explicitly selected Whisper rollback without fallback; inject ASR in endpoint tests; preserve HTTP form fields and successful JSON response. Add failure/cleanup/preprocessing/concurrency tests and an explicit real-model smoke script. Change no extraction, eligibility, data, repository, migration, frontend or TTS implementation.
