import asyncio
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import uuid
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import numpy as np
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

from backend import server
from backend.config import ASRSettings, ConfigurationError, ProtectionSettings, SessionSettings
from backend.profile_schemas import ProfileExtractionResult
from backend.services.asr_service import AudioDecodeError, normalize_audio
from backend.services.audio_cleanup import cleanup_audio
from backend.services.login_throttle import LoginThrottle, LOGIN_ATTEMPT
from backend.services.session_store import InMemorySessionStore, RedisSessionStore
from tests.support import create_test_database as create_database


def test_login_failure_lockout_expiry_and_success_reset():
    now = [100.0]
    throttle = LoginThrottle(InMemorySessionStore(),
                             ProtectionSettings(admin_login_attempts=2, admin_login_cooldown_seconds=10),
                             clock=lambda: now[0])
    throttle.attempt('client', False)
    throttle.attempt('client', True)
    assert not throttle.failures
    throttle.attempt('client', False)
    throttle.attempt('client', False)
    for valid in (True, False):
        with pytest.raises(HTTPException) as error:
            throttle.attempt('client', valid)
        assert error.value.status_code == 429
        assert error.value.headers['Retry-After'] == '10'
    throttle.attempt('different-client', True)
    now[0] += 10
    throttle.attempt('client', True)
    assert not throttle.failures


def test_redis_throttle_atomic_call_and_fail_closed():
    redis = Mock()
    throttle = LoginThrottle(RedisSessionStore(redis, SessionSettings()))
    redis.eval.return_value = 0
    throttle.attempt('client', False)
    args = redis.eval.call_args.args
    assert args[0] == LOGIN_ATTEMPT and args[1] == 1
    assert ':admin-login:' in args[2] and 'client' not in args[2]
    assert args[3:] == (5, 300, 0)
    throttle.attempt('client', True)
    assert redis.eval.call_args.args[-1] == 1
    redis.eval.return_value = 17
    with pytest.raises(HTTPException) as error:
        throttle.attempt('client', True)
    assert error.value.status_code == 429
    redis.eval.side_effect = RedisConnectionError('private upstream details')
    with pytest.raises(HTTPException) as error:
        throttle.attempt('client', False)
    assert error.value.status_code == 503
    assert 'private' not in error.value.detail


def test_http_login_throttling_and_session_deletion(monkeypatch):
    password = secrets.token_urlsafe(32)
    monkeypatch.setenv('ADMIN_PASSWORD', password)
    monkeypatch.setenv('ADMIN_SESSION_SECRET', secrets.token_urlsafe(48))
    monkeypatch.setenv('ADMIN_LOGIN_ATTEMPTS', '2')
    engine, factory = create_database()
    store = InMemorySessionStore()
    server.app.state.session_factory = factory
    server.app.state.conversation_store = store
    try:
        with TestClient(server.app) as client:
            def login(value):
                return client.post('/admin/login', json={'password': value}, headers={'X-Admin-Login': '1'})
            assert login(secrets.token_urlsafe(32)).status_code == 401
            assert login(password).status_code == 200
            assert login(secrets.token_urlsafe(32)).status_code == 401
            assert login(secrets.token_urlsafe(32)).status_code == 401
            locked = login(password)
            assert locked.status_code == 429 and 'Retry-After' in locked.headers
            sid = uuid.uuid4().hex
            values = {name: None for name in ProfileExtractionResult.model_fields}
            values['age'] = 25
            store.merge(sid, ProfileExtractionResult(**values))
            assert client.delete('/session/' + sid).json() == {'deleted': True}
            assert store.get(sid).profile.age is None
            assert client.delete('/session/' + sid).status_code == 200
            assert client.delete('/session/invalid!').status_code == 422
    finally:
        del server.app.state.session_factory
        del server.app.state.conversation_store
        engine.dispose()


def test_cleanup_only_expired_completed_generated_files(tmp_path):
    old = tmp_path / f'{uuid.uuid4()}.mp3'
    fresh = tmp_path / f'{uuid.uuid4()}.mp3'
    partial = tmp_path / f'{uuid.uuid4()}.mp3.part'
    other = tmp_path / 'recording.mp3'
    nested = tmp_path / 'nested'
    nested.mkdir()
    inside = nested / f'{uuid.uuid4()}.mp3'
    for path in (old, fresh, partial, other, inside):
        path.write_bytes(b'audio')
        os.utime(path, (1, 1))
    os.utime(fresh, (95, 95))
    assert cleanup_audio(tmp_path, 10, now=100) == 1
    assert not old.exists()
    assert all(p.exists() for p in (fresh, partial, other, inside))
    assert cleanup_audio(tmp_path, 10, now=100) == 0


def test_cleanup_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / 'outside.mp3'
    outside.write_bytes(b'private')
    directory = tmp_path / 'audio'
    directory.mkdir()
    link = directory / f'{uuid.uuid4()}.mp3'
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip('OS does not permit symlinks')
    os.utime(outside, (1, 1))
    assert cleanup_audio(directory, 10, now=100) == 0
    assert outside.read_bytes() == b'private'


