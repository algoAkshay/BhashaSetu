from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
import asyncio
from typing import Annotated
import logging

from fastapi import Depends, FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import tempfile
import os
import uuid
from gtts import gTTS
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from backend.api.routes import database_session, router
from backend.api.admin import pages as admin_pages, router as admin_router
from backend.config import ConfigurationError
from backend.conversation_schemas import ConversationInput, ConversationOptions
from backend.db.session import create_session_factory
from backend.normalization import InputValidationError, RuleValidationError
from backend.services.conversation_service import process_turn
from backend.services.user_response import result_narration
from backend.services.eligibility_service import EligibilityService
from backend.services.scheme_service import SchemeService
from backend.services.asr_service import ASRError, ASRService, get_asr_service
from backend.services.profile_extraction_service import ProfileExtractor, get_profile_extractor
from backend.services.session_store import (
    InvalidSessionID, SessionStore, SessionStoreError, create_session_store,
    get_session_store, validate_session_id,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    owned_factory = not hasattr(application.state, "session_factory")
    owned_store = not hasattr(application.state, "conversation_store")
    try:
        if owned_factory:
            application.state.session_factory = create_session_factory()
        with application.state.session_factory() as session:
            # Check both schema tables at startup without reading the CSV or seeding.
            scheme_count, rule_count = SchemeService(session).catalogue_counts()
        logger.info("database_startup_ready active_schemes=%s rules=%s", scheme_count, rule_count)
        if not scheme_count:
            logger.warning("database_has_no_active_schemes run_seed_command")
    except (SQLAlchemyError, ConfigurationError):
        if owned_factory and hasattr(application.state, "session_factory"):
            application.state.session_factory.kw["bind"].dispose()
            del application.state.session_factory
        logger.error("database_startup_failed check_configuration_and_migrations")
        raise RuntimeError("Database unavailable. Check DATABASE_URL and run migrations.") from None
    try:
        if owned_store:
            # Creates a reusable pool without pinging or opening a socket. Sessions
            # are never cleared on startup; Redis owns their lifetime.
            application.state.conversation_store = create_session_store()
        yield
    finally:
        if owned_store and hasattr(application.state, "conversation_store"):
            await run_in_threadpool(application.state.conversation_store.close)
            del application.state.conversation_store
        if owned_factory:
            application.state.session_factory.kw["bind"].dispose()
            del application.state.session_factory

# -------------------------------------------------
# APP INIT
# -------------------------------------------------
app = FastAPI(title="Bhasha Setu", lifespan=lifespan)
app.include_router(router)
app.include_router(admin_pages)
app.include_router(admin_router)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request, error):
    logger.error("database_request_failed error_type=%s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": "Database unavailable. Please try again later."})


@app.exception_handler(RuleValidationError)
async def rule_error_handler(request, error):
    logger.error("eligibility_evaluation_failed invalid_rule")
    return JSONResponse(status_code=500, content={"detail": "Eligibility rules are invalid; administrator review required."})


@app.exception_handler(InputValidationError)
async def input_error_handler(request, error):
    return JSONResponse(status_code=422, content={"detail": str(error)})


@app.exception_handler(ASRError)
async def asr_error_handler(request, error):
    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(request, error):
    logger.error("request_configuration_failed error_type=%s", type(error).__name__)
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.exception_handler(SessionStoreError)
async def session_store_error_handler(request, error):
    return JSONResponse(status_code=503, content={"detail": str(error)})


@app.exception_handler(InvalidSessionID)
async def session_id_error_handler(request, error):
    return JSONResponse(status_code=422, content={"detail": str(error)})

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")

os.makedirs(AUDIO_DIR, exist_ok=True)


# -------------------------------------------------
# STATIC FILES
# -------------------------------------------------
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


@app.get("/")
def serve_ui():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# -------------------------------------------------
# TEXT → SPEECH
# -------------------------------------------------
TTS_RESPONSE_TIMEOUT_SECONDS = 4.0
TTS_NETWORK_TIMEOUT = (2.0, 3.0)  # Per gTTS chunk: connection and read timeout.
TTS_MAX_WORKERS = 2
_tts_executor = ThreadPoolExecutor(max_workers=TTS_MAX_WORKERS, thread_name_prefix="bhasha-tts")
_tts_slots = BoundedSemaphore(TTS_MAX_WORKERS)


