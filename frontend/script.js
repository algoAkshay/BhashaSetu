let mediaRecorder;
let chunks = [];
let recordedBlob = null;

// UI elements
const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const submitBtn = document.getElementById("submit");

const userAudio = document.getElementById("userAudio");
const aiAudio = document.getElementById("aiAudio");

const userText = document.getElementById("userText");
const aiText = document.getElementById("aiText");

// --------------------
// 🎙️ START RECORDING
// --------------------
startBtn.onclick = async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    chunks = [];
    recordedBlob = null;

    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);

    mediaRecorder.start();

    startBtn.disabled = true;
    stopBtn.disabled = false;

    userText.innerText = "";
    aiText.innerText = "";
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
    recordedBlob = new Blob(chunks, { type: "audio/wav" });
    userAudio.src = URL.createObjectURL(recordedBlob);
    userAudio.load();
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
  formData.append("file", recordedBlob, "voice.wav");

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

    // 🤖 Show assistant text
    aiText.innerText = "Assistant: " + data.ai_text;

    // 🔊 PLAY ASSISTANT AUDIO (IMPORTANT PART)
    if (data.audio_url) {
      aiAudio.src = data.audio_url + "?t=" + Date.now(); // cache-bust
      aiAudio.style.display = "block";
      aiAudio.load();
      await aiAudio.play();   // 🔥 THIS WAS MISSING EARLIER
    }

  } catch (err) {
    alert("Could not connect to server");
    console.error(err);
  }
};
