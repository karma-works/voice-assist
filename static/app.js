'use strict';

const SAMPLE_RATE = 16000;
const TARGET_SAMPLE_RATE = 24000;

const statusEl = document.getElementById('status');
const micBtn = document.getElementById('mic-btn');
const transcriptEl = document.getElementById('transcript');
const mainCard = document.getElementById('main-card');
const errorPage = document.getElementById('error-page');
const waveformBars = document.querySelectorAll('.bar');

let ws = null;
let audioCtx = null;
let mediaStream = null;
let scriptProcessor = null;
let isRecording = false;
let reconnectAttempts = 0;
const MAX_RECONNECTS = 3;

// Audio playback queue
let audioQueue = [];
let isPlaying = false;
let playbackCtx = null;

function getInviteToken() {
  const params = new URLSearchParams(window.location.search);
  return params.get('invite') || '';
}

function setStatus(text, cls = '') {
  statusEl.textContent = text;
  statusEl.className = cls;
}

function showError() {
  mainCard.style.display = 'none';
  errorPage.style.display = 'block';
}

function addTranscript(role, text) {
  if (!text.trim()) return;
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  const label = document.createElement('span');
  label.className = 'msg-label';
  label.textContent = role === 'user' ? 'You:' : 'Assistant:';
  div.appendChild(label);
  div.appendChild(document.createTextNode(text));
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function animateWaveform(active, speaking) {
  waveformBars.forEach((bar, i) => {
    if (active) {
      const h = 8 + Math.random() * 28;
      bar.style.height = h + 'px';
      bar.className = 'bar ' + (speaking ? 'speaking' : 'active');
    } else {
      const heights = [8, 16, 24, 16, 8, 20, 12];
      bar.style.height = (heights[i] || 8) + 'px';
      bar.className = 'bar';
    }
  });
}

let waveInterval = null;
function startWave(speaking) {
  stopWave();
  waveInterval = setInterval(() => animateWaveform(true, speaking), 120);
}
function stopWave() {
  if (waveInterval) clearInterval(waveInterval);
  waveInterval = null;
  animateWaveform(false, false);
}

async function getAudioContext() {
  if (!audioCtx || audioCtx.state === 'closed') {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  }
  if (audioCtx.state === 'suspended') {
    await audioCtx.resume();
  }
  return audioCtx;
}

async function getPlaybackContext() {
  if (!playbackCtx || playbackCtx.state === 'closed') {
    playbackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SAMPLE_RATE });
  }
  if (playbackCtx.state === 'suspended') {
    await playbackCtx.resume();
  }
  return playbackCtx;
}

async function playAudioChunk(pcmData) {
  audioQueue.push(pcmData);
  if (!isPlaying) processAudioQueue();
}

let nextPlayTime = 0;
async function processAudioQueue() {
  if (audioQueue.length === 0) {
    isPlaying = false;
    return;
  }
  isPlaying = true;
  const chunk = audioQueue.shift();

  try {
    const ctx = await getPlaybackContext();
    const int16 = new Int16Array(chunk.buffer || chunk);
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768.0;
    }

    const buffer = ctx.createBuffer(1, float32.length, TARGET_SAMPLE_RATE);
    buffer.copyToChannel(float32, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(ctx.destination);

    const now = ctx.currentTime;
    const startTime = Math.max(now, nextPlayTime);
    source.start(startTime);
    nextPlayTime = startTime + buffer.duration;

    source.onended = () => processAudioQueue();
  } catch (e) {
    console.error('Audio playback error:', e);
    isPlaying = false;
    processAudioQueue();
  }
}

function clearAudioQueue() {
  audioQueue = [];
  isPlaying = false;
  nextPlayTime = 0;
}

function connect() {
  const invite = getInviteToken();
  if (!invite) {
    showError();
    return;
  }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/ws?invite=${encodeURIComponent(invite)}`;

  setStatus('Connecting...', 'connecting');
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    reconnectAttempts = 0;
    setStatus('Ready — press 🎤 to speak', '');
    micBtn.disabled = false;
  };

  ws.onmessage = async (event) => {
    if (event.data instanceof Blob) {
      const buf = await event.data.arrayBuffer();
      setStatus('Speaking...', 'speaking');
      startWave(true);
      await playAudioChunk(new Uint8Array(buf));
    } else {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'error' && msg.code === 4001) {
          showError();
          ws.close();
          return;
        }
        if (msg.type === 'transcript') {
          addTranscript(msg.role, msg.text);
        }
        if (msg.type === 'turn_complete') {
          setStatus('Ready — press 🎤 to speak', '');
          stopWave();
        }
        if (msg.type === 'error') {
          setStatus('Error — refresh page', 'error');
          stopWave();
        }
      } catch {}
    }
  };

  ws.onerror = () => {
    setStatus('Connection error', 'error');
    stopWave();
  };

  ws.onclose = (event) => {
    micBtn.disabled = true;
    stopRecording();
    stopWave();
    clearAudioQueue();

    if (event.code === 4001) {
      showError();
      return;
    }

    if (reconnectAttempts < MAX_RECONNECTS) {
      reconnectAttempts++;
      const delay = reconnectAttempts * 2000;
      setStatus(`Reconnecting (${reconnectAttempts}/${MAX_RECONNECTS})...`, 'connecting');
      setTimeout(connect, delay);
    } else {
      setStatus('Disconnected — refresh page', 'error');
    }
  };
}

async function startRecording() {
  if (isRecording) return;
  isRecording = true;
  micBtn.classList.add('active');
  setStatus('Listening...', 'listening');
  startWave(false);
  clearAudioQueue();

  try {
    const ctx = await getAudioContext();
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: { sampleRate: SAMPLE_RATE, channelCount: 1 }, video: false });
    const source = ctx.createMediaStreamSource(mediaStream);

    scriptProcessor = ctx.createScriptProcessor(4096, 1, 1);
    source.connect(scriptProcessor);
    scriptProcessor.connect(ctx.destination);

    scriptProcessor.onaudioprocess = (e) => {
      if (!isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        int16[i] = Math.max(-32768, Math.min(32767, Math.round(float32[i] * 32767)));
      }
      ws.send(int16.buffer);
    };
  } catch (err) {
    console.error('Mic error:', err);
    setStatus('Microphone access denied', 'error');
    isRecording = false;
    micBtn.classList.remove('active');
  }
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  micBtn.classList.remove('active');

  if (scriptProcessor) {
    scriptProcessor.disconnect();
    scriptProcessor = null;
  }
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop());
    mediaStream = null;
  }
}

// Push-to-talk
micBtn.addEventListener('mousedown', async () => {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    playbackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SAMPLE_RATE });
  }
  await startRecording();
});

micBtn.addEventListener('mouseup', () => stopRecording());
micBtn.addEventListener('touchstart', async (e) => {
  e.preventDefault();
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
    playbackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SAMPLE_RATE });
  }
  await startRecording();
}, { passive: false });
micBtn.addEventListener('touchend', (e) => {
  e.preventDefault();
  stopRecording();
});

connect();
