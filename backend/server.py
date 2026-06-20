from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import whisper
import tempfile
import os
import shutil
import uuid
from gtts import gTTS

from backend.logic import (
    extract_fields,
    get_session_state,
    find_eligible_schemes,
    apply_defaults_if_needed,
    reset_session,
)

# -------------------------------------------------
# APP INIT
# -------------------------------------------------
app = FastAPI(title="Hindi Government Voice Agent")

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")
AUDIO_DIR = os.path.join(BASE_DIR, "audio")

os.makedirs(AUDIO_DIR, exist_ok=True)


def configure_ffmpeg():
    if os.environ.get("FFMPEG_BINARY"):
        return

    try:
        import imageio_ffmpeg
    except ImportError:
        return

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    shim_dir = os.path.join(tempfile.gettempdir(), "bhashasetu-ffmpeg")
    shim_path = os.path.join(shim_dir, "ffmpeg.exe")
    os.makedirs(shim_dir, exist_ok=True)

    if not os.path.exists(shim_path):
        shutil.copyfile(ffmpeg_path, shim_path)

    os.environ["FFMPEG_BINARY"] = shim_path
    os.environ["PATH"] = shim_dir + os.pathsep + os.environ.get("PATH", "")


configure_ffmpeg()

model = None


def get_whisper_model():
    global model
    if model is None:
        model = whisper.load_model("base")
    return model

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
async def speech_to_text(
    file: UploadFile = File(...),
    session_id: str = Form("default"),
):

    # ---------- Save audio ----------
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    # ---------- Transcribe ----------
    try:
        result = get_whisper_model().transcribe(tmp_path, language="hi")
        user_text = result["text"].strip()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    # ---------- MEMORY UPDATE ----------
    if get_session_state(session_id)["finalized"]:
        reset_session(session_id)

    extract_fields(user_text, session_id)
    state = get_session_state(session_id)

    # ---------- DECISION ----------
    if state["attempts"] == 1:
        # ASK ONLY ONCE
        ai_text = "कृपया अपनी उम्र, लिंग और वार्षिक आय बताएं।"

    else:
        # FINAL RESPONSE
        apply_defaults_if_needed(session_id)
        schemes = find_eligible_schemes(session_id)
        state = get_session_state(session_id)

        if schemes:
            ai_text = (
                "आपके द्वारा दी गई जानकारी के आधार पर "
                f"आप {len(schemes)} सरकारी योजनाओं के लिए पात्र पाए गए हैं। "
                "पूरी सूची नीचे तालिका में दी गई है।"
            )
        else:
            ai_text = "दिए गए विवरण के आधार पर कोई उपयुक्त सरकारी योजना नहीं मिली।"

    try:
        audio_url = speak_hindi(ai_text)
    except Exception:
        audio_url = None

    return JSONResponse({
        "user_text": user_text,
        "ai_text": ai_text,
        "audio_url": audio_url,
        "schemes": schemes if state["finalized"] else [],
        "session": state
    })
