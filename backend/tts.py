from gtts import gTTS
import uuid
import os

AUDIO_DIR = "audio"
os.makedirs(AUDIO_DIR, exist_ok=True)


def speak(text: str):
    filename = f"{uuid.uuid4()}.mp3"
    path = os.path.join(AUDIO_DIR, filename)

    tts = gTTS(text=text, lang="hi")
    tts.save(path)

    return path
