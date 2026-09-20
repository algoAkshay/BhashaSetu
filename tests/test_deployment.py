"""Small deployment checks; no production database or external providers."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.config import SessionSettings, database_url
from backend.server import app
from backend.services.asr_service import get_asr_service
from backend.services.profile_extraction_service import get_profile_extractor
from backend.services.session_store import InMemorySessionStore
from tests.support import test_database as create_database


def test_startup_health_and_static_without_provider_work():
    engine, factory = create_database()
    store = InMemorySessionStore()
    app.state.session_factory = factory
    app.state.conversation_store = store
    forbidden = Mock(side_effect=AssertionError("Health must not invoke providers"))
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_asr_service] = forbidden
    app.dependency_overrides[get_profile_extractor] = forbidden
    try:
        with TestClient(app) as client:
            # Startup performs its existing database check; /health itself does not.
            with patch.object(app.state, "session_factory", forbidden), \
                 patch("backend.server.bounded_speech", forbidden), \
                 patch.object(store, "get", forbidden):
                response = client.get("/health")
                assert response.status_code == 200
                assert response.json() == {"status": "ok"}
                forbidden.assert_not_called()
            assert client.get("/").status_code == 200
            assert client.get("/static/script.js").status_code == 200
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        del app.state.session_factory
        del app.state.conversation_store
        engine.dispose()


def test_managed_service_urls_use_environment(monkeypatch):
    # These are synthetic endpoints, never opened by this configuration test.
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@postgres.railway.internal:5432/railway")
    monkeypatch.setenv("REDIS_URL", "redis://redis.railway.internal:6379/0")
    monkeypatch.setenv("SESSION_BACKEND", "redis")
    assert database_url().drivername == "postgresql+psycopg"
    assert database_url().host == "postgres.railway.internal"
    assert SessionSettings.from_environment().redis_url == "redis://redis.railway.internal:6379/0"


def test_deployment_configuration_keeps_explicit_runtime_contract():
    config = json.loads((Path(__file__).resolve().parents[1] / "railpack.json").read_text())
    assert config["packages"]["python"] == "3.12"
    deploy = config["deploy"]
    assert "ffmpeg" in deploy["aptPackages"]
    assert deploy["variables"]["HF_HOME"].startswith("/tmp/")
    command = deploy["startCommand"]
    assert "backend.server:app" in command and '"$PORT"' in command
    assert "--host 0.0.0.0" in command and "--workers 1" in command
    assert "seed" not in command and "import_dataset" not in command
