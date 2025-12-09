from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import whisper
import tempfile
import os
import uuid
from gtts import gTTS

from backend.logic import (
    extract_fields,
    get_session_state,
    find_eligible_schemes,
    apply_defaults_if_needed
)

# -------------------------------------------------
# APP INIT
# -------------------------------------------------
app = FastAPI(title="Hindi Government Voice Agent")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")

os.makedirs(AUDIO_DIR, exist_ok=True)

# -------------------------------------------------
# LOAD WHISPER
# -------------------------------------------------
model = whisper.load_model("base")

# -------------------------------------------------
# STATIC FILES
# -------------------------------------------------
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/audio", StaticFiles(directory=AUDIO_DIR), name="audio")


@app.get("/")
def serve_ui():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


# -------------------------------------------------
# TEXT → SPEECH
# -------------------------------------------------
def speak_hindi(text: str) -> str:
    filename = f"{uuid.uuid4()}.mp3"
    path = os.path.join(AUDIO_DIR, filename)
    gTTS(text=text, lang="hi").save(path)
    return f"/audio/{filename}"


# -------------------------------------------------
# MAIN ENDPOINT
# -------------------------------------------------
@app.post("/speech-to-text")
async def speech_to_text(file: UploadFile = File(...)):

    # ---------- Save audio ----------
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # ---------- Transcribe ----------
    result = model.transcribe(tmp_path, language="hi")
    user_text = result["text"].strip()
    os.remove(tmp_path)

    # ---------- MEMORY UPDATE ----------
    extract_fields(user_text)
    state = get_session_state()

    # ---------- DECISION ----------
    if state["attempts"] == 1:
        # ASK ONLY ONCE
        ai_text = "कृपया अपनी उम्र, लिंग और वार्षिक आय बताएं।"

    else:
        # FINAL RESPONSE
        apply_defaults_if_needed()
        schemes = find_eligible_schemes()

        if schemes:
            ai_text = (
                "आपके द्वारा दी गई जानकारी के आधार पर "
                "आप निम्न सरकारी योजनाओं के लिए पात्र पाए गए हैं: "
                + ", ".join(schemes)
                + "। अधिक जानकारी के लिए संबंधित विभाग की वेबसाइट देखें।"
            )
        else:
            ai_text = "दिए गए विवरण के आधार पर कोई उपयुक्त सरकारी योजना नहीं मिली।"

    audio_url = speak_hindi(ai_text)

    return JSONResponse({
        "user_text": user_text,
        "ai_text": ai_text,
        "audio_url": audio_url,
        "session": state
    })
