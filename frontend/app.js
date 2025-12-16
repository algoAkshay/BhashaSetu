let mediaRecorder;
let recordedChunks = [];
let recordedBlob = null;

const startBtn = document.getElementById("start");
const stopBtn = document.getElementById("stop");
const submitBtn = document.getElementById("submit");

startBtn.onclick = startRecording;
stopBtn.onclick = stopRecording;
submitBtn.onclick = submitRecording;

function startRecording() {
  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = e => recordedChunks.push(e.data);
      mediaRecorder.onstop = () => {
        recordedBlob = new Blob(recordedChunks, { type: "audio/wav" });
        document.getElementById("userAudio").src =
          URL.createObjectURL(recordedBlob);
      };
      mediaRecorder.start();
      console.log("Recording started");
    })
    .catch(err => alert("Mic access error: " + err));
}

function stopRecording() {
  if (mediaRecorder) {
    mediaRecorder.stop();
    console.log("Recording stopped");
  }
}

async function submitRecording() {
  if (!recordedBlob) {
    alert("Please record first");
    return;
  }

  const formData = new FormData();
  formData.append("file", recordedBlob, "voice.wav");

  const res = await fetch("/speech-to-text", {
    method: "POST",
    body: formData
  });

  const data = await res.json();

  document.getElementById("userText").innerText =
    "User said: " + data.user_text;

  document.getElementById("aiText").innerText =
    "Assistant: " + data.ai_text;

  document.getElementById("aiAudio").src = data.audio_url;
}
