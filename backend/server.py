from contextlib import asynccontextmanager, suppress
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict, deque
from threading import BoundedSemaphore, Lock
import asyncio
import logging
import os
import tempfile
import time
import uuid
from typing import Annotated

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from gtts import gTTS
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.api.admin import pages as admin_pages, router as admin_router
from backend.api.routes import database_session, router
from backend.config import ConfigurationError, ProtectionSettings
from backend.services.audio_cleanup import cleanup_audio
from backend.services.login_throttle import LoginThrottle
from backend.conversation_schemas import ConversationInput, ConversationOptions
from backend.db.session import create_session_factory
from backend.normalization import InputValidationError, RuleValidationError
from backend.services.asr_service import ASRError, ASRService, get_asr_service
from backend.services.conversation_service import process_turn
from backend.services.eligibility_service import EligibilityService
from backend.services.profile_extraction_service import (
    ProfileExtractor,
    get_profile_extractor,
)
from backend.services.scheme_service import SchemeService
from backend.services.session_store import (
    InvalidSessionID,
    SessionStore,
    SessionStoreError,
    create_session_store,
    get_session_store,
    validate_session_id,
)
from backend.services.user_response import result_narration


logger = logging.getLogger(__name__)


# -------------------------------------------------
# REQUEST LIMITS
# -------------------------------------------------

PROTECTION = ProtectionSettings.from_environment()
MAX_AUDIO_UPLOAD_BYTES = PROTECTION.max_audio_upload_bytes
UPLOAD_CHUNK_SIZE = 1024 * 1024

CONVERSATION_REQUESTS_PER_MINUTE = PROTECTION.conversation_requests_per_minute
SPEECH_REQUESTS_PER_MINUTE = PROTECTION.speech_requests_per_minute
RATE_LIMIT_WINDOW_SECONDS = 60

_rate_limit_lock = Lock()
_rate_limit_events = defaultdict(deque)


def _client_identifier(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host

    return "unknown"


def enforce_rate_limit(
    request: Request,
    bucket: str,
    limit: int,
) -> None:
    """
    Lightweight per-process abuse protection.

    This prevents one client from hammering Gemini/ASR repeatedly.
    For multi-worker production deployments, move this counter to Redis.
    """
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS

    key = (
        bucket,
        _client_identifier(request),
    )

    with _rate_limit_lock:
        events = _rate_limit_events[key]

        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= limit:
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many requests. "
                    "Please wait briefly and try again."
                ),
            )

        events.append(now)


# -------------------------------------------------
# APPLICATION LIFESPAN
# -------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    owned_factory = not hasattr(
        application.state,
        "session_factory",
    )

    owned_store = not hasattr(
        application.state,
        "conversation_store",
    )

    try:
        if owned_factory:
            application.state.session_factory = (
                create_session_factory()
            )

        with application.state.session_factory() as session:
            scheme_count, rule_count = (
                SchemeService(
                    session
                ).catalogue_counts()
            )

        logger.info(
            "database_startup_ready active_schemes=%s rules=%s",
            scheme_count,
            rule_count,
        )

        if not scheme_count:
            logger.warning(
                "database_has_no_active_schemes run_seed_command"
            )

    except (
        SQLAlchemyError,
        ConfigurationError,
    ):
        if (
            owned_factory
            and hasattr(
                application.state,
                "session_factory",
            )
        ):
            application.state.session_factory.kw[
                "bind"
            ].dispose()

            del application.state.session_factory

        logger.error(
            "database_startup_failed "
            "check_configuration_and_migrations"
        )

        raise RuntimeError(
            "Database unavailable. "
            "Check DATABASE_URL and run migrations."
        ) from None

    cleanup_task = None
    try:
        if owned_store:
            application.state.conversation_store = (
                create_session_store()
            )

        application.state.login_throttle = LoginThrottle(application.state.conversation_store)
        cleanup_task = asyncio.create_task(periodic_audio_cleanup())
        yield

    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task

        if (
            owned_store
            and hasattr(
                application.state,
                "conversation_store",
            )
        ):
            await run_in_threadpool(
                application.state.conversation_store.close
            )

            del application.state.conversation_store

        if owned_factory:
            application.state.session_factory.kw[
                "bind"
            ].dispose()

            del application.state.session_factory


# -------------------------------------------------
# APP INIT
# -------------------------------------------------

app = FastAPI(
    title="Bhasha Setu",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(admin_pages)
app.include_router(admin_router)


# -------------------------------------------------
# ERROR HANDLERS
# -------------------------------------------------

@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request, error):
    logger.error(
        "database_request_failed error_type=%s",
        type(error).__name__,
    )

    return JSONResponse(
        status_code=503,
        content={
            "detail":
                "Database unavailable. "
                "Please try again later."
        },
    )


