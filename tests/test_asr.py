import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

import numpy as np

from backend.config import ASRSettings, ConfigurationError, INDICCONFORMER_MODEL
from backend.services.asr_service import (
    ASRService, ASRUnavailableError, AudioDecodeError, SAMPLE_RATE,
    _load_indicconformer, _load_whisper, ffmpeg_binary, get_asr_service, normalize_audio,
)


class ASRConfigurationTests(unittest.TestCase):
    def test_defaults_are_hindi_ctc_indicconformer(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = ASRSettings.from_environment()
        self.assertEqual((settings.provider, settings.model_id, settings.language, settings.decoder),
                         ("indicconformer", INDICCONFORMER_MODEL, "hi", "ctc"))
        self.assertFalse(settings.log_transcripts)

    def test_explicit_whisper_selects_base_unless_model_is_supplied(self):
        with patch.dict(os.environ, {"ASR_PROVIDER": "whisper"}, clear=True):
            self.assertEqual(ASRSettings.from_environment().model_id, "base")

    def test_invalid_settings_fail_clearly(self):
        for key, value in [("ASR_PROVIDER", "unknown"), ("ASR_LANGUAGE", "auto"),
                           ("ASR_DECODER", "rnnt"), ("ASR_MODEL_ID", " "), ("ASR_LOG_TRANSCRIPTS", "maybe")]:
            with self.subTest(key=key), patch.dict(os.environ, {key: value}, clear=True):
                with self.assertRaises(ConfigurationError):
                    ASRSettings.from_environment()

    def test_provider_singleton_does_not_load_model(self):
        get_asr_service.cache_clear()
        self.addCleanup(get_asr_service.cache_clear)
        with patch.dict(os.environ, {}, clear=True), patch("backend.services.asr_service.load_recognizer") as loader:
            self.assertIs(get_asr_service(), get_asr_service())
            loader.assert_not_called()


class ASRServiceTests(unittest.TestCase):
    def setUp(self):
        self.waveform = np.zeros(1600, dtype=np.float32)
        self.normalizer = Mock(return_value=self.waveform)
        self.recognizer = Mock(return_value="  मेरी उम्र पैंसठ साल है।  ")
        self.loader = Mock(return_value=self.recognizer)
        self.service = ASRService(ASRSettings(), loader=self.loader, normalizer=self.normalizer)

    def test_lazy_loading_and_reuse(self):
        self.loader.assert_not_called()
        self.assertEqual(self.service.transcribe("a.webm"), "मेरी उम्र पैंसठ साल है।")
        self.assertEqual(self.service.transcribe("b.wav"), "मेरी उम्र पैंसठ साल है।")
        self.loader.assert_called_once_with(self.service.settings)
        self.normalizer.assert_any_call("a.webm")
        self.normalizer.assert_any_call("b.wav")
        self.assertIs(self.recognizer.call_args.args[0], self.waveform)
        self.assertIsNotNone(self.service.initialization_seconds)

    def test_explicit_warmup_is_reused(self):
        self.service.load_model()
        self.service.load_model()
        self.service.transcribe("a.webm")
        self.loader.assert_called_once()

    def test_concurrent_calls_load_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(self.service.transcribe, ["a.webm"] * 8))
        self.assertEqual(len(results), 8)
        self.loader.assert_called_once()

    def test_model_failure_does_not_fallback_and_hides_upstream_secrets(self):
        self.loader.side_effect = RuntimeError("hf_secret_token should never be logged")
        with self.assertLogs("backend.services.asr_service", level="ERROR") as logs:
            with self.assertRaises(ASRUnavailableError) as caught:
                self.service.transcribe("a.webm")
        self.assertNotIn("hf_secret", str(caught.exception) + str(logs.output))
        self.recognizer.assert_not_called()
        with patch("backend.services.asr_service._load_indicconformer", side_effect=RuntimeError), patch("backend.services.asr_service._load_whisper") as fallback:
            with self.assertRaises(ASRUnavailableError):
                ASRService(ASRSettings(), normalizer=self.normalizer).load_model()
        fallback.assert_not_called()

    def test_failed_initialization_can_be_retried(self):
        self.loader.side_effect = [RuntimeError("temporary outage"), self.recognizer]
        with self.assertRaises(ASRUnavailableError):
            self.service.transcribe("a.webm")
        self.assertTrue(self.service.transcribe("a.webm"))
        self.assertEqual(self.loader.call_count, 2)

    def test_wrapped_gated_model_error_has_actionable_safe_message(self):
        from huggingface_hub.errors import GatedRepoError
        error = OSError("wrapper with sensitive URL")
        error.__cause__ = GatedRepoError("secret token in upstream response")
        self.loader.side_effect = error
        with self.assertLogs("backend.services.asr_service", level="ERROR") as logs:
            with self.assertRaisesRegex(ASRUnavailableError, "Accept the model conditions") as caught:
                self.service.load_model()
        self.assertIn("model_access_denied", str(logs.output))
        self.assertNotIn("secret", str(caught.exception) + str(logs.output))

    def test_decode_failure_does_not_load_model(self):
        self.normalizer.side_effect = AudioDecodeError("Invalid audio")
        with self.assertRaises(AudioDecodeError):
            self.service.transcribe("broken.webm")
        self.loader.assert_not_called()

    def test_inference_error_is_explicit(self):
        self.recognizer.side_effect = RuntimeError("upstream error")
        with self.assertRaises(ASRUnavailableError):
            self.service.transcribe("a.webm")

    def test_empty_and_non_string_output_rejected(self):
        for output, expected in [("  ", AudioDecodeError), ({"text": "unexpected"}, ASRUnavailableError)]:
            with self.subTest(output=output):
                self.recognizer.return_value = output
                with self.assertRaises(expected):
                    self.service.transcribe("a.webm")

    def test_transcripts_are_not_logged_by_default(self):
        with self.assertLogs("backend.services.asr_service", level="INFO") as logs:
            self.service.transcribe("a.webm")
        self.assertNotIn("पैंसठ", str(logs.output))
        self.assertIn("asr_duration_ms", str(logs.output))

    def test_transcript_logging_is_explicitly_opt_in(self):
        service = ASRService(replace(ASRSettings(), log_transcripts=True), loader=self.loader, normalizer=self.normalizer)
        with self.assertLogs("backend.services.asr_service", level="INFO") as logs:
            service.transcribe("a.webm")
        self.assertIn("पैंसठ", str(logs.output))

    def test_official_auto_model_api_and_tensor_shape(self):
        # Real tensor library, mocked loader: never download model assets in tests.
        model = Mock(return_value="मेरी उम्र पैंसठ साल है।")
        with patch("transformers.AutoModel.from_pretrained", return_value=model) as load:
            recognize = _load_indicconformer(ASRSettings())
        self.assertEqual(recognize(self.waveform), "मेरी उम्र पैंसठ साल है।")
        load.assert_called_once_with(INDICCONFORMER_MODEL, trust_remote_code=True)
        tensor, language, decoder = model.call_args.args
        self.assertEqual(tuple(tensor.shape), (1, 1600))
        self.assertEqual(str(tensor.dtype), "torch.float32")
        self.assertEqual((language, decoder), ("hi", "ctc"))

    def test_whisper_rollback_forces_hindi_on_cpu(self):
        model = Mock()
        model.transcribe.return_value = {"text": "नमस्ते"}
        with patch("torch.cuda.is_available", return_value=False), patch("whisper.load_model", return_value=model) as load:
            recognize = _load_whisper(ASRSettings(provider="whisper", model_id="base"))
        self.assertEqual(recognize(self.waveform), "नमस्ते")
        load.assert_called_once_with("base", device="cpu")
        self.assertEqual(model.transcribe.call_args.kwargs,
                         {"language": "hi", "task": "transcribe", "fp16": False, "temperature": 0})


