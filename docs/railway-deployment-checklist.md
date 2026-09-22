# Railway deployment checklist

Configuration is prepared; a Railway build and live deployment have not been verified.
Use one app service, one replica and one Uvicorn worker. Railpack is Railway's
current native builder (the successor to Nixpacks); no Dockerfile is needed here.

- [ ] Make the current source, including `railpack.json`, available in the existing GitHub repository.
- [ ] Create a Railway project, add PostgreSQL and Redis, then add the app from that repository.
- [ ] Set the app root to the directory containing `requirements.txt`, `railpack.json` and `backend/`.
- [ ] Use Railpack. It reads `requirements.txt`, selects Python 3.12 and adds runtime FFmpeg.
- [ ] Set `DATABASE_URL` to `${{Postgres.DATABASE_URL}}` and `REDIS_URL` to `${{Redis.REDIS_URL}}` using the actual service names. Do not paste localhost URLs.
- [ ] Set `GEMINI_API_KEY`, `ADMIN_PASSWORD`, `ADMIN_SESSION_SECRET` and `HF_TOKEN` privately in Railway. The signing secret needs at least 32 random characters. Accept model access for the token's Hugging Face account.
- [ ] Set `SESSION_BACKEND=redis` and `ADMIN_COOKIE_SECURE=true`. Keep the existing ASR provider/model/language/decoder defaults and transcript logging disabled. Verify access to the configured `LLM_MODEL`.
- [ ] Configure the Railway **Pre-deploy Command** as `python -m alembic upgrade head`. Review future migrations before rollout; never put seeding in this command. Existing revisions are the unchanged schema migrations.
- [ ] Confirm startup: `python -m uvicorn backend.server:app --host 0.0.0.0 --port "$PORT" --workers 1`. The build file wraps this in `sh -c` for variable expansion. Railway provides PORT. Leave any dashboard start override empty or use the same command.
- [ ] Deploy and generate an HTTPS domain. Configure the Railway healthcheck path as `/health`.
- [ ] GET `/health` returns HTTP 200 and `{"status":"ok"}`. This is process liveness only, not provider/Redis/model readiness. Existing app startup still checks the migrated database.
- [ ] For a **new empty database only**, deliberately run `python -m backend.db.seed` inside the deployed app environment (Railway SSH). Normal re-import skips/reports conflicting records. Use --overwrite-existing only after reviewing conflicts and backing up; it can replace admin changes.
- [ ] Confirm runtime `ffmpeg -version`. The build sets `FFMPEG_BINARY=/usr/bin/ffmpeg`.
- [ ] Submit a text turn, answer an intermediate question and reach a final result. Confirm profile facts persist over turns.
- [ ] Confirm audio playback when available and readable results when `audio_url` is null. Automated bounded-TTS regression coverage already exists; do not alter production credentials to simulate failure.
- [ ] Submit one voice recording over HTTPS. Verify the ASR model initializes and a second voice request reuses it in the same process.
- [ ] Sign in at `/admin`, view a scheme and log out. Avoid editing real data solely as a smoke test.

## Model and filesystem limits

ASR downloads on the first voice request, not application startup, and caches one
model/service per process. The build sets `HF_HOME=/tmp/bhashasetu-huggingface`;
Hugging Face stores model files below that writable Linux path. This cache is
ephemeral and can be lost on redeploy/replacement, requiring another download.
No model files are bundled in Git or downloaded during the build.

The model ID identifies a 600M-parameter model: float32 weights alone are roughly
2.4 GB, before runtime tensors, TorchScript/ONNX artifacts and Python libraries.
This is a planning lower-bound calculation, not a measured download size or RAM
requirement. Allow several GB of memory and disk and measure the first voice
request on Railway; small plans can fail from memory pressure or request timeouts.
Torch/ONNX dependencies also make the build large. A successful /health response
does not prove that voice model access, memory or cold-start latency is adequate.

Generated audio stays in the existing application-local `audio/` directory;
uploads use temporary files. Treat both as ephemeral, and expect old audio URLs
to stop working after a replacement. Existing failed/late TTS cleanup is preserved;
completed generated MP3s expire under AUDIO_CLEANUP_TTL_SECONDS (default one hour).
One replica avoids cross-instance audio lookup failures. No shared storage or
new retention subsystem is added. Uvicorn and existing logging use stdout/stderr;
no local log file is required.

References: [Railway builds](https://docs.railway.com/builds/build-configuration),
[Railpack Python](https://railpack.com/languages/python/),
[Railpack configuration](https://railpack.com/config/file/).
