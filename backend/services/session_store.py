"""Temporary conversation profiles. Redis is the default; no implicit fallback."""
from dataclasses import dataclass, field, replace
import logging
import re
from threading import Lock
from time import monotonic
from typing import Protocol

from fastapi import Request
from pydantic import ValidationError
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError, WatchError
from redis.retry import Retry

from backend.config import ConfigurationError, SessionSettings
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.normalization import FIELD_TYPES

logger = logging.getLogger(__name__)


class InvalidSessionID(ValueError):
    def __init__(self):
        super().__init__("session_id must contain 1-128 letters, digits, underscores or hyphens.")


class SessionStoreError(RuntimeError):
    def __init__(self):
        super().__init__("Conversation storage is unavailable. Please try again later.")


def validate_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        raise InvalidSessionID()
    return session_id


@dataclass(frozen=True)
class SessionState:
    profile: UserProfile = field(default_factory=UserProfile)
    attempts: int = 0
    finalized: bool = False

    def response_dict(self) -> dict:
        additional = self.profile.model_dump(exclude_none=True)
        additional.pop("annual_income", None)
        return {**additional, "age": self.profile.age, "gender": self.profile.gender,
                "income": self.profile.annual_income, "attempts": self.attempts,
                "finalized": self.finalized}


class SessionStore(Protocol):
    def get(self, session_id: str) -> SessionState: ...
    def merge(self, session_id: str, update: ProfileExtractionResult) -> SessionState: ...
    def touch(self, session_id: str) -> None: ...
    def delete(self, session_id: str) -> None: ...
    def mark_finalized(self, session_id: str, attempts: int) -> None: ...
    def close(self) -> None: ...


def _decode(values: dict[str, str]) -> SessionState:
    """Fail the whole read on corruption; never feed partial corrupt data to rules."""
    try:
        if set(values) - (set(UserProfile.model_fields) | {"_attempts", "_finalized"}):
            raise ValueError
        attributes = {key: value for key, value in values.items() if not key.startswith("_")}
        for key in attributes:
            if key in {"age", "annual_income", "family_annual_income", "individual_monthly_income"}:
                if not re.fullmatch(r"[0-9]+", attributes[key]):
                    raise ValueError
                attributes[key] = int(attributes[key])
            elif key == "disability_percentage":
                attributes[key] = float(attributes[key])
            elif FIELD_TYPES.get(key) == "boolean":
                if attributes[key] not in {"true", "false"}:
                    raise ValueError
                attributes[key] = attributes[key] == "true"
        attempts = values.get("_attempts", "0")
        finalized = values.get("_finalized", "0")
        if not re.fullmatch(r"[0-9]+", attempts) or finalized not in {"0", "1"}:
            raise ValueError
        return SessionState(UserProfile.model_validate(attributes), int(attempts), finalized == "1")
    except (ValidationError, ValueError, TypeError):
        logger.warning("session_store_failure reason=invalid_stored_data")
        raise SessionStoreError() from None


