'use strict';

const $ = (id) => document.getElementById(id);

function newSessionId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

let sessionId = newSessionId();

let recorder;
let stream;
let chunks = [];
let blob = null;
let objectURL;
let busy = false;
let recording = false;
let recordingError = false;

let question = null;
let skipped = [];

$('delete-session').onclick = async () => {
  if (busy || recording) return;
  busy = true;
  controls();
  try {
    const freshId = newSessionId();
    const response = await fetch(`/session/${encodeURIComponent(sessionId)}`, {method: 'DELETE'});
    if (!response.ok) throw new Error('Session deletion failed');
    sessionId = freshId;
    question = null;
    skipped = [];
    chunks = [];
    blob = null;
    recorder = null;
    recordingError = false;
    stopTracks();
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    for (const id of ['userAudio', 'aiAudio']) {
      $(id).pause();
      $(id).removeAttribute('src');
      $(id).load();
      $(id).hidden = true;
    }
    $('typed-message').value = '';
    $('userText').textContent = '';
    $('aiText').replaceChildren();
    $('question-context').textContent = '';
    $('reply').hidden = true;
    $('skip').hidden = true;
    renderResults([]);
    $('results').open = false;
    status('इस सत्र की जानकारी मिटा दी गई है। आप नई बातचीत शुरू कर सकते हैं।');
  } catch {
    status('जानकारी मिट नहीं पाई। दोबारा कोशिश करें।', true);
  } finally {
    busy = false;
    controls();
  }
};


function node(tag, text) {
  const el = document.createElement(tag);

  if (text !== undefined) {
    el.textContent = text;
  }

  return el;
}


function status(text, error = false) {
  $('input-status').textContent = text;
  $('input-status').classList.toggle('error', error);
}


function controls() {
  $('delete-session').disabled = busy || recording;
  $('start').disabled = busy || recording;
  $('stop').disabled = !recording || busy;
  $('submit').disabled = busy || recording || !blob;
  $('send-text').disabled = busy || recording;
  $('typed-message').disabled = busy || recording;
  $('skip').disabled = busy || recording;
  $('type-mode').disabled = busy || recording;
  $('voice-mode').disabled = busy || recording;
}


function mode(voice) {
  $('voice-input').hidden = !voice;
  $('text-form').hidden = voice;

  $('voice-mode').setAttribute(
    'aria-pressed',
    String(voice)
  );

  $('type-mode').setAttribute(
    'aria-pressed',
    String(!voice)
  );

  if (!voice) {
    $('typed-message').focus();
  }
}


$('voice-mode').onclick = () => mode(true);
$('type-mode').onclick = () => mode(false);


function stopTracks() {
  stream?.getTracks().forEach(
    (track) => track.stop()
  );

  stream = null;
}


function resultCard(result) {
  const card = node('article');

  card.className =
    'result-card ' + result.status;

  card.append(
    node(
      'h4',
      result.scheme_name
    )
  );

  const label = node(
    'p',
    result.label
  );

  label.className = 'result-label';

  card.append(label);

  const reasons = node('ul');

  (result.reasons || []).forEach(
    (reason) => {
      reasons.append(
        node(
          'li',
          reason
        )
      );
    }
  );

  card.append(reasons);

  if (
    result.manual_conditions &&
    result.manual_conditions.length
  ) {
    const details = node('details');

    details.append(
      node(
        'summary',
        'इस योजना की अतिरिक्त शर्तें'
      )
    );

    details.append(
      node(
        'p',
        'इन शर्तों की पुष्टि आवेदन के समय करनी होगी:'
      )
    );

    const list = node('ul');

    result.manual_conditions.forEach(
      (condition) => {
        list.append(
          node(
            'li',
            condition
          )
        );
      }
    );

    details.append(list);
    card.append(details);
  }

  return card;
}


