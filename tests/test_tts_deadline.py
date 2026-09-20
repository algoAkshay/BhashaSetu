"""A stalled synchronous provider must not hold the conversation response hostage."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Event
from unittest.mock import patch

import pytest

from backend import server


def test_gtts_network_timeout_and_failed_partial_file_cleanup(tmp_path):
    def fail(path):
        Path(path).write_bytes(b"partial")
        raise RuntimeError("network failure")
    with patch.object(server, "AUDIO_DIR", str(tmp_path)), patch.object(server, "gTTS") as provider:
        provider.return_value.save.side_effect = fail
        with pytest.raises(RuntimeError):
            server.speak_hindi("question")
        provider.assert_called_once_with(text="question", lang="hi", timeout=server.TTS_NETWORK_TIMEOUT)
        assert not list(tmp_path.iterdir())


def test_successful_gtts_still_saves_audio(tmp_path):
    with patch.object(server, "AUDIO_DIR", str(tmp_path)), patch.object(server, "gTTS") as provider:
        provider.return_value.save.side_effect = lambda path: Path(path).write_bytes(b"audio fixture")
        url = server.speak_hindi("question")
        assert url.startswith("/audio/")
        assert (tmp_path / url.rsplit("/", 1)[1]).read_bytes() == b"audio fixture"


def test_deadline_slots_stay_bounded_and_late_audio_is_removed(tmp_path):
    release = Event()
    calls = []
    def stalled(text):
        calls.append(text)
        release.wait(5)
        (tmp_path / (text + ".mp3")).write_bytes(b"late fixture")
        return "/audio/" + text + ".mp3"

    async def exercise():
        assert await server.bounded_speech("first") is None
        assert await server.bounded_speech("second") is None
        # Both abandoned calls still own their slots; no third job is queued.
        assert await server.bounded_speech("third") is None
        assert calls == ["first", "second"]
        assert not server._tts_slots.acquire(blocking=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with patch.object(server, "_tts_executor", pool), patch.object(server, "_tts_slots", BoundedSemaphore(2)), \
             patch.object(server, "TTS_RESPONSE_TIMEOUT_SECONDS", .03), patch.object(server, "speak_hindi", stalled), \
             patch.object(server, "AUDIO_DIR", str(tmp_path)):
            try:
                asyncio.run(exercise())
            finally:
                release.set()
                pool.shutdown(wait=True)
            assert not list(tmp_path.iterdir())
            assert server._tts_slots.acquire(blocking=False)
            server._tts_slots.release()


def test_request_cancellation_does_not_free_a_still_running_slot():
    release, entered = Event(), Event()
    def stalled(text):
        entered.set()
        release.wait(5)
        return None

    async def exercise():
        task = asyncio.create_task(server.bounded_speech("question"))
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not server._tts_slots.acquire(blocking=False)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with patch.object(server, "_tts_executor", pool), patch.object(server, "_tts_slots", BoundedSemaphore(1)), \
             patch.object(server, "speak_hindi", stalled):
            try:
                asyncio.run(exercise())
            finally:
                release.set()
                pool.shutdown(wait=True)