class AudioNormalizationTests(unittest.TestCase):
    def test_ffmpeg_arguments_and_float32_output(self):
        samples = np.array([0, 0.5, -0.5], dtype="<f4")
        with patch("backend.services.asr_service.ffmpeg_binary", return_value="ffmpeg"), patch(
            "backend.services.asr_service.subprocess.run", return_value=Mock(stdout=samples.tobytes())
        ) as run:
            result = normalize_audio("browser-recording.webm")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("-ac") + 1], "1")
        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[-1], "pipe:1")
        self.assertNotIn("shell", run.call_args.kwargs)
        np.testing.assert_array_equal(result, samples)

    def test_decode_errors_and_invalid_output(self):
        errors = [subprocess.CalledProcessError(1, "ffmpeg"), subprocess.TimeoutExpired("ffmpeg", 60)]
        for error in errors:
            with self.subTest(error=type(error).__name__), patch("backend.services.asr_service.subprocess.run", side_effect=error):
                with self.assertRaises(AudioDecodeError):
                    normalize_audio("broken.webm")
        for output in [b"", b"x", np.array([np.nan], dtype="<f4").tobytes()]:
            with self.subTest(output=output), patch("backend.services.asr_service.subprocess.run", return_value=Mock(stdout=output)):
                with self.assertRaises(AudioDecodeError):
                    normalize_audio("broken.webm")

    def test_configured_ffmpeg_is_respected_or_fails_clearly(self):
        with patch.dict(os.environ, {"FFMPEG_BINARY": "chosen-ffmpeg"}), patch("shutil.which", return_value="chosen-ffmpeg"):
            self.assertEqual(ffmpeg_binary(), "chosen-ffmpeg")
        with patch.dict(os.environ, {"FFMPEG_BINARY": "missing"}), patch("shutil.which", return_value=None):
            with self.assertRaises(ASRUnavailableError):
                ffmpeg_binary()

    def test_real_ffmpeg_downmixes_resamples_and_decodes_webm_by_content(self):
        # Synthetic signal only: no personal recordings and no ASR model access.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "stereo.wav"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(2)
                output.setsampwidth(2)
                output.setframerate(48000)
                samples = np.column_stack([np.full(4800, 12000), np.full(4800, -12000)]).astype("<i2")
                output.writeframes(samples.tobytes())
            waveform = normalize_audio(source)
            self.assertEqual(len(waveform), SAMPLE_RATE // 10)
            self.assertLess(float(np.max(np.abs(waveform))), 0.0001)
            # Match MediaRecorder WebM/Opus, then intentionally use a misleading suffix.
            browser_file = Path(directory) / "browser.wav"
            subprocess.run([ffmpeg_binary(), "-nostdin", "-loglevel", "error", "-i", str(source),
                            "-c:a", "libopus", "-f", "webm", str(browser_file)], check=True, capture_output=True)
            decoded = normalize_audio(browser_file)
            self.assertEqual(decoded.dtype, np.float32)
            self.assertGreater(len(decoded), 0)
            self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ["browser.wav", "stereo.wav"])


class UploadCleanupTests(unittest.TestCase):
    def test_upload_read_failure_also_cleans_temp_file(self):
        from backend.server import speech_to_text

        upload = Mock(filename="voice.webm")
        upload.read = AsyncMock(side_effect=OSError("upload interrupted"))
        upload.close = AsyncMock()
        paths = []
        original = tempfile.NamedTemporaryFile

        def tracked_file(**kwargs):
            result = original(**kwargs)
            paths.append(result.name)
            return result

        with patch("backend.server.tempfile.NamedTemporaryFile", side_effect=tracked_file):
            with self.assertRaises(OSError):
                asyncio.run(speech_to_text(upload, "cleanup-test", Mock(), Mock()))
        upload.close.assert_awaited_once()
        self.assertTrue(paths)
        self.assertTrue(all(not Path(path).exists() for path in paths))
