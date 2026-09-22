"""Runtime configuration; no implicit database or embedded credentials."""
import os
import re
from urllib.parse import urlsplit
from dataclasses import dataclass, field

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


class ConfigurationError(RuntimeError):
    pass


def positive_integer(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        if value <= 0:
            raise ValueError
        return value
    except ValueError:
        raise ConfigurationError(f"{name} must be a positive integer.") from None


@dataclass(frozen=True)
class ProtectionSettings:
    max_audio_upload_bytes: int = 8 * 1024 * 1024
    max_audio_duration_seconds: int = 60
    conversation_requests_per_minute: int = 30
    speech_requests_per_minute: int = 8
    audio_cleanup_ttl_seconds: int = 3600
    admin_login_attempts: int = 5
    admin_login_cooldown_seconds: int = 300

    @classmethod
    def from_environment(cls):
        return cls(**{name: positive_integer(name.upper(), item.default)
                      for name, item in cls.__dataclass_fields__.items()})


def ffmpeg_override():
    return os.getenv("FFMPEG_BINARY", "").strip()


def database_url() -> URL:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise ConfigurationError("Set DATABASE_URL and run Alembic migrations and the seed command.")
    try:
        url = make_url(value)
    except (ArgumentError, ValueError):
        raise ConfigurationError("DATABASE_URL must be a valid PostgreSQL URL.") from None
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ConfigurationError("DATABASE_URL must use PostgreSQL with psycopg.")
    return url.set(drivername="postgresql+psycopg")


INDICCONFORMER_MODEL = "ai4bharat/indic-conformer-600m-multilingual"


@dataclass(frozen=True)
class ASRSettings:
    provider: str = "indicconformer"
    model_id: str = INDICCONFORMER_MODEL
    language: str = "hi"
    decoder: str = "ctc"
    log_transcripts: bool = False
    revision: str = ""

    def __post_init__(self):
        if self.provider not in {"indicconformer", "whisper"}:
            raise ConfigurationError("ASR_PROVIDER must be indicconformer or whisper.")
        if not self.model_id.strip():
            raise ConfigurationError("ASR_MODEL_ID must not be empty.")
        if self.revision and not re.fullmatch(r"[0-9a-fA-F]{40}", self.revision):
            raise ConfigurationError("ASR_MODEL_REVISION must be an immutable 40-character commit hash.")
        if self.language != "hi":
            raise ConfigurationError("This Hindi ASR integration requires ASR_LANGUAGE=hi.")
        if self.decoder != "ctc":
            raise ConfigurationError("This integration supports ASR_DECODER=ctc.")

    @classmethod
    def from_environment(cls):
        provider = os.getenv("ASR_PROVIDER", "indicconformer").strip().lower()
        logging_value = os.getenv("ASR_LOG_TRANSCRIPTS", "false").strip().lower()
        if logging_value not in {"true", "false"}:
            raise ConfigurationError("ASR_LOG_TRANSCRIPTS must be true or false.")
        return cls(
            provider=provider,
            model_id=os.getenv("ASR_MODEL_ID", "base" if provider == "whisper" else INDICCONFORMER_MODEL).strip(),
            language=os.getenv("ASR_LANGUAGE", "hi").strip(),
            decoder=os.getenv("ASR_DECODER", "ctc").strip().lower(),
            log_transcripts=logging_value == "true",
            revision=os.getenv("ASR_MODEL_REVISION", "").strip(),
        )


@dataclass(frozen=True)
class LLMSettings:
    provider: str = "gemini"
    model: str = "gemini-3.5-flash-lite"
    api_key: str = field(default="", repr=False)
    timeout_ms: int = 30000

    def __post_init__(self):
        if self.provider != "gemini":
            raise ConfigurationError("LLM_PROVIDER must be gemini; no automatic fallback is configured.")
        if not self.model.strip():
            raise ConfigurationError("LLM_MODEL must not be empty.")
        if type(self.timeout_ms) is not int or not 1000 <= self.timeout_ms <= 120000:
            raise ConfigurationError("LLM_TIMEOUT_MS must be an integer between 1000 and 120000.")

    @classmethod
    def from_environment(cls):
        try:
            timeout = int(os.getenv("LLM_TIMEOUT_MS", "30000"))
        except ValueError:
            raise ConfigurationError("LLM_TIMEOUT_MS must be an integer.") from None
        return cls(
            provider=os.getenv("LLM_PROVIDER", "gemini").strip().lower(),
            model=os.getenv("LLM_MODEL", "gemini-3.5-flash-lite").strip(),
            api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            timeout_ms=timeout,
        )


@dataclass(frozen=True)
class SessionSettings:
    backend: str = "redis"
    redis_url: str = field(default="redis://localhost:6379/0", repr=False)
    ttl_seconds: int = 1800
    key_prefix: str = "bhashasetu:session"

    def __post_init__(self):
        if self.backend not in {"redis", "memory"}:
            raise ConfigurationError("SESSION_BACKEND must be redis or memory.")
        if type(self.ttl_seconds) is not int or not 1 <= self.ttl_seconds <= 604800:
            raise ConfigurationError("SESSION_TTL_SECONDS must be between 1 and 604800.")
        if not isinstance(self.key_prefix, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,100}", self.key_prefix):
            raise ConfigurationError("REDIS_KEY_PREFIX must contain 1-100 letters, digits, colon, underscore or hyphen.")
        if self.backend == "redis":
            try:
                url = urlsplit(self.redis_url)
                if url.scheme not in {"redis", "rediss"} or not url.hostname:
                    raise ValueError
                if url.port is not None and not 1 <= url.port <= 65535:
                    raise ValueError
                if url.path not in {"", "/"} and not re.fullmatch(r"/[0-9]+", url.path):
                    raise ValueError
            except (ValueError, TypeError):
                raise ConfigurationError("REDIS_URL must be a valid redis:// or rediss:// URL.") from None

    @classmethod
    def from_environment(cls):
        try:
            ttl = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
        except ValueError:
            raise ConfigurationError("SESSION_TTL_SECONDS must be an integer.") from None
        return cls(backend=os.getenv("SESSION_BACKEND", "redis").strip().lower(),
                   redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0").strip(),
                   ttl_seconds=ttl,
                   key_prefix=os.getenv("REDIS_KEY_PREFIX", "bhashasetu:session"))
