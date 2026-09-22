"""Keep process-local request counters isolated between independent tests."""
import pytest


@pytest.fixture(autouse=True)
def isolate_request_rate_counters():
    from backend.server import _rate_limit_events, _rate_limit_lock
    with _rate_limit_lock:
        _rate_limit_events.clear()
    yield
    with _rate_limit_lock:
        _rate_limit_events.clear()
