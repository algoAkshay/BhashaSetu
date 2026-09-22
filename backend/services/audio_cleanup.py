"""Expire only completed, generated UUID MP3 files in the audio directory."""
import logging
import os
import re
import stat
import time

logger = logging.getLogger(__name__)
GENERATED_MP3 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\.mp3")


def cleanup_audio(directory, ttl_seconds, *, now=None):
    if ttl_seconds <= 0:
        raise ValueError("Audio TTL must be positive")
    cutoff = (time.time() if now is None else now) - ttl_seconds
    deleted = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if not GENERATED_MP3.fullmatch(entry.name):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) and info.st_mtime < cutoff:
                        os.unlink(entry.path)
                        deleted += 1
                except FileNotFoundError:
                    pass  # A late-TTS callback or another worker removed it.
                except OSError:
                    logger.warning("audio_cleanup_failed")
    except OSError:
        logger.warning("audio_cleanup_unavailable")
    return deleted