@app.exception_handler(RuleValidationError)
async def rule_error_handler(request, error):
    logger.error(
        "eligibility_evaluation_failed invalid_rule"
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail":
                "Eligibility rules are invalid; "
                "administrator review required."
        },
    )


@app.exception_handler(InputValidationError)
async def input_error_handler(request, error):
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(error)
        },
    )


@app.exception_handler(ASRError)
async def asr_error_handler(request, error):
    return JSONResponse(
        status_code=error.status_code,
        content={
            "detail": str(error)
        },
    )


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(request, error):
    logger.error(
        "request_configuration_failed error_type=%s",
        type(error).__name__,
    )

    return JSONResponse(
        status_code=503,
        content={
            "detail": str(error)
        },
    )


@app.exception_handler(SessionStoreError)
async def session_store_error_handler(request, error):
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(error)
        },
    )


@app.exception_handler(InvalidSessionID)
async def session_id_error_handler(request, error):
    return JSONResponse(
        status_code=422,
        content={
            "detail": str(error)
        },
    )


# -------------------------------------------------
# DIRECTORIES
# -------------------------------------------------

BASE_DIR = os.path.dirname(
    os.path.dirname(__file__)
)

FRONTEND_DIR = os.path.join(
    BASE_DIR,
    "frontend",
)

AUDIO_DIR = os.path.join(
    BASE_DIR,
    "audio",
)

os.makedirs(
    AUDIO_DIR,
    exist_ok=True,
)


# -------------------------------------------------
# STATIC FILES
# -------------------------------------------------

app.mount(
    "/static",
    StaticFiles(
        directory=FRONTEND_DIR
    ),
    name="static",
)

app.mount(
    "/audio",
    StaticFiles(
        directory=AUDIO_DIR
    ),
    name="audio",
)


@app.get("/health")
async def health():
    """Process liveness only; no provider, model or database work."""
    return {
        "status": "ok"
    }


@app.get("/")
def serve_ui():
    return FileResponse(
        os.path.join(
            FRONTEND_DIR,
            "index.html",
        )
    )


# -------------------------------------------------
# SESSION DELETION
# -------------------------------------------------

@app.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(
        get_session_store
    ),
):
    """
    Allow the user to explicitly remove their stored
    conversation/profile data before Redis TTL expiry.
    """
    validate_session_id(
        session_id
    )

    await run_in_threadpool(
        store.delete,
        session_id,
    )

    return {
        "deleted": True
    }


# -------------------------------------------------
# TEXT → SPEECH
# -------------------------------------------------

TTS_RESPONSE_TIMEOUT_SECONDS = 4.0

TTS_NETWORK_TIMEOUT = (
    2.0,
    3.0,
)

TTS_MAX_WORKERS = 2

_tts_executor = ThreadPoolExecutor(
    max_workers=TTS_MAX_WORKERS,
    thread_name_prefix="bhasha-tts",
)

_tts_slots = BoundedSemaphore(
    TTS_MAX_WORKERS
)


async def periodic_audio_cleanup():
    ttl = PROTECTION.audio_cleanup_ttl_seconds
    while True:
        await run_in_threadpool(cleanup_audio, AUDIO_DIR, ttl)
        await asyncio.sleep(min(60, ttl))


def speak_hindi(text: str) -> str:
    filename = (
        f"{uuid.uuid4()}.mp3"
    )

    path = os.path.join(
        AUDIO_DIR,
        filename,
    )

    partial_path = path + ".part"
    try:
        gTTS(
            text=text,
            lang="hi",
            timeout=TTS_NETWORK_TIMEOUT,
        ).save(partial_path)
        os.replace(partial_path, path)

    except Exception:
        if os.path.exists(partial_path):
            os.remove(partial_path)

        raise

    return (
        f"/audio/{filename}"
    )


def _discard_late_audio(future):
    try:
        url = future.result()

        if (
            isinstance(url, str)
            and url.startswith(
                "/audio/"
            )
        ):
            filename = url[
                len("/audio/"):
            ]

            if (
                filename
                and os.path.basename(
                    filename
                )
                == filename
            ):
                path = os.path.join(
                    AUDIO_DIR,
                    filename,
                )

                if os.path.exists(
                    path
                ):
                    os.remove(
                        path
                    )

    except Exception as error:
        logger.warning(
            "tts_late_completion error_type=%s",
            type(error).__name__,
        )


