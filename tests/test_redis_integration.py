"""Opt-in real Redis checks. Never flush a database or touch unrelated keys."""
from concurrent.futures import ThreadPoolExecutor
import os
import time
from uuid import uuid4

import pytest

from backend.config import SessionSettings
from backend.profile_schemas import ProfileExtractionResult, UserProfile
from backend.services.session_store import SessionState, create_session_store


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="TEST_REDIS_URL not configured (real Redis opt-in)")
def test_real_redis_sessions():
    settings = SessionSettings(redis_url=os.environ["TEST_REDIS_URL"],
                               key_prefix=f"bhashasetu:test:{uuid4().hex}", ttl_seconds=30)
    first = create_session_store(settings)
    second = create_session_store(settings)
    ids = ["abc", "xyz", "expires", "concurrent"]

    def update(**fields):
        return ProfileExtractionResult.model_validate({"age": None, "gender": None, "annual_income": None} | fields)

    try:
        assert first.get("abc") == SessionState()
        first.merge("abc", update(age=25))
        # An independent client/worker reads the same Redis state.
        assert second.get("abc").profile.age == 25
        second.merge("abc", update(gender="Male"))
        first.merge("abc", update(annual_income=100000))
        assert second.get("abc").profile == UserProfile(age=25, gender="Male", annual_income=100000)
        first.merge("xyz", update(age=60, gender="Female"))
        first.merge("abc", update(age=26, annual_income=200000))
        assert second.get("abc").profile == UserProfile(age=26, gender="Male", annual_income=200000)
        assert second.get("xyz").profile == UserProfile(age=60, gender="Female")
        key = f"{settings.key_prefix}:abc"
        assert 0 < first.client.ttl(key) <= 30
        first.client.expire(key, 2)
        second.touch("abc")
        assert 20 < first.client.ttl(key) <= 30
        first.close()  # client lifecycle ends; keys must survive
        first = create_session_store(settings)
        assert first.get("abc").profile.age == 26
        first.merge("abc", update(state_or_ut="Bihar", student_status=False, family_annual_income=150000))
        expanded = second.get("abc").profile
        assert expanded.state_or_ut == "Bihar"
        assert expanded.student_status is False and expanded.family_annual_income == 150000
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda item: first.merge("concurrent", item), [update(age=25), update(gender="Male")]))
        assert second.get("concurrent").profile == UserProfile(age=25, gender="Male")
        state = first.get("abc")
        first.mark_finalized("abc", state.attempts)
        assert second.get("abc").finalized
        first.delete("abc")
        first.mark_finalized("abc", state.attempts)
        assert second.get("abc") == SessionState()
        assert second.get("xyz").profile.age == 60
        first.merge("expires", update(age=25))
        first.client.pexpire(f"{settings.key_prefix}:expires", 50)
        # Poll existence (not store.get, which intentionally slides TTL).
        deadline = time.monotonic() + 3
        while first.client.exists(f"{settings.key_prefix}:expires") and time.monotonic() < deadline:
            time.sleep(0.02)
        assert second.get("expires") == SessionState()
    finally:
        try:
            # Only the exact keys owned by this run. Never FLUSHDB or wildcard deletion.
            for sid in ids:
                second.delete(sid)
        finally:
            first.close()
            second.close()
