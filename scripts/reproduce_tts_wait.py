"""Local HTTP fault injection. No external services or production data are used.

Run: python -m scripts.reproduce_tts_wait --mode stall
The actual gTTS HTTP transport is held until the local client finishes waiting.
"""
import argparse
from contextlib import ExitStack
import json
import logging
import socket
import tempfile
import threading
import time
from unittest.mock import patch

import httpx
import requests
import uvicorn

from backend import server
from backend.db.seed import seed_database
from backend.profile_schemas import ProfileExtractionResult
from backend.services.asr_service import get_asr_service
from backend.services.profile_extraction_service import get_profile_extractor
from backend.services.session_store import InMemorySessionStore
from tests.support import test_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["stall", "success", "failure"], default="stall")
    parser.add_argument("--input", choices=["text", "voice"], default="text")
    parser.add_argument("--question", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.perf_counter()
    counts = {"extraction": 0, "asr": 0, "tts_network": 0}
    release = threading.Event()

    def event(stage, **values):
        print(json.dumps({"stage": stage, "elapsed_s": round(time.perf_counter() - started, 3), **values}), flush=True)

    class Extractor:
        def extract_profile(self, *_args, **kwargs):
            counts["extraction"] += 1
            event("extraction_start_end")
            if args.question:
                return ProfileExtractionResult(age=None, gender=None, annual_income=None)
            return ProfileExtractionResult(age=25, gender="Male", annual_income=100000)

    def no_asr():
        counts["asr"] += 1
        if args.input == "text":
            raise AssertionError("Text must not initialize ASR")
        class ASR:
            def transcribe(self, path):
                event("asr_start_end")
                return "age 25 male annual income 100000"
        return ASR()

    def transport(*args, **kwargs):
        counts["tts_network"] += 1
        event("tts_network_start", timeout=kwargs.get("timeout"))
        if args_mode == "stall":
            release.wait(20)
        raise requests.exceptions.Timeout("injected transport failure")

    args_mode = args.mode
    engine, factory = test_database()
    seed_database(factory)
    store = InMemorySessionStore()
    server.app.state.session_factory = factory
    server.app.state.conversation_store = store
    server.app.dependency_overrides[get_profile_extractor] = lambda: Extractor()
    server.app.dependency_overrides[get_asr_service] = no_asr
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    config = uvicorn.Config(server.app, log_level="warning", access_log=False)
    host = uvicorn.Server(config)
    thread = threading.Thread(target=host.run, kwargs={"sockets": [listener]}, daemon=True)
    try:
        with tempfile.TemporaryDirectory(prefix="bhasha-tts-repro-") as audio_dir, ExitStack() as patches:
            patches.enter_context(patch.object(server, "AUDIO_DIR", audio_dir))
            original_response = server.conversation_response
            async def timed_response(*args, **kwargs):
                event("request_received")
                value = await original_response(*args, **kwargs)
                event("response_constructed")
                return value
            patches.enter_context(patch.object(server, "conversation_response", timed_response))
            if args.mode == "success":
                patches.enter_context(patch.object(server, "speak_hindi", return_value="/audio/test.mp3"))
            else:
                patches.enter_context(patch("requests.Session.send", side_effect=transport))
            for target, name, label in [(store, "get", "session_load"), (store, "merge", "session_merge"),
                                        (server.EligibilityService, "evaluate_all_schemes", "eligibility"),
                                        (server, "result_narration", "narration")]:
                original = getattr(target, name)
                def timed(*args, _original=original, _label=label, **kwargs):
                    event(_label + "_start")
                    value = _original(*args, **kwargs)
                    extra = {}
                    if _label == "narration":
                        extra = {"characters": len(value), "network_chunks": len(server.gTTS(value, lang="hi").get_bodies()),
                                 "previous_message_chunks": len(server.gTTS(args[0]["ai_text"], lang="hi").get_bodies())}
                    event(_label + "_end", **extra)
                    return value
                patches.enter_context(patch.object(target, name, timed))
            thread.start()
            deadline = time.monotonic() + 10
            while not host.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(.01)
            if not host.started:
                raise RuntimeError("Local server did not start")
            event("http_request_start")
            request_started = time.perf_counter()
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=6, trust_env=False) as client:
                try:
                    if args.input == "text":
                        result = client.post("/conversation", json={"text": "age 25 male annual income 100000", "session_id": "repro"})
                    else:
                        result = client.post("/speech-to-text", data={"session_id": "repro"},
                                             files={"file": ("voice.webm", b"injected audio", "audio/webm")})
                    payload = result.json()
                    event("http_response", status=result.status_code, request_s=round(time.perf_counter()-request_started, 3),
                          matched=len(payload.get("schemes", [])), audio_available=payload.get("audio_url") is not None)
                except httpx.ReadTimeout:
                    event("http_client_timeout", request_s=round(time.perf_counter()-request_started, 3))
                event("root_responsive", status=client.get("/").status_code)
            event("session_preserved", attempts=store.get("repro").attempts, **counts)
            release.set()
            time.sleep(.2)
    finally:
        release.set()
        host.should_exit = True
        thread.join(10)
        listener.close()
        server.app.dependency_overrides.clear()
        del server.app.state.session_factory
        del server.app.state.conversation_store
        engine.dispose()


if __name__ == "__main__":
    main()
