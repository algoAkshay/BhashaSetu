# TTS response-wait regression

## Evidence and limits

This checkout has no Git metadata. `git status` reports that it is not a Git
repository; no commit diff is available. The preceding change in this work session
replaced `speak_hindi(response["ai_text"])` with
`speak_hindi(result_narration(response))` inside the awaited TTS stage. It added
presentation formatting, not another extraction or eligibility call.

No database/Gemini credentials were available in the process or local `.env`.
Reproduction therefore used a real Uvicorn server and HTTP requests on loopback,
an isolated migrated SQLite catalogue, explicit memory sessions and fixture
extraction/ASR. For the stall, **actual gTTS serialization/save code** was used,
with `requests.Session.send` held by a controlled event. No production PostgreSQL
data or real Redis keys were accessed. This reproduces the response-blocking
mechanism; it does not identify the cause of Google's latency on the user's network.

## Root cause

`conversation_response()` awaited the complete `speak_hindi()` operation before
constructing JSON. `speak_hindi()` used gTTS's default `timeout=None`. The installed
gTTS sends sequential HTTP requests for text chunks. The tested final narration
was 304 characters and seven network chunks; formatting itself took about 3 ms.
A single stalled chunk could hold either text or voice responses indefinitely.
Longer final narration amplifies this pre-existing lack of a timeout. Intermediate
questions use unchanged text, but also waited on this same unbounded dependency.

Before the fix, session loading, extraction and eligibility completed in about
16 ms in the controlled run. Execution stopped at `tts_network_start`, with
`timeout: null`. The HTTP client timed out after its six-second wait (6.807 seconds
including local client setup). A concurrent subsequent `GET /` returned 200 while
TTS was still held. The event loop was not deadlocked; one optional audio worker
was preventing completion of the conversation response.

Frontend inspection shows `fetch -> response.json -> render`; audio playback is
started without awaiting its promise. The browser was waiting for backend JSON,
not for playback. The API contract did not change. Extraction ran once per request;
text never invoked the ASR dependency. Gemini already has an explicit timeout and
one SDK attempt; Redis has three-second connect/read timeouts. Neither is changed.
There is no new recursion, model initialization, result-evaluation loop or repeated
gTTS invocation in the narration formatter.

## Fix

Only `backend/server.py` changes production behavior:

- gTTS receives a two-second connect and three-second read timeout per chunk.
- The caller waits at most four seconds for optional audio, then returns existing
  text/results with `audio_url: null`.
- Two dedicated TTS workers, with nonblocking admission and no waiting queue,
  isolate audio from FastAPI's shared conversation/database worker pool.
- A timed-out synchronous worker retains its slot until completion. Additional
  requests skip audio if both slots are occupied; they still evaluate normally.
- Late audio files and partially written failed audio files are removed.
- Logs contain only fixed failure classifications and exception types.

Python cannot forcibly stop a running synchronous thread. The HTTP deadline stops
waiting; network timeouts bound normal socket waits. Worker count remains capped
even if a platform/network operation ignores its timeout. No audio endpoint, UI
contract, new provider, eligibility logic, Redis semantics or source data changes.

## Actual HTTP commands and results

Run from the project root using the existing development environment:

```powershell
python -m scripts.reproduce_tts_wait --mode stall
python -m scripts.reproduce_tts_wait --mode stall --question
python -m scripts.reproduce_tts_wait --mode success
python -m scripts.reproduce_tts_wait --mode success --question
python -m scripts.reproduce_tts_wait --mode failure
python -m scripts.reproduce_tts_wait --mode success --input voice
python -m pytest -q --tb=short
```

| Local HTTP scenario | After fix | Result |
| --- | --- | --- |
| Stalled final-result gTTS transport | 4.666 s | 200, 44 eligible results, null audio |
| Stalled intermediate-question transport | 4.754 s | 200, question preserved, null audio |
| Fast final text/audio fixture | 0.705 s | 200, results and audio URL |
| Fast intermediate question/audio fixture | 0.596 s | 200, question and audio URL |
| Immediate gTTS transport exception | 0.693 s | 200, results preserved, null audio |
| Voice with ASR/audio fixtures | 0.714 s | 200, results and audio URL |

Times include local client construction; the TTS waiting budget itself is four
seconds. Each scenario preserved its session and performed extraction once.
Text scenarios had zero ASR dependency invocations; voice had one. Fast audio
fixtures verify the URL flow, not real Google speech synthesis or pronunciation.

Full suite: **286 passed, 2 skipped, 1,111 subtests passed**, six existing warnings,
56.39 seconds. Existing tests were retained. New tests cover bounded waiting,
saturation, cancellation, cleanup, network-timeout configuration, successful
saving and HTTP text/question/voice behavior. Existing Redis, admin, dataset and
eligibility regression tests pass; live PostgreSQL/Redis tests remain skipped.

Changed files: `backend/server.py`, `tests/test_api.py`, new
`tests/test_tts_deadline.py`, `scripts/reproduce_tts_wait.py`, and this report.
Timing instrumentation is isolated in the diagnostic script, not added to normal
profile logging. Browser rendering and live Google/ASR/Redis service behavior were
not manually exercised in this environment.