function renderResults(cards) {
  $('result-list').replaceChildren();

  $('results').hidden =
    !cards.length;

  const candidateCount = cards.filter(
    (card) =>
      card.status !== 'NOT_ELIGIBLE'
  ).length;

  $('results-summary').textContent =
    `योजनाएँ और कारण देखें (${candidateCount} संभावित)`;

  const groups = [
    [
      'ELIGIBLE',
      'पात्र दिखाई देने वाली योजनाएँ'
    ],
    [
      'POTENTIALLY_ELIGIBLE',
      'संभावित योजनाएँ — अंतिम शर्तों की पुष्टि बाकी'
    ],
    [
      'NEED_MORE_INFORMATION',
      'थोड़ी और जानकारी चाहिए'
    ],
    [
      'NOT_ELIGIBLE',
      'अभी मेल नहीं खाने वाली योजनाएँ'
    ]
  ];

  for (const [key, title] of groups) {
    const items = cards.filter(
      (card) =>
        card.status === key
    );

    if (!items.length) {
      continue;
    }

    const group = node(
      key === 'NOT_ELIGIBLE'
        ? 'details'
        : 'section'
    );

    group.append(
      node(
        key === 'NOT_ELIGIBLE'
          ? 'summary'
          : 'h3',
        `${title} (${items.length})`
      )
    );

    const content = node('div');

    group.append(content);

    let shown = 0;

    const more = node(
      'button',
      'और योजनाएँ देखें'
    );

    more.type = 'button';

    function appendPage() {
      items
        .slice(
          shown,
          shown + 5
        )
        .forEach(
          (item) => {
            content.append(
              resultCard(item)
            );
          }
        );

      shown += 5;

      more.hidden =
        shown >= items.length;
    }

    more.onclick = appendPage;

    appendPage();

    group.append(more);

    $('result-list').append(group);
  }
}


function render(data) {
  question =
    data.next_question;

  skipped =
    data.skipped_fields || skipped;

  $('reply').hidden = false;

  $('userText').textContent =
    data.user_text ||
    'अभी नहीं बताना';

  const summary = node(
    'p',
    data.ai_text
  );

  summary.className =
    'assistant-summary';

  $('aiText').replaceChildren(
    summary
  );

  $('skip').hidden =
    !question;

  $('question-context').textContent =
    question?.sensitive
      ? (
          'यह बताना आपकी इच्छा पर है। ' +
          (
            question.scheme_names?.length
              ? (
                  'संबंधित योजनाएँ: ' +
                  question.scheme_names.join(' · ')
                )
              : ''
          )
        )
      : '';

  renderResults(
    data.result_cards || []
  );

  $('results').open =
    !question &&
    (data.result_cards || []).length > 0;

  $('aiAudio').pause();

  if (data.audio_url) {
    $('aiAudio').src =
      data.audio_url;

    $('aiAudio').hidden =
      false;

    $('aiAudio')
      .play()
      .catch(
        () => {
          /*
           * Controls remain available
           * when autoplay is blocked.
           */
        }
      );

  } else {
    $('aiAudio').removeAttribute(
      'src'
    );

    $('aiAudio').hidden =
      true;
  }
}


async function send(
  url,
  options
) {
  if (busy) {
    return false;
  }

  busy = true;

  controls();

  status(
    'आपकी बात समझ रहे हैं…'
  );

  $('aiAudio').pause();

  try {
    const response = await fetch(
      url,
      {
        ...options,
        method: 'POST'
      }
    );

    if (!response.ok) {
      throw new Error(
        `Request failed with status ${response.status}`
      );
    }

    const data =
      await response.json();

    render(data);

    if (data.retry) {
      status(
        'अपनी बात दोबारा भेजें या थोड़ा बदलकर बताएँ।',
        true
      );

      return false;
    }

    status(
      'आप चाहें तो और जानकारी जोड़ें या नीचे योजनाएँ देखें।'
    );

    return true;

  } catch (error) {
    console.error(
      'Request failed',
      error
    );

    status(
      'अभी जवाब नहीं मिल पाया। दोबारा भेजें या थोड़ी देर बाद कोशिश करें।',
      true
    );

    return false;

  } finally {
    busy = false;
    controls();
  }
}


function context() {
  return {
    answer_field:
      question?.field || null,

    skipped_fields:
      skipped
  };
}


$('text-form').onsubmit =
  async (event) => {
    event.preventDefault();

    const text =
      $('typed-message')
        .value
        .trim();

    if (!text) {
      return;
    }

    const success = await send(
      '/conversation',
      {
        headers: {
          'Content-Type':
            'application/json'
        },

        body: JSON.stringify({
          text,
          session_id:
            sessionId,
          ...context()
        })
      }
    );

    if (success) {
      $('typed-message').value =
        '';
    }
  };