class RedisSessionStore:
    def __init__(self, client: Redis, settings: SessionSettings):
        self.client = client
        self.settings = settings

    def _key(self, session_id: str) -> str:
        return f"{self.settings.key_prefix}:{validate_session_id(session_id)}"

    def get(self, session_id: str) -> SessionState:
        key = self._key(session_id)
        try:
            # Snapshot and sliding expiry share a transaction; missing keys stay absent.
            with self.client.pipeline(transaction=True) as pipe:
                pipe.hgetall(key)
                pipe.expire(key, self.settings.ttl_seconds)
                values = pipe.execute()[0]
        except (RedisError, UnicodeError):
            logger.warning("session_store_failure operation=read")
            raise SessionStoreError() from None
        state = _decode(values)
        logger.info("session_store_read" if values else "session_expired_or_missing")
        return state

    def merge(self, session_id: str, update: ProfileExtractionResult) -> SessionState:
        key = self._key(session_id)
        update = ProfileExtractionResult.model_validate(update)
        fields = update.model_dump(exclude_none=True)
        fields = {key: str(value).lower() if type(value) is bool else value for key, value in fields.items()}
        try:
            with self.client.pipeline(transaction=True) as pipe:
                # Only explicit fields: never overwrite a concurrent update to another slot.
                pipe.hset(key, mapping={**fields, "_finalized": "0"})
                pipe.hincrby(key, "_attempts", 1)
                pipe.expire(key, self.settings.ttl_seconds)
                pipe.hgetall(key)
                state = _decode(pipe.execute()[-1])
        except (RedisError, UnicodeError):
            logger.warning("session_store_failure operation=merge")
            raise SessionStoreError() from None
        logger.info("session_store_updated fields=%s", list(fields))
        return state

    def touch(self, session_id: str) -> None:
        key = self._key(session_id)
        try:
            self.client.expire(key, self.settings.ttl_seconds)
        except RedisError:
            logger.warning("session_store_failure operation=touch")
            raise SessionStoreError() from None

    def delete(self, session_id: str) -> None:
        key = self._key(session_id)
        try:
            self.client.delete(key)
        except RedisError:
            logger.warning("session_store_failure operation=delete")
            raise SessionStoreError() from None

    def mark_finalized(self, session_id: str, attempts: int) -> None:
        # This flag is API compatibility metadata, never an eligibility input.
        # WATCH prevents an old result from marking a newer turn complete or
        # resurrecting an expired/deleted key. It is not a distributed lock.
        key = self._key(session_id)
        try:
            with self.client.pipeline() as pipe:
                pipe.watch(key)
                if pipe.hget(key, "_attempts") != str(attempts):
                    return
                pipe.multi()
                pipe.hset(key, "_finalized", "1")
                pipe.expire(key, self.settings.ttl_seconds)
                pipe.execute()
        except WatchError:
            # Another update/expiry won: leave its completion metadata alone.
            return
        except RedisError:
            logger.warning("session_store_failure operation=finalize")
            raise SessionStoreError() from None

    def close(self) -> None:
        try:
            self.client.close()
        except RedisError:
            logger.warning("session_store_failure operation=shutdown")


class InMemorySessionStore:
    """Explicit test/dev backend only; matching TTL semantics with an injectable clock."""
    def __init__(self, settings: SessionSettings | None = None, *, clock=monotonic):
        self.settings = settings or SessionSettings(backend="memory")
        self._clock = clock
        self._entries: dict[str, tuple[SessionState, float]] = {}
        self._lock = Lock()

    def _prune(self):
        now = self._clock()
        for key in [key for key, (_, expiry) in self._entries.items() if expiry <= now]:
            del self._entries[key]

    def get(self, session_id: str) -> SessionState:
        validate_session_id(session_id)
        with self._lock:
            self._prune()
            state, _ = self._entries.get(session_id, (SessionState(), 0))
            if session_id in self._entries:
                self._entries[session_id] = (state, self._clock() + self.settings.ttl_seconds)
            return state

    def merge(self, session_id: str, update: ProfileExtractionResult) -> SessionState:
        validate_session_id(session_id)
        update = ProfileExtractionResult.model_validate(update)
        with self._lock:
            self._prune()
            previous, _ = self._entries.get(session_id, (SessionState(), 0))
            profile = UserProfile.model_validate(previous.profile.model_dump() | update.model_dump(exclude_none=True))
            state = SessionState(profile, previous.attempts + 1)
            self._entries[session_id] = (state, self._clock() + self.settings.ttl_seconds)
            return state

    def touch(self, session_id: str) -> None:
        self.get(session_id)

    def delete(self, session_id: str) -> None:
        validate_session_id(session_id)
        with self._lock:
            self._entries.pop(session_id, None)

    def mark_finalized(self, session_id: str, attempts: int) -> None:
        validate_session_id(session_id)
        with self._lock:
            self._prune()
            state, _ = self._entries.get(session_id, (SessionState(), 0))
            if session_id in self._entries and state.attempts == attempts:
                self._entries[session_id] = (replace(state, finalized=True), self._clock() + self.settings.ttl_seconds)

    def close(self) -> None:
        pass


def create_session_store(settings: SessionSettings | None = None) -> SessionStore:
    settings = settings or SessionSettings.from_environment()
    if settings.backend == "memory":
        logger.warning("session_store_backend_memory explicit_development_mode")
        return InMemorySessionStore(settings)
    try:
        client = Redis.from_url(settings.redis_url, decode_responses=True,
                                socket_connect_timeout=3, socket_timeout=3,
                                retry=Retry(NoBackoff(), 0))
    except (ValueError, RedisError):
        raise ConfigurationError("Redis client configuration is invalid.") from None
    return RedisSessionStore(client, settings)


def get_session_store(request: Request) -> SessionStore:
    """One reusable client/pool per application lifespan, injectable in API tests."""
    return request.app.state.conversation_store
