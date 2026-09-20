"""Offline storage tests; no sockets or Redis service required."""
from concurrent.futures import ThreadPoolExecutor
import os
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError
from redis.exceptions import ConnectionError, TimeoutError, ResponseError, WatchError

from backend.config import ConfigurationError, SessionSettings
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.session_store import (
    InMemorySessionStore, RedisSessionStore, SessionState, SessionStoreError,
    InvalidSessionID, create_session_store,
)


def update(**fields):
    return ProfileExtractionResult.model_validate(
        {"age": None, "gender": None, "annual_income": None} | fields)


@pytest.fixture
def redis_store():
    client = MagicMock()
    pipe = client.pipeline.return_value.__enter__.return_value
    return RedisSessionStore(client, SessionSettings()), client, pipe


def test_memory_merge_corrections_isolation_and_delete():
    store = InMemorySessionStore()
    assert store.get("abc") == SessionState()
    store.merge("abc", update(age=25, gender="Male"))
    store.merge("xyz", update(age=60, gender="Female"))
    state = store.merge("abc", update(annual_income=100000))
    assert state.profile == UserProfile(age=25, gender="Male", annual_income=100000)
    state = store.merge("abc", update(age=26, annual_income=200000))
    assert state.profile == UserProfile(age=26, gender="Male", annual_income=200000)
    assert store.get("xyz").profile == UserProfile(age=60, gender="Female")
    store.delete("abc")
    assert store.get("abc") == SessionState()
    assert store.get("xyz").profile.age == 60


def test_memory_expiry_and_sliding_get_merge_touch():
    now = [0]
    store = InMemorySessionStore(SessionSettings(backend="memory", ttl_seconds=10), clock=lambda: now[0])
    store.merge("abc", update(age=25))
    now[0] = 9
    assert store.get("abc").profile.age == 25  # expiry now 19
    now[0] = 18
    store.touch("abc")  # expiry 28
    now[0] = 27
    store.merge("abc", update(gender="Male"))  # expiry 37
    now[0] = 37
    assert store.get("abc") == SessionState()
    store.touch("missing")
    assert not store._entries  # no immortal empty placeholders


def test_memory_concurrent_different_fields_and_completion_guard():
    store = InMemorySessionStore()
    with ThreadPoolExecutor(2) as executor:
        list(executor.map(lambda item: store.merge("abc", item), [update(age=25), update(gender="Male")]))
    assert store.get("abc").profile == UserProfile(age=25, gender="Male")
    assert store.get("abc").attempts == 2
    store.mark_finalized("abc", 1)
    assert not store.get("abc").finalized
    store.mark_finalized("abc", 2)
    assert store.get("abc").finalized
    store.delete("abc")
    store.mark_finalized("abc", 2)
    assert store.get("abc") == SessionState()


@pytest.mark.parametrize("values,expected", [
    ({}, UserProfile()),
    ({"age": "25"}, UserProfile(age=25)),
    ({"age": "0", "gender": "Male", "annual_income": "0"}, UserProfile(age=0, gender="Male", annual_income=0)),
    ({"age": "65", "gender": "Female", "annual_income": "150000", "_attempts": "3", "_finalized": "1"},
     UserProfile(age=65, gender="Female", annual_income=150000)),
])
def test_redis_read_converts_and_validates_and_refreshes_ttl(redis_store, values, expected):
    store, client, pipe = redis_store
    pipe.execute.return_value = [values, bool(values)]
    state = store.get("abc")
    assert state.profile == expected
    client.pipeline.assert_called_once_with(transaction=True)
    pipe.hgetall.assert_called_once_with("bhashasetu:session:abc")
    pipe.expire.assert_called_once_with("bhashasetu:session:abc", 1800)
    pipe.hset.assert_not_called()


@pytest.mark.parametrize("values", [
    {"age": "-5"}, {"age": "121"}, {"age": "25.5"}, {"age": "true"},
    {"age": ""}, {"annual_income": "-1"}, {"annual_income": "one lakh"},
    {"annual_income": "1e5"}, {"gender": "male"}, {"gender": "private-profile"},
    {"unknown": "private-data"}, {"_attempts": "-1"}, {"_finalized": "true"},
])
def test_redis_corrupt_data_fails_safely(redis_store, values, caplog):
    store, _, pipe = redis_store
    pipe.execute.return_value = [values, True]
    with pytest.raises(SessionStoreError) as error:
        store.get("abc")
    assert "private" not in caplog.text and "private" not in str(error.value)
    assert "invalid_stored_data" in caplog.text


def test_redis_merge_is_partial_atomic_and_returns_merged_snapshot(redis_store):
    store, client, pipe = redis_store
    pipe.execute.return_value = [1, 2, True, {
        "age": "25", "gender": "Male", "annual_income": "100000", "_attempts": "2", "_finalized": "0"}]
    result = store.merge("abc", update(annual_income=100000))
    assert result.profile == UserProfile(age=25, gender="Male", annual_income=100000)
    assert result.attempts == 2
    client.pipeline.assert_called_once_with(transaction=True)
    assert [call[0] for call in pipe.method_calls] == ["hset", "hincrby", "expire", "hgetall", "execute"]
    pipe.hset.assert_called_once_with("bhashasetu:session:abc", mapping={"annual_income": 100000, "_finalized": "0"})
    pipe.hincrby.assert_called_once_with("bhashasetu:session:abc", "_attempts", 1)
    pipe.expire.assert_called_once_with("bhashasetu:session:abc", 1800)


