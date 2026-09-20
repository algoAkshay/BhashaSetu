"""Local speech recognition. No extraction, session or eligibility logic."""
from collections.abc import Callable
from functools import lru_cache
import logging
import os
from pathlib import Path
import shutil
import subprocess
from threading import Lock
from time import perf_counter

import numpy as np

from backend.config import ASRSettings

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000


class ASRError(RuntimeError):
    """Messages are safe to return to clients (never include upstream error text)."""
    status_code = 503


class AudioDecodeError(ASRError):
    status_code = 422


class ASRUnavailableError(ASRError):
    pass


def ffmpeg_binary() -> str:
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        path = shutil.which(configured)
        if path:
            return path
        raise ASRUnavailableError("FFMPEG_BINARY does not identify an executable.")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError):
        raise ASRUnavailableError("FFmpeg is unavailable. Install imageio-ffmpeg or set FFMPEG_BINARY.") from None


def normalize_audio(audio_path: str | Path) -> np.ndarray:
    """Decode real container bytes to mono 16 kHz float32; no output recording on disk."""
    command = [
        ffmpeg_binary(), "-nostdin", "-hide_banner", "-loglevel", "error",
        "-protocol_whitelist", "file,pipe", "-i", str(Path(audio_path).resolve()),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1",
    ]
    try:
        result = subprocess.run(command, capture_output=True, check=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise AudioDecodeError("Audio decoding timed out. Please submit a shorter recording.") from None
    except subprocess.CalledProcessError:
        raise AudioDecodeError("Cannot decode the uploaded audio. Please record and try again.") from None
    except OSError:
        raise ASRUnavailableError("Unable to run FFmpeg. Check FFMPEG_BINARY or imageio-ffmpeg.") from None
    if not result.stdout or len(result.stdout) % 4:
        raise AudioDecodeError("The recording contains no decodable audio samples.")
    waveform = np.frombuffer(result.stdout, dtype="<f4").copy()
    if not np.isfinite(waveform).all():
        raise AudioDecodeError("The recording contains invalid audio samples.")
    return waveform


def _load_indicconformer(settings: ASRSettings) -> Callable[[np.ndarray], str]:
    import torch
    import onnxruntime
    from transformers import AutoModel

    # Official wrapper owns TorchScript/ONNX placement. No invented device_map or .to()
    # override: it selects CUDA only when torch.cuda.is_available(), otherwise CPU.
    model = AutoModel.from_pretrained(settings.model_id, trust_remote_code=True)
    model.eval()
    logger.info("asr_runtime torch_device=%s onnx_providers=%s",
                "cuda" if torch.cuda.is_available() else "cpu", onnxruntime.get_available_providers())

    def recognize(waveform: np.ndarray) -> str:
        with torch.inference_mode():
            wav = torch.from_numpy(waveform).unsqueeze(0)  # [1, samples], float32
            return model(wav, settings.language, settings.decoder)

    return recognize


def _load_whisper(settings: ASRSettings) -> Callable[[np.ndarray], str]:
    import torch
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = whisper.load_model(settings.model_id, device=device)

    def recognize(waveform: np.ndarray) -> str:
        result = model.transcribe(waveform, language=settings.language, task="transcribe",
                                  fp16=device == "cuda", temperature=0)
        return result["text"]

    return recognize


def load_recognizer(settings: ASRSettings) -> Callable[[np.ndarray], str]:
    if settings.provider == "indicconformer":
        return _load_indicconformer(settings)
    return _load_whisper(settings)


def _load_failure_reason(error: Exception) -> str:
    """Classify chained library failures without exposing exception messages."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if type(error).__name__ == "GatedRepoError":
            return "model_access_denied"
        if isinstance(error, ImportError):
            return "missing_dependency"
        if type(error).__name__ in {"ConnectionError", "ProxyError", "ConnectTimeout", "ReadTimeout", "SSLError"}:
            return "network_error"
        error = error.__cause__ or error.__context__
    return "model_initialization_failed"


class ASRService:
    def __init__(self, settings: ASRSettings | None = None, *, loader=None, normalizer=None):
        self.settings = settings or ASRSettings.from_environment()
        self._loader = loader or load_recognizer
        self._normalizer = normalizer or normalize_audio
        self._recognize = None
        # Serialize first load and inference on this single shared local model.
        self._lock = Lock()
        self.initialization_seconds: float | None = None

    def _ensure_loaded(self):
        if self._recognize is not None:
            return
        started = perf_counter()
        logger.info("asr_model_loading provider=%s", self.settings.provider)
        try:
            self._recognize = self._loader(self.settings)
        except Exception as error:
            # HF exceptions can contain tokens/URLs; record only class and a safe reason.
            reason = _load_failure_reason(error)
            logger.error("asr_failure stage=load provider=%s reason=%s error_type=%s",
                         self.settings.provider, reason, type(error).__name__)
            if reason == "model_access_denied":
                raise ASRUnavailableError(
                    "Hugging Face denied model access. Accept the model conditions for your account "
                    "and authenticate using hf auth login or HF_TOKEN. No fallback provider was used."
                ) from None
            raise ASRUnavailableError(
                "ASR model could not be loaded. Check installed dependencies, model access, "
                "and Hugging Face login/HF_TOKEN. No fallback provider was used."
            ) from None
        self.initialization_seconds = perf_counter() - started
        logger.info("asr_model_loaded provider=%s initialization_ms=%.1f",
                    self.settings.provider, self.initialization_seconds * 1000)

    def load_model(self) -> None:
        """Explicit warm-up for the manual smoke test; normal startup stays lazy."""
        with self._lock:
            self._ensure_loaded()

    def transcribe(self, audio_path: str | Path) -> str:
        started = perf_counter()
        try:
            waveform = self._normalizer(audio_path)
            with self._lock:
                self._ensure_loaded()
                transcript = self._recognize(waveform)
            if not isinstance(transcript, str):
                raise ASRUnavailableError("ASR returned an unsupported response instead of a transcript.")
            transcript = transcript.strip()
            if not transcript:
                raise AudioDecodeError("No speech was recognized. Please record and try again.")
        except ASRError as error:
            logger.error("asr_failure provider=%s error_type=%s asr_duration_ms=%.1f",
                         self.settings.provider, type(error).__name__, (perf_counter() - started) * 1000)
            raise
        except Exception as error:
            logger.error("asr_failure stage=inference provider=%s error_type=%s asr_duration_ms=%.1f",
                         self.settings.provider, type(error).__name__, (perf_counter() - started) * 1000)
            raise ASRUnavailableError("Speech recognition failed. Please retry or check the ASR installation.") from None
        logger.info("asr_transcription_complete provider=%s language=%s asr_duration_ms=%.1f",
                    self.settings.provider, self.settings.language, (perf_counter() - started) * 1000)
        if self.settings.log_transcripts:
            logger.info("asr_transcript text=%r", transcript)
        return transcript


@lru_cache(maxsize=1)
def get_asr_service() -> ASRService:
    """One service/model per server process. FastAPI can override this dependency."""
    return ASRService()
