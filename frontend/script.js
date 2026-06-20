let mediaRecorder;
let chunks = [];
let recordedBlob = null;
let currentStream = null;

// UI elements
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const submitBtn = document.getElementById("submit");

const userAudio = document.getElementById("userAudio");
const aiAudio = document.getElementById("aiAudio");

const userText = document.getElementById("userText");
const aiText = document.getElementById("aiText");

function createSessionId() {
  if (window.crypto && crypto.randomUUID) {
    return crypto.randomUUID();
  }

  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

const sessionId = createSessionId();

function renderAssistantResponse(data) {
  aiText.replaceChildren();

  const response = document.createElement("p");
  response.className = "assistant-summary";
  response.innerText = "Assistant: " + data.ai_text;
  aiText.appendChild(response);

  if (!data.schemes || data.schemes.length === 0) {
    return;
  }

  const table = document.createElement("table");
  table.className = "scheme-table";

  const thead = document.createElement("thead");
  const headerRow = document.createElement("tr");
  ["क्रम", "योजना का नाम"].forEach((label) => {
    const th = document.createElement("th");
    th.innerText = label;
    headerRow.appendChild(th);
  });
  thead.appendChild(headerRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  data.schemes.forEach((scheme, index) => {
    const row = document.createElement("tr");

    const serial = document.createElement("td");
    serial.innerText = String(index + 1);
    row.appendChild(serial);

    const name = document.createElement("td");
    name.innerText = scheme;
    row.appendChild(name);

    tbody.appendChild(row);
  });

  table.appendChild(tbody);
  aiText.appendChild(table);
}

// --------------------
// 🎙️ START RECORDING
// --------------------
startBtn.onclick = async () => {
  try {
    currentStream = await navigator.mediaDevices.getUserMedia({ audio: true });

    chunks = [];
    recordedBlob = null;

    const mimeType = MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
      : "";
    mediaRecorder = new MediaRecorder(currentStream, mimeType ? { mimeType } : undefined);
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);

    mediaRecorder.start();

    startBtn.disabled = true;
    stopBtn.disabled = false;

    userText.innerText = "";
    aiText.replaceChildren();
    aiAudio.style.display = "none";

  } catch (err) {
    alert("Microphone access denied");
    console.error(err);
  }
};

// --------------------
// ⏹️ STOP RECORDING
// --------------------
stopBtn.onclick = () => {
  if (!mediaRecorder) return;

  mediaRecorder.stop();

  stopBtn.disabled = true;
  startBtn.disabled = false;

  mediaRecorder.onstop = () => {
    recordedBlob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
    userAudio.src = URL.createObjectURL(recordedBlob);
    userAudio.load();

    if (currentStream) {
      currentStream.getTracks().forEach((track) => track.stop());
      currentStream = null;
    }
  };
};

// --------------------
// 📤 SUBMIT TO BACKEND
// --------------------
submitBtn.onclick = async () => {
  if (!recordedBlob) {
    alert("Please record your voice first");
    return;
  }

  const formData = new FormData();
  const extension = recordedBlob.type.includes("webm") ? "webm" : "wav";
  formData.append("file", recordedBlob, `voice.${extension}`);
  formData.append("session_id", sessionId);

  try {
    const res = await fetch("/speech-to-text", {
      method: "POST",
      body: formData
    });

    if (!res.ok) {
      throw new Error("Server error");
    }

    const data = await res.json();

    // 👤 Show user text
    userText.innerText = "User: " + data.user_text;

    // 🤖 Show assistant text and eligible schemes
    renderAssistantResponse(data);

    // 🔊 PLAY ASSISTANT AUDIO (IMPORTANT PART)
    if (data.audio_url) {
      aiAudio.src = data.audio_url + "?t=" + Date.now(); // cache-bust
      aiAudio.style.display = "block";
      aiAudio.load();
      try {
        await aiAudio.play();
      } catch (err) {
        console.warn("Audio autoplay was blocked", err);
      }
    } else {
      aiAudio.removeAttribute("src");
      aiAudio.style.display = "none";
    }

  } catch (err) {
    alert("Could not connect to server");
    console.error(err);
  }
};