def speak_hindi(text: str) -> str:
    filename = f"{uuid.uuid4()}.mp3"
    path = os.path.join(AUDIO_DIR, filename)
    try:
        gTTS(text=text, lang="hi", timeout=TTS_NETWORK_TIMEOUT).save(path)
    except Exception:
        # gTTS may have opened/written the file before a later chunk failed.
        if os.path.exists(path):
            os.remove(path)
        raise
    return f"/audio/{filename}"


def _discard_late_audio(future):
    try:
        url = future.result()
        # Only remove a generated file directly inside our audio directory.
        if isinstance(url, str) and url.startswith("/audio/"):
            filename = url[len("/audio/"):]
            if filename and os.path.basename(filename) == filename:
                path = os.path.join(AUDIO_DIR, filename)
                if os.path.exists(path):
                    os.remove(path)
    except Exception as error:
        logger.warning("tts_late_completion error_type=%s", type(error).__name__)


async def bounded_speech(text: str) -> str | None:
    # No waiting queue: timed-out synchronous calls retain their slot until done.
    # They cannot occupy FastAPI's shared conversation/database worker pool.
    if not _tts_slots.acquire(blocking=False):
        logger.warning("tts_unavailable reason=busy")
        return None
    try:
        job = _tts_executor.submit(speak_hindi, text)
    except Exception:
        _tts_slots.release()
        raise
    job.add_done_callback(lambda _: _tts_slots.release())
    future = asyncio.wrap_future(job)
    # Retrieve late exceptions even after the HTTP request stopped awaiting audio.
    future.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.wait_for(asyncio.shield(future), timeout=TTS_RESPONSE_TIMEOUT_SECONDS)
    except TimeoutError:
        job.add_done_callback(_discard_late_audio)
        logger.warning("tts_unavailable reason=deadline")
        return None
    except asyncio.CancelledError:
        job.add_done_callback(_discard_late_audio)
        raise


# -------------------------------------------------
# MAIN ENDPOINT
# -------------------------------------------------
@app.post("/speech-to-text")
async def speech_to_text(
    file: UploadFile = File(...),
    session_id: str = Form("default"),
    session: Session = Depends(database_session),
    asr: ASRService = Depends(get_asr_service),
    extractor: ProfileExtractor = Depends(get_profile_extractor),
    store: SessionStore = Depends(get_session_store),
    *,
    context: Annotated[str, Form(max_length=2000)] = "{}",
):
    # ---------- Save audio ----------
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"

    tmp_path = None
    try:
        validate_session_id(session_id)
        try:
            options = ConversationOptions.model_validate_json(context)
        except ValidationError:
            raise InputValidationError("Invalid conversation context.") from None
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = tmp.name
            tmp.write(await file.read())
        # A local CPU model must not block the async event loop during inference.
        user_text = await run_in_threadpool(asr.transcribe, tmp_path)
    finally:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.remove(tmp_path)
        await file.close()

    return await conversation_response(user_text, session_id, session, extractor, store,
                                       **options.model_dump())


@app.post("/conversation")
async def text_conversation(
    payload: ConversationInput,
    session: Session = Depends(database_session),
    extractor: ProfileExtractor = Depends(get_profile_extractor),
    store: SessionStore = Depends(get_session_store),
):
    return await conversation_response(payload.text, payload.session_id, session, extractor, store,
                                       answer_field=payload.answer_field,
                                       skipped_fields=payload.skipped_fields, skip=payload.skip)


async def conversation_response(user_text, session_id, session, extractor, store, **options):
    # Voice and typing use the same extractor, profile store and decision engine.
    response = await run_in_threadpool(
        process_turn,
        user_text,
        session_id,
        EligibilityService(session),
        extractor,
        store,
        **options,
    )

    # ---------- Text to speech ----------
    try:
        audio_url = await bounded_speech(result_narration(response))
    except Exception as error:
        logger.warning(
            "tts_unavailable error_type=%s",
            type(error).__name__,
        )
        audio_url = None

    return JSONResponse({
        **response,
        "audio_url": audio_url,
    })