def test_redis_null_update_only_updates_compatibility_metadata(redis_store):
    store, _, pipe = redis_store
    pipe.execute.return_value = [1, 1, True, {"_attempts": "1", "_finalized": "0"}]
    result = store.merge("abc", update())
    assert result.profile == UserProfile()
    pipe.hset.assert_called_once_with("bhashasetu:session:abc", mapping={"_finalized": "0"})


def test_invalid_update_never_reaches_redis(redis_store):
    store, client, _ = redis_store
    bad = ProfileExtractionResult.model_construct(age=-5, gender="Male", annual_income=None)
    with pytest.raises(ValidationError):
        store.merge("abc", bad)
    client.pipeline.assert_not_called()


@pytest.mark.parametrize("error", [ConnectionError("redis://user:secret@host"),
    TimeoutError("private profile"), ResponseError("WRONGTYPE secret")])
@pytest.mark.parametrize("method", ["get", "merge", "touch", "delete", "mark_finalized"])
def test_redis_failures_do_not_fallback_or_leak(redis_store, error, method, caplog):
    store, client, pipe = redis_store
    pipe.execute.side_effect = error
    pipe.watch.side_effect = error
    client.expire.side_effect = error
    client.delete.side_effect = error
    with patch("backend.services.session_store.InMemorySessionStore") as memory:
        with pytest.raises(SessionStoreError) as caught:
            args = ["abc", update(age=25)] if method == "merge" else ["abc", 1] if method == "mark_finalized" else ["abc"]
            getattr(store, method)(*args)
    memory.assert_not_called()
    assert not any(word in caplog.text + str(caught.value) for word in ["secret", "private", "redis://"])


def test_delete_and_touch_target_only_selected_key(redis_store):
    store, client, _ = redis_store
    store.delete("abc")
    store.touch("xyz")
    client.delete.assert_called_once_with("bhashasetu:session:abc")
    client.expire.assert_called_once_with("bhashasetu:session:xyz", 1800)
    client.flushdb.assert_not_called()


@pytest.mark.parametrize("stored_attempt", [None, "4"])
def test_finalize_cannot_resurrect_or_mark_newer_turn(redis_store, stored_attempt):
    store, _, pipe = redis_store
    pipe.hget.return_value = stored_attempt
    store.mark_finalized("abc", 3)
    pipe.hset.assert_not_called()


def test_finalize_uses_optimistic_guard(redis_store):
    store, _, pipe = redis_store
    pipe.hget.return_value = "3"
    store.mark_finalized("abc", 3)
    pipe.watch.assert_called_once_with("bhashasetu:session:abc")
    pipe.multi.assert_called_once()
    pipe.hset.assert_called_once_with("bhashasetu:session:abc", "_finalized", "1")
    pipe.execute.side_effect = WatchError()
    store.mark_finalized("abc", 3)  # concurrent newer turn wins without retry or lock


@pytest.mark.parametrize("sid", ["", " ", "a" * 129, "a:b", "abc\n", "*", "../../x", None, 123])
def test_session_ids_are_bounded_and_validated_before_redis(redis_store, sid):
    store, client, _ = redis_store
    with pytest.raises(InvalidSessionID):
        store.get(sid)
    client.pipeline.assert_not_called()
    with pytest.raises(InvalidSessionID):
        InMemorySessionStore().merge(sid, update(age=25))


@pytest.mark.parametrize("kwargs", [{"backend": "auto"}, {"ttl_seconds": 0}, {"ttl_seconds": True},
    {"ttl_seconds": 604801}, {"key_prefix": "*"}, {"redis_url": "http://user:secret@host"},
    {"redis_url": "redis://host:bad/0"}, {"redis_url": "redis://host/invalid"}])
def test_invalid_settings_are_sanitized(kwargs):
    with pytest.raises(ConfigurationError) as error:
        SessionSettings(**kwargs)
    assert "secret" not in str(error.value)


def test_factory_is_explicit_lazy_supports_tls_and_pool_close():
    settings = SessionSettings(redis_url="rediss://user:private-password@localhost:6380/1")
    assert "private" not in repr(settings)
    with patch("backend.services.session_store.Redis.from_url") as factory:
        store = create_session_store(settings)
        assert isinstance(store, RedisSessionStore)
        factory.return_value.ping.assert_not_called()
        assert factory.call_args.kwargs["decode_responses"] is True
        assert factory.call_args.kwargs["socket_timeout"] == 3
        store.close()
        factory.return_value.close.assert_called_once()
    assert isinstance(create_session_store(SessionSettings(backend="memory")), InMemorySessionStore)
    with patch.dict(os.environ, {"SESSION_TTL_SECONDS": "oops"}):
        with pytest.raises(ConfigurationError):
            SessionSettings.from_environment()


def test_actual_client_configuration_is_lazy_and_does_not_connect():
    with patch("redis.connection.Connection.connect", side_effect=AssertionError("Network forbidden")):
        store = create_session_store(SessionSettings())
        assert store.client.connection_pool.connection_kwargs["decode_responses"] is True
        store.close()
