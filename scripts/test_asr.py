"""Explicit real-model smoke test; never downloads a model during pytest collection."""
import argparse
import json
import logging
from pathlib import Path
import sys
from time import perf_counter

# Allow `python scripts/test_asr.py ...` from the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import ConfigurationError
from backend.services.asr_service import ASRError, ASRService


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, nargs="+", help="Existing WAV, WebM, MP3 or other FFmpeg-decodable recordings")
    args = parser.parse_args()
    if any(not path.is_file() for path in args.audio):
        parser.error("Every supplied audio path must be an existing file.")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    started = perf_counter()
    try:
        service = ASRService()
        print(json.dumps({"provider": service.settings.provider, "model": service.settings.model_id,
                          "configured_language": service.settings.language, "decoder": service.settings.decoder}), flush=True)
        service.load_model()
        print(json.dumps({"model_initialization_seconds": round(service.initialization_seconds, 3)}), flush=True)
        for path in args.audio:
            inference_started = perf_counter()
            transcript = service.transcribe(path)
            print(json.dumps({"file": path.name, "configured_language": service.settings.language,
                              "transcript": transcript,
                              "transcription_seconds_including_decode": round(perf_counter() - inference_started, 3)},
                             ensure_ascii=False), flush=True)
    except (ASRError, ConfigurationError) as error:
        print(json.dumps({"error": str(error), "elapsed_seconds": round(perf_counter() - started, 3)}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