async def bounded_speech(
    text: str,
) -> str | None:
    if not _tts_slots.acquire(
        blocking=False
    ):
        logger.warning(
            "tts_unavailable reason=busy"
        )

        return None

    try:
        job = _tts_executor.submit(
            speak_hindi,
            text,
        )

    except Exception:
        _tts_slots.release()
        raise

    job.add_done_callback(
        lambda _:
            _tts_slots.release()
    )

    future = asyncio.wrap_future(
        job
    )

    future.add_done_callback(
        lambda done:
            None
            if done.cancelled()
            else done.exception()
    )

    try:
        return await asyncio.wait_for(
            asyncio.shield(
                future
            ),
            timeout=(
                TTS_RESPONSE_TIMEOUT_SECONDS
            ),
        )

    except asyncio.TimeoutError:
        job.add_done_callback(
            _discard_late_audio
        )

        logger.warning(
            "tts_unavailable reason=deadline"
        )

        return None

    except asyncio.CancelledError:
        job.add_done_callback(
            _discard_late_audio
        )

        raise


# -------------------------------------------------
# SPEECH ENDPOINT
# -------------------------------------------------

@app.post("/speech-to-text")
async def speech_to_text(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form("default"),
    session: Session = Depends(
        database_session
    ),
    asr: ASRService = Depends(
        get_asr_service
    ),
    extractor: ProfileExtractor = Depends(
        get_profile_extractor
    ),
    store: SessionStore = Depends(
        get_session_store
    ),
    *,
    context: Annotated[
        str,
        Form(
            max_length=2000
        ),
    ] = "{}",
):
    enforce_rate_limit(
        request,
        "speech",
        SPEECH_REQUESTS_PER_MINUTE,
    )

    suffix = os.path.splitext(
        file.filename or ""
    )[1] or ".webm"

    tmp_path = None
    total_bytes = 0

    try:
        validate_session_id(
            session_id
        )

        try:
            options = (
                ConversationOptions
                .model_validate_json(
                    context
                )
            )

        except ValidationError:
            raise InputValidationError(
                "Invalid conversation context."
            ) from None

        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as tmp:
            tmp_path = tmp.name

            while True:
                chunk = await file.read(
                    UPLOAD_CHUNK_SIZE
                )

                if not chunk:
                    break

                total_bytes += len(
                    chunk
                )

                if (
                    total_bytes
                    > MAX_AUDIO_UPLOAD_BYTES
                ):
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Audio upload is too large. "
                            "Please send a shorter recording."
                        ),
                    )

                tmp.write(
                    chunk
                )

        if total_bytes == 0:
            raise InputValidationError(
                "The uploaded audio file is empty."
            )

        user_text = await run_in_threadpool(
            asr.transcribe,
            tmp_path,
        )

    finally:
        if (
            tmp_path is not None
            and os.path.exists(
                tmp_path
            )
        ):
            os.remove(
                tmp_path
            )

        await file.close()

    return await conversation_response(
        user_text,
        session_id,
        session,
        extractor,
        store,
        **options.model_dump(),
    )


# -------------------------------------------------
# TEXT CONVERSATION ENDPOINT
# -------------------------------------------------

@app.post("/conversation")
async def text_conversation(
    request: Request,
    payload: ConversationInput,
    session: Session = Depends(
        database_session
    ),
    extractor: ProfileExtractor = Depends(
        get_profile_extractor
    ),
    store: SessionStore = Depends(
        get_session_store
    ),
):
    enforce_rate_limit(
        request,
        "conversation",
        CONVERSATION_REQUESTS_PER_MINUTE,
    )

    return await conversation_response(
        payload.text,
        payload.session_id,
        session,
        extractor,
        store,
        answer_field=(
            payload.answer_field
        ),
        skipped_fields=(
            payload.skipped_fields
        ),
        skip=payload.skip,
    )


# -------------------------------------------------
# COMMON CONVERSATION RESPONSE
# -------------------------------------------------

async def conversation_response(
    user_text,
    session_id,
    session,
    extractor,
    store,
    **options,
):
    response = await run_in_threadpool(
        process_turn,
        user_text,
        session_id,
        EligibilityService(
            session
        ),
        extractor,
        store,
        **options,
    )

    try:
        audio_url = (
            await bounded_speech(
                result_narration(
                    response
                )
            )
        )

    except Exception as error:
        logger.warning(
            "tts_unavailable error_type=%s",
            type(error).__name__,
        )

        audio_url = None

    return JSONResponse(
        {
            **response,
            "audio_url":
                audio_url,
        }
    )