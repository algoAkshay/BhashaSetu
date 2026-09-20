import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.config import ConfigurationError, database_url
from backend.db.seed import seed_database
from backend.models import EligibilityRule
from backend.profile_schemas import ProfileExtractionResult
from backend.repositories.scheme_repository import SchemeRepository
from backend.server import app
from backend.services.asr_service import ASRService, ASRUnavailableError, AudioDecodeError, get_asr_service
from backend.services.profile_extraction_service import ProfileExtractor, ProfileExtractionError, get_profile_extractor
from backend.services.session_store import InMemorySessionStore, get_session_store, SessionStoreError
from tests.support import test_database


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.engine, self.factory = test_database()
        seed_database(self.factory)
        app.state.session_factory = self.factory
        self.asr = Mock(spec=ASRService)
        app.dependency_overrides[get_asr_service] = lambda: self.asr
        self.extractor = Mock(spec=ProfileExtractor)
        self.extractor.extract_profile.return_value = ProfileExtractionResult(
            age=None, gender=None, annual_income=None)
        app.dependency_overrides[get_profile_extractor] = lambda: self.extractor
        self.store = InMemorySessionStore()
        app.dependency_overrides[get_session_store] = lambda: self.store
        self.client = TestClient(app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        del app.state.session_factory
        app.dependency_overrides.pop(get_asr_service, None)
        app.dependency_overrides.pop(get_profile_extractor, None)
        app.dependency_overrides.pop(get_session_store, None)
        self.engine.dispose()

    def test_ui_and_static_files_still_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/static/script.js").status_code, 200)
        self.assertEqual(self.client.get("/docs").status_code, 200)

    def test_read_endpoints(self):
        response = self.client.get("/api/schemes")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 67)
        scheme = response.json()[0]
        self.assertEqual(self.client.get(f"/api/schemes/{scheme['id']}").json(), scheme)
        self.assertTrue(scheme["rules"])
        self.assertEqual(self.client.get("/api/schemes/999999").status_code, 404)
        self.assertEqual(self.client.get("/api/schemes/invalid").status_code, 422)
        self.assertEqual(self.client.post("/api/schemes", json={}).status_code, 405)

    def test_inactive_schemes_not_listed_or_evaluated(self):
        with self.factory.begin() as session:
            repository = SchemeRepository(session)
            repository.set_active(repository.get_by_id(1), False)
        self.assertEqual(len(self.client.get("/api/schemes").json()), 66)
        self.assertFalse(self.client.get("/api/schemes/1").json()["is_active"])
        response = self.client.post("/api/eligibility", json={"attributes": {}})
        self.assertEqual(len(response.json()), 66)

    def test_explainable_eligibility_and_missing_information(self):
        response = self.client.post("/api/eligibility", json={"attributes": {"age": 64, "annual_income": 150000, "gender": "Female"}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(result["status"] == "ELIGIBLE" for result in response.json()))
        check = response.json()[0]["checks"][0]
        self.assertEqual(set(check), {"field", "actual", "operator", "expected", "passed"})
        response = self.client.post("/api/eligibility", json={"attributes": {}})
        self.assertTrue(all(result["status"] == "NEED_MORE_INFORMATION" for result in response.json()))
        self.assertTrue(all("annual_income" in result["missing_fields"] for result in response.json()))

    def test_malformed_attributes_are_422(self):
        for attributes in [{"age": "sixty"}, {"age": True}, {"annual_income": "1 lakh"}, {"unknown": 1}]:
            with self.subTest(attributes=attributes):
                self.assertEqual(self.client.post("/api/eligibility", json={"attributes": attributes}).status_code, 422)

    def test_invalid_persisted_rule_returns_error_not_decision(self):
        with self.factory.begin() as session:
            session.get(EligibilityRule, 1).operator = "INVALID"
        response = self.client.post("/api/eligibility", json={"attributes": {}})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("status", response.json())

    def test_database_failure_is_sanitized_503(self):
        error = OperationalError("secret SQL", {}, RuntimeError("secret credentials"))
        with patch.object(SchemeRepository, "get_active_schemes", side_effect=error):
            response = self.client.get("/api/schemes")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", response.text)

    def speech(self):
        return self.client.post("/speech-to-text", files={"file": ("voice.webm", b"mock audio", "audio/webm")},
                                data={"session_id": "api-test"})

    def test_speech_complete_profile_preserves_response_contract_and_cleans_file(self):
        self.extractor.extract_profile.return_value = ProfileExtractionResult(
            age=32, gender="Male", annual_income=150000)
        model = self.asr
        paths = []

        def transcribe(path):
            self.assertTrue(Path(path).exists())
            paths.append(path)
            return "मैं 32 साल का पुरुष हूँ और मेरी आय 150000 है"

        model.transcribe.side_effect = transcribe
        with patch("backend.server.speak_hindi", return_value="/audio/mock.mp3"):
            first = self.speech().json()
            second = self.speech().json()
            third = self.speech().json()
        self.assertTrue(first["schemes"])
        self.assertTrue(first["session"]["finalized"])
        self.assertEqual(first["session"]["attempts"], 1)
        self.assertTrue(second["schemes"])
        self.assertTrue(all(isinstance(name, str) for name in second["schemes"]))
        self.assertTrue(second["session"]["finalized"])
        self.assertTrue(second["eligibility_results"])
        self.assertEqual(second["audio_url"], "/audio/mock.mp3")
        self.assertEqual(set(second), {"user_text", "ai_text", "audio_url", "schemes", "session",
                                      "eligibility_results", "missing_fields", "next_question", "result_cards", "skipped_fields"})
        self.assertEqual(third["session"]["attempts"], 3)
        self.assertTrue(all(not Path(path).exists() for path in paths))

    def test_speech_unknown_values_remain_missing_and_can_be_completed(self):
        self.extractor.extract_profile.side_effect = [
            ProfileExtractionResult(age=None, gender=None, annual_income=None),
            ProfileExtractionResult(age=None, gender=None, annual_income=None),
            ProfileExtractionResult(age=32, gender="Male", annual_income=150000),
        ]
        model = self.asr
        model.transcribe.side_effect = ["मुझे योजना चाहिए", "पता नहीं",
                                       "मैं 32 साल का पुरुष हूँ और मेरी आय 150000 है"]
        with patch("backend.server.speak_hindi", return_value="/audio/mock.mp3"):
            self.speech()
            second = self.speech().json()
            third = self.speech().json()
        self.assertEqual(second["schemes"], [])
        self.assertFalse(second["session"]["finalized"])
        self.assertIsNone(second["session"]["age"])
        self.assertIsNone(second["session"]["gender"])
        self.assertIsNone(second["session"]["income"])
        self.assertEqual(second["missing_fields"], ["age"])
        self.assertTrue(all(result["status"] == "NEED_MORE_INFORMATION" for result in second["eligibility_results"]))
        self.assertTrue(third["session"]["finalized"])

    def test_tts_failure_preserves_text(self):
        self.asr.transcribe.return_value = "मुझे योजना चाहिए"
        with patch("backend.server.speak_hindi", side_effect=RuntimeError("offline")):
            response = self.speech()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["audio_url"])
        self.assertTrue(response.json()["ai_text"])

    def test_stalled_tts_returns_question_final_text_and_voice_without_duplicate_extraction(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import BoundedSemaphore, Event
        from time import monotonic
        from backend import server

        for mode in ("question", "final", "voice"):
            with self.subTest(mode=mode):
                release = Event()
                self.extractor.reset_mock()
                self.extractor.extract_profile.return_value = (
                    ProfileExtractionResult(age=None, gender=None, annual_income=None) if mode == "question" else
                    ProfileExtractionResult(age=25, gender="Male", annual_income=100000))
                self.asr.transcribe.return_value = "age 25 male annual income 100000"
                def stalled(text):
                    release.wait(5)
                    return None
                with ThreadPoolExecutor(max_workers=1) as pool:
                    with patch.object(server, "_tts_executor", pool), patch.object(server, "_tts_slots", BoundedSemaphore(1)), \
                         patch.object(server, "TTS_RESPONSE_TIMEOUT_SECONDS", .05), patch.object(server, "speak_hindi", side_effect=stalled) as speak:
                        try:
                            started = monotonic()
                            response = self.speech() if mode == "voice" else self.client.post("/conversation", json={
                                "text": "hello", "session_id": "deadline-" + mode})
                            self.assertLess(monotonic() - started, 2)
                            self.assertEqual(response.status_code, 200)
                            body = response.json()
                            self.assertIsNone(body["audio_url"])
                            self.assertTrue(body["ai_text"] and body["result_cards"])
                            self.assertEqual(body["session"]["attempts"], 1)
                            if mode == "question":
                                self.assertIsNotNone(body["next_question"])
                            else:
                                self.assertTrue(body["schemes"])
                            self.extractor.extract_profile.assert_called_once()
                            speak.assert_called_once()
                            if mode != "voice":
                                self.asr.transcribe.assert_not_called()
                            self.assertEqual(self.client.get("/").status_code, 200)
                        finally:
                            release.set()
                            pool.shutdown(wait=True)

    def test_tts_speaks_question_and_final_results_for_both_input_modes(self):
        self.asr.transcribe.return_value = "मुझे योजना चाहिए"
        with patch("backend.server.speak_hindi", return_value="/audio/mock.mp3") as speak:
            first = self.speech().json()
            self.assertIsNotNone(first["next_question"])
            speak.assert_called_once_with(first["ai_text"])
        self.extractor.extract_profile.return_value = ProfileExtractionResult(age=25, gender="Male", annual_income=100000)
        from backend.services.user_response import SPOKEN_SCHEME_LIMIT
        for mode in ("text", "voice"):
            with self.subTest(mode=mode), patch("backend.server.speak_hindi", return_value="/audio/mock.mp3") as speak:
                response = (self.client.post("/conversation", json={"text": "मेरी उम्र 25 है", "session_id": "narration"})
                            if mode == "text" else self.speech())
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIsNone(data["next_question"])
                spoken = speak.call_args.args[0]
                matched = [card for card in data["result_cards"] if card["status"] == "ELIGIBLE"]
                self.assertIn(f"{len(matched)} योजनाओं", spoken)
                for card in matched[:SPOKEN_SCHEME_LIMIT]:
                    self.assertIn(card["scheme_name"], spoken)
                    self.assertIn(card["reasons"][0], spoken)
                self.assertNotEqual(spoken, data["ai_text"])
                self.assertEqual(data["audio_url"], "/audio/mock.mp3")

    def test_final_results_survive_gtts_failure_without_logging_payload(self):
        self.extractor.extract_profile.return_value = ProfileExtractionResult(age=25, gender="Male", annual_income=100000)
        with patch("backend.server.gTTS", side_effect=RuntimeError("private-provider-payload")), self.assertLogs("backend.server", level="WARNING") as logs:
            response = self.client.post("/conversation", json={"text": "मेरी उम्र 25 है", "session_id": "tts-failure"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsNone(data["audio_url"])
        self.assertTrue(data["schemes"] and data["result_cards"] and data["eligibility_results"] and data["ai_text"])
        self.assertNotIn("private-provider-payload", " ".join(logs.output))
        self.assertNotIn("मेरी उम्र", " ".join(logs.output))
        self.assertIn("tts_unavailable error_type=RuntimeError", " ".join(logs.output))

    def test_asr_errors_clean_upload_and_do_not_process_conversation(self):
        paths = []
        for error in [ASRUnavailableError("ASR unavailable"), AudioDecodeError("Invalid audio")]:
            def fail(path):
                self.assertTrue(Path(path).exists())
                paths.append(path)
                raise error
            self.asr.transcribe.side_effect = fail
            with patch("backend.server.process_turn") as process, patch("backend.server.speak_hindi") as speak:
                response = self.speech()
            self.assertEqual(response.status_code, error.status_code)
            process.assert_not_called()
            speak.assert_not_called()
        self.assertTrue(all(not Path(path).exists() for path in paths))

    def test_english_transcript_reaches_structured_extraction(self):
        self.extractor.extract_profile.return_value = ProfileExtractionResult(
            age=65, gender="Male", annual_income=100000)
        self.asr.transcribe.return_value = "My age is 65 male annual income 100000"
        with patch("backend.server.speak_hindi", return_value=None):
            self.speech()
            response = self.speech()
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["user_text"], self.asr.transcribe.return_value)
        self.assertEqual((result["session"]["age"], result["session"]["gender"], result["session"]["income"]),
                         (65, "Male", 100000))
        self.assertTrue(result["eligibility_results"])
        self.assertEqual(self.extractor.extract_profile.call_args.args[0],
                         self.asr.transcribe.return_value)

    def test_extraction_failure_keeps_speech_response_contract(self):
        self.asr.transcribe.return_value = "मेरी उम्र पच्चीस है"
        self.extractor.extract_profile.side_effect = ProfileExtractionError("provider_unavailable")
        with patch("backend.server.speak_hindi", return_value="/audio/retry.mp3"):
            response = self.speech()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("दोबारा", body["ai_text"])
        self.assertEqual(body["schemes"], [])
        self.assertEqual(body["session"]["attempts"], 0)
        self.assertEqual(body["audio_url"], "/audio/retry.mp3")

    def test_three_turn_speech_slot_filling_and_correction(self):
        self.asr.transcribe.side_effect = ["meri age twenty five hai", "main male hoon",
                                          "meri annual income one lakh rupees hai",
                                          "income one lakh nahi two lakh hai"]
        self.extractor.extract_profile.side_effect = [
            ProfileExtractionResult(age=25, gender=None, annual_income=None),
            ProfileExtractionResult(age=None, gender="Male", annual_income=None),
            ProfileExtractionResult(age=None, gender=None, annual_income=100000),
            ProfileExtractionResult(age=None, gender=None, annual_income=200000),
        ]
        with patch("backend.server.speak_hindi", return_value=None):
            first, second, third, fourth = [self.speech().json() for _ in range(4)]
        self.assertEqual(first["missing_fields"], ["annual_income"])
        self.assertEqual(second["missing_fields"], ["annual_income"])
        self.assertTrue(third["session"]["finalized"])
        self.assertEqual(fourth["session"]["income"], 200000)
        self.assertEqual(fourth["session"]["age"], 25)
        expected = self.client.post("/api/eligibility", json={"attributes": {
            "age": 25, "gender": "Male", "annual_income": 200000}}).json()
        self.assertEqual(fourth["eligibility_results"], expected)

    def test_required_file_form_field_unchanged(self):
        self.assertEqual(self.client.post("/speech-to-text", data={"session_id": "api-test"}).status_code, 422)
        self.asr.transcribe.assert_not_called()

    def test_voice_and_text_share_profile_and_question_context(self):
        self.extractor.extract_profile.side_effect = [
            ProfileExtractionResult(age=24, gender=None, annual_income=None, state_or_ut="Uttar Pradesh", student_status=True),
            ProfileExtractionResult(age=None, gender=None, annual_income=200000),
            ProfileExtractionResult(age=26, gender=None, annual_income=None),
        ]
        self.asr.transcribe.return_value = "2 lakh"
        with patch("backend.server.speak_hindi", return_value=None):
            first = self.client.post("/conversation", json={"session_id": "mixed-input",
                "text": "मैं 24 साल का छात्र हूँ और उत्तर प्रदेश से हूँ"})
            self.assertEqual(first.status_code, 200)
            self.asr.transcribe.assert_not_called()
            second = self.client.post("/speech-to-text", files={"file": ("voice.webm", b"audio", "audio/webm")},
                data={"session_id": "mixed-input", "context": '{"answer_field":"annual_income"}'})
            self.assertEqual(second.status_code, 200)
            self.assertEqual(self.extractor.extract_profile.call_args.kwargs["question"], "आपकी अपनी सालाना आय कितनी है?")
            third = self.client.post("/conversation", json={"session_id": "mixed-input", "text": "नहीं, मेरी उम्र 26 है"})
        profile = third.json()["session"]
        self.assertEqual(profile["age"], 26)
        self.assertEqual(profile["state_or_ut"], "Uttar Pradesh")
        self.assertTrue(profile["student_status"])
        self.assertEqual(profile["income"], 200000)

    def test_text_validation_and_skip_without_extraction(self):
        for payload in [{"text": " "}, {"text": "x" * 4001}, {"text": "hi", "session_id": "../bad"},
                        {"text": "hi", "answer_field": "invented"}, {"text": "hi", "skipped_fields": ["invented"]},
                        {"skip": True}, {"skip": "true"}, {"text": "hi", "skip": True, "answer_field": "age"}]:
            self.assertEqual(self.client.post("/conversation", json=payload).status_code, 422)
        self.extractor.extract_profile.assert_not_called()
        with patch("backend.server.speak_hindi", return_value=None):
            response = self.client.post("/conversation", json={"session_id": "skip", "skip": True,
                "answer_field": "age", "skipped_fields": ["age"]})
        self.assertEqual(response.status_code, 200)
        self.extractor.extract_profile.assert_not_called()
        self.assertIsNone(response.json()["session"]["age"])
        self.assertNotIn("age", response.json()["missing_fields"])

    def test_invalid_voice_context_rejected_before_asr(self):
        response = self.client.post("/speech-to-text", files={"file": ("voice.webm", b"audio", "audio/webm")},
                                    data={"context": '{"answer_field":"invented"}'})
        self.assertEqual(response.status_code, 422)
        self.asr.transcribe.assert_not_called()

    def test_normal_page_is_hindi_first_with_both_inputs(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        for text in ['lang="hi"', 'id="text-form"', 'id="start"', 'id="skip"', 'user.css', "बोलकर बताएँ", "लिखकर बताएँ"]:
            self.assertIn(text, response.text)
        self.assertEqual(self.client.get("/static/user.css").status_code, 200)

    def test_session_storage_failure_is_safe_503(self):
        self.asr.transcribe.return_value = "age 25"
        with patch.object(self.store, "get", side_effect=SessionStoreError()):
            response = self.speech()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("redis://", response.text)
        self.extractor.extract_profile.assert_not_called()

    def test_session_ids_are_isolated_through_speech_api(self):
        self.asr.transcribe.return_value = "test"
        self.extractor.extract_profile.side_effect = [
            ProfileExtractionResult(age=25, gender="Male", annual_income=None),
            ProfileExtractionResult(age=60, gender="Female", annual_income=None),
            ProfileExtractionResult(age=None, gender=None, annual_income=100000),
        ]
        with patch("backend.server.speak_hindi", return_value=None):
            a, b, c = [self.client.post("/speech-to-text", files={"file": ("voice.webm", b"mock audio")},
                                       data={"session_id": sid}).json()
                       for sid in ["abc", "xyz", "abc"]]
        self.assertEqual((c["session"]["age"], c["session"]["gender"], c["session"]["income"]),
                         (25, "Male", 100000))
        self.assertEqual((b["session"]["age"], b["session"]["gender"]), (60, "Female"))
        self.assertIsNone(self.store.get("xyz").profile.annual_income)


class ConfigurationTests(unittest.TestCase):
    def test_postgresql_url_required_and_credentials_not_exposed(self):
        for value in ["", "invalid", "sqlite:///runtime.db", "mysql://user:secret@localhost/db",
                      "postgresql://USER:secret@localhost:invalid/db"]:
            with self.subTest(value=value), patch.dict(os.environ, {"DATABASE_URL": value}):
                with self.assertRaises(ConfigurationError) as caught:
                    database_url()
                self.assertNotIn("secret", str(caught.exception))

    def test_postgresql_url_selects_psycopg_driver(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://USER:PASSWORD@localhost/example"}):
            self.assertEqual(database_url().drivername, "postgresql+psycopg")

    def test_startup_without_database_fails_clearly(self):
        with patch.dict(os.environ, {"DATABASE_URL": ""}):
            with self.assertRaisesRegex(RuntimeError, "Database unavailable"):
                with TestClient(app):
                    self.fail("Startup should not succeed")

    def test_startup_detects_missing_rule_table_in_empty_database(self):
        engine, factory = test_database()
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            EligibilityRule.__table__.drop(connection)
        app.state.session_factory = factory
        try:
            with self.assertRaisesRegex(RuntimeError, "Database unavailable"):
                with TestClient(app):
                    self.fail("Startup should detect incomplete migrations")
        finally:
            del app.state.session_factory

    def test_startup_connection_failure_hides_credentials(self):
        factory = Mock()
        factory.return_value.__enter__ = Mock(side_effect=OperationalError("SQL", {}, RuntimeError("secret password")))
        factory.return_value.__exit__ = Mock()
        factory.kw = {"bind": Mock()}
        with patch("backend.server.create_session_factory", return_value=factory):
            with self.assertRaises(RuntimeError) as caught:
                with TestClient(app):
                    self.fail("Startup should fail")
        self.assertNotIn("secret", str(caught.exception))
        factory.kw["bind"].dispose.assert_called_once()
