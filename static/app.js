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

let audioQueue = [];
let isPlaying = false;
let playbackCtx = null;
let nextPlayTime = 0;
let totalBytesReceived = 0;
let audioCtxInitialized = false;

function getInviteToken() {
  return new URLSearchParams(window.location.search).get('invite') || '';
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

function addDebug(text) {
  const div = document.createElement('div');
  div.style.cssText = 'color:#555;font-size:0.75rem;margin:2px 0';
  div.textContent = text;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function animateWaveform(active, speaking) {
  waveformBars.forEach((bar, i) => {
    if (active) {
      bar.style.height = (8 + Math.random() * 28) + 'px';
      bar.className = 'bar ' + (speaking ? 'speaking' : 'active');
    } else {
      bar.style.height = ([8,16,24,16,8,20,12][i] || 8) + 'px';
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

async function initAudioContexts() {
  if (audioCtxInitialized) return;
  audioCtxInitialized = true;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE });
  playbackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SAMPLE_RATE });
  addDebug(`AudioCtx: input=${audioCtx.sampleRate}Hz state=${audioCtx.state} | output=${playbackCtx.sampleRate}Hz state=${playbackCtx.state}`);
}

async function ensurePlaybackCtx() {
  if (!playbackCtx) {
    playbackCtx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: TARGET_SAMPLE_RATE });
  }
  if (playbackCtx.state === 'suspended') {
    await playbackCtx.resume();
    addDebug(`Playback ctx resumed, state=${playbackCtx.state}`);
  }
  return playbackCtx;
}

async function playAudioChunk(pcmData) {
  audioQueue.push(pcmData);
  if (!isPlaying) processAudioQueue();
}

async function processAudioQueue() {
  if (audioQueue.length === 0) {
    isPlaying = false;
    return;
  }
  isPlaying = true;
  const chunk = audioQueue.shift();

  try {
    const ctx = await ensurePlaybackCtx();
    const int16 = new Int16Array(chunk.buffer, chunk.byteOffset, chunk.byteLength / 2);
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
    const startTime = Math.max(now + 0.01, nextPlayTime);
    source.start(startTime);
    nextPlayTime = startTime + buffer.duration;
    source.onended = () => processAudioQueue();
  } catch (e) {
    console.error('Playback error:', e);
    addDebug('Playback error: ' + e.message);
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
  if (!invite) { showError(); return; }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${proto}//${location.host}/ws?invite=${encodeURIComponent(invite)}`;

  setStatus('Connecting...', 'connecting');
  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    reconnectAttempts = 0;
    setStatus('Ready — press and hold 🎤 to speak', '');
    micBtn.disabled = false;
    addDebug('WebSocket connected');
  };

  ws.onmessage = async (event) => {
    if (event.data instanceof Blob) {
      const buf = await event.data.arrayBuffer();
      totalBytesReceived += buf.byteLength;
      addDebug(`Audio received: ${buf.byteLength}b (total ${totalBytesReceived}b)`);
      setStatus('Speaking...', 'speaking');
      startWave(true);
      // Ensure playback context exists even without prior user gesture (may fail on Safari)
      await playAudioChunk(new Uint8Array(buf));
    } else {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'error' && msg.code === 4001) { showError(); ws.close(); return; }
        if (msg.type === 'transcript') {
          addTranscript(msg.role, msg.text);
        }
        if (msg.type === 'turn_complete') {
          setStatus('Ready — press 🎤 to speak', '');
          stopWave();
          micBtn.disabled = false;
        }
        if (msg.type === 'error') {
          setStatus('Error — refresh page', 'error');
          addDebug('Server error: ' + JSON.stringify(msg));
          stopWave();
        }
        addDebug('JSON: ' + JSON.stringify(msg));
      } catch {}
    }
  };

  ws.onerror = (e) => {
    setStatus('Connection error', 'error');
    addDebug('WS error: ' + e.type);
    stopWave();
  };

  ws.onclose = (event) => {
    micBtn.disabled = true;
    stopRecording();
    stopWave();
    clearAudioQueue();
    addDebug(`WS closed: code=${event.code} reason=${event.reason}`);

    if (event.code === 4001) { showError(); return; }

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
  await initAudioContexts();
  if (audioCtx.state === 'suspended') await audioCtx.resume();

  isRecording = true;
  micBtn.classList.add('active');
  setStatus('Listening...', 'listening');
  startWave(false);
  clearAudioQueue();
  nextPlayTime = 0;

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true },
      video: false
    });
    addDebug('Mic acquired');
    const source = audioCtx.createMediaStreamSource(mediaStream);
    scriptProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
    source.connect(scriptProcessor);
    scriptProcessor.connect(audioCtx.destination);

    let framesSent = 0;
    scriptProcessor.onaudioprocess = (e) => {
      if (!isRecording || !ws || ws.readyState !== WebSocket.OPEN) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        int16[i] = Math.max(-32768, Math.min(32767, Math.round(float32[i] * 32767)));
      }
      ws.send(int16.buffer);
      framesSent++;
      if (framesSent === 1 || framesSent % 20 === 0) {
        addDebug(`Mic frame ${framesSent}: ${int16.buffer.byteLength}b, rate=${audioCtx.sampleRate}Hz`);
      }
    };
  } catch (err) {
    addDebug('Mic error: ' + err.message);
    setStatus('Microphone access denied', 'error');
    isRecording = false;
    micBtn.classList.remove('active');
  }
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  micBtn.classList.remove('active');
  if (scriptProcessor) { scriptProcessor.disconnect(); scriptProcessor = null; }
  if (mediaStream) { mediaStream.getTracks().forEach(t => t.stop()); mediaStream = null; }
  addDebug('Mic stopped');
}

micBtn.addEventListener('mousedown', async () => { await initAudioContexts(); await startRecording(); });
micBtn.addEventListener('mouseup', () => stopRecording());
micBtn.addEventListener('touchstart', async (e) => { e.preventDefault(); await initAudioContexts(); await startRecording(); }, { passive: false });
micBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

connect();