def test_in_progress_audio_not_deleted_and_completed_audio_expires(tmp_path):
    def save(path):
        p = Path(path)
        assert p.suffix == '.part'
        p.write_bytes(b'partial')
        os.utime(p, (1, 1))
        assert cleanup_audio(tmp_path, 10, now=100) == 0
        assert p.exists()
        p.write_bytes(b'complete')
    with patch.object(server, 'AUDIO_DIR', str(tmp_path)), patch.object(server, 'gTTS') as provider:
        provider.return_value.save.side_effect = save
        url = server.speak_hindi('fixture')
    completed = tmp_path / url.split('/')[-1]
    assert completed.read_bytes() == b'complete'
    assert not list(tmp_path.glob('*.part'))
    os.utime(completed, (1, 1))
    assert cleanup_audio(tmp_path, 10, now=100) == 1


@pytest.mark.parametrize('name', list(ProtectionSettings.__dataclass_fields__))
def test_central_limits_reject_invalid_values(monkeypatch, name):
    for value in ('0', '-1', 'invalid'):
        monkeypatch.setenv(name.upper(), value)
        with pytest.raises(ConfigurationError):
            ProtectionSettings.from_environment()
    monkeypatch.setenv(name.upper(), '7')
    assert getattr(ProtectionSettings.from_environment(), name) == 7


def test_asr_revision_must_be_immutable(monkeypatch):
    monkeypatch.setenv('ASR_MODEL_REVISION', 'main')
    with pytest.raises(ConfigurationError, match='immutable'):
        ASRSettings.from_environment()
    # Synthetic test-only hash, never supplied as a usable model revision.
    revision = secrets.token_hex(20)
    monkeypatch.setenv('ASR_MODEL_REVISION', revision)
    assert ASRSettings.from_environment().revision == revision


def test_request_rate_limits_remain_enforced_and_expire():
    request = Request({'type': 'http', 'client': ('rate-test', 1)})
    with patch.object(server.time, 'monotonic', return_value=100):
        server.enforce_rate_limit(request, 'speech', 2)
        server.enforce_rate_limit(request, 'speech', 2)
        with pytest.raises(HTTPException) as error:
            server.enforce_rate_limit(request, 'speech', 2)
        assert error.value.status_code == 429
        server.enforce_rate_limit(request, 'conversation', 2)
    with patch.object(server.time, 'monotonic', return_value=161):
        server.enforce_rate_limit(request, 'speech', 2)


def test_periodic_cleanup_runs_without_new_audio_requests(tmp_path):
    old = tmp_path / f'{uuid.uuid4()}.mp3'
    old.write_bytes(b'expired')
    os.utime(old, (1, 1))
    async def stop_after_first_pass(_):
        assert not old.exists()
        raise asyncio.CancelledError
    with patch.object(server, 'AUDIO_DIR', str(tmp_path)), \
         patch.object(server.asyncio, 'sleep', side_effect=stop_after_first_pass):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(server.periodic_audio_cleanup())


@pytest.mark.parametrize('chunks,status', [([b''], 422), ([b'ab', b'cd'], 413)])
def test_chunked_upload_limits_and_temp_cleanup(tmp_path, chunks, status):
    upload = Mock(filename='voice.webm')
    upload.read = AsyncMock(side_effect=chunks)
    upload.close = AsyncMock()
    asr = Mock()
    request = Request({'type': 'http', 'client': ('test-limits', 1)})
    original = server.tempfile.NamedTemporaryFile
    def temporary(**kwargs):
        return original(dir=tmp_path, **kwargs)
    with patch.object(server, 'MAX_AUDIO_UPLOAD_BYTES', 3), \
         patch.object(server.tempfile, 'NamedTemporaryFile', side_effect=temporary), \
         patch.object(server, 'enforce_rate_limit'):
        with pytest.raises((HTTPException, server.InputValidationError)) as error:
            asyncio.run(server.speech_to_text(request=request, file=upload, session_id='test',
                        session=Mock(), asr=asr, extractor=Mock(), store=Mock()))
    assert getattr(error.value, 'status_code', 422) == status
    assert all(call.args == (server.UPLOAD_CHUNK_SIZE,) for call in upload.read.await_args_list)
    upload.close.assert_awaited_once()
    asr.transcribe.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_decoded_duration_and_ffmpeg_timeout_are_preserved():
    with patch('backend.services.asr_service.MAX_AUDIO_DURATION_SECONDS', 1), \
         patch('backend.services.asr_service.ffmpeg_binary', return_value='ffmpeg'), \
         patch('backend.services.asr_service.subprocess.run') as run:
        run.return_value.stdout = np.zeros(16001, dtype='<f4').tobytes()
        with pytest.raises(AudioDecodeError):
            normalize_audio('fixture.webm')
        assert run.call_args.kwargs['timeout'] == 60
        command = run.call_args.args[0]
        assert command[command.index('-t') + 1] == '2'
        run.side_effect = subprocess.TimeoutExpired('ffmpeg', 60)
        with pytest.raises(AudioDecodeError, match='timed out'):
            normalize_audio('fixture.webm')


def test_frontend_session_privacy_behaviour():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for browser-JavaScript unit checks')
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / 'tests/frontend_privacy.cjs')],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