$('skip').onclick =
  async () => {
    if (!question) {
      return;
    }

    const nextSkipped = [
      ...new Set(
        [
          ...skipped,
          question.field
        ]
      )
    ];

    await send(
      '/conversation',
      {
        headers: {
          'Content-Type':
            'application/json'
        },

        body: JSON.stringify({
          session_id:
            sessionId,

          skip: true,

          ...context(),

          skipped_fields:
            nextSkipped
        })
      }
    );
  };


$('start').onclick =
  async () => {
    if (
      !navigator.mediaDevices
        ?.getUserMedia ||
      !window.MediaRecorder
    ) {
      status(
        'इस ब्राउज़र में रिकॉर्डिंग उपलब्ध नहीं है। लिखकर बताएँ।',
        true
      );

      mode(false);

      return;
    }

    busy = true;

    controls();

    $('aiAudio').pause();

    try {
      stream =
        await navigator.mediaDevices
          .getUserMedia({
            audio: true
          });

      chunks = [];
      blob = null;
      recordingError = false;

      $('userAudio').hidden =
        true;

      const mime = [
        'audio/webm',
        'audio/mp4',
        'audio/ogg'
      ].find(
        (value) =>
          MediaRecorder.isTypeSupported(
            value
          )
      );

      recorder =
        new MediaRecorder(
          stream,
          mime
            ? {
                mimeType: mime
              }
            : undefined
        );

      recorder.ondataavailable =
        (event) => {
          if (event.data.size) {
            chunks.push(
              event.data
            );
          }
        };

      recorder.onstop =
        () => {
          blob = new Blob(
            chunks,
            {
              type:
                recorder.mimeType ||
                'audio/webm'
            }
          );

          stopTracks();

          recording = false;
          busy = false;

          if (
            recordingError ||
            !blob.size
          ) {
            blob = null;

            controls();

            status(
              'रिकॉर्डिंग नहीं हो पाई। दोबारा कोशिश करें या लिखें।',
              true
            );

            return;
          }

          if (objectURL) {
            URL.revokeObjectURL(
              objectURL
            );
          }

          objectURL =
            URL.createObjectURL(
              blob
            );

          $('userAudio').src =
            objectURL;

          $('userAudio').hidden =
            false;

          controls();

          status(
            'रिकॉर्डिंग सुन लें, फिर भेजें।'
          );
        };

      recorder.onerror =
        () => {
          recordingError =
            true;

          stopTracks();

          recording = false;
          busy = false;
          blob = null;

          controls();

          status(
            'रिकॉर्डिंग नहीं हो पाई। दोबारा कोशिश करें या लिखें।',
            true
          );
        };

      recorder.start();

      recording = true;

      status(
        'रिकॉर्डिंग चालू है… अपनी बात पूरी करके रोकें।'
      );

    } catch {
      stopTracks();

      status(
        'माइक की अनुमति नहीं मिली। अनुमति दें या लिखकर बताएँ।',
        true
      );

    } finally {
      busy = false;
      controls();
    }
  };


$('stop').onclick =
  () => {
    if (
      recorder?.state ===
      'recording'
    ) {
      busy = true;

      controls();

      recorder.stop();
    }
  };


$('submit').onclick =
  async () => {
    if (!blob) {
      return;
    }

    const form =
      new FormData();

    const extension =
      blob.type.includes('mp4')
        ? 'm4a'
        : blob.type.includes('ogg')
          ? 'ogg'
          : 'webm';

    form.append(
      'file',
      blob,
      `voice.${extension}`
    );

    form.append(
      'session_id',
      sessionId
    );

    form.append(
      'context',
      JSON.stringify(
        context()
      )
    );

    const success =
      await send(
        '/speech-to-text',
        {
          body: form
        }
      );

    if (success) {
      blob = null;

      controls();
    }
  };


window.addEventListener(
  'pagehide',
  () => {
    stopTracks();

    if (objectURL) {
      URL.revokeObjectURL(
        objectURL
      );
    }
  }
);
