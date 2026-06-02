'use strict';

// Gemini Live expects 16kHz PCM16 mono input, outputs 24kHz PCM16 mono
const INPUT_SAMPLE_RATE = 16000;
const OUTPUT_SAMPLE_RATE = 24000;

const statusEl = document.getElementById('status');
const micBtn = document.getElementById('mic-btn');
const transcriptEl = document.getElementById('transcript');
const mainCard = document.getElementById('main-card');
const errorPage = document.getElementById('error-page');
const waveformBars = document.querySelectorAll('.bar');

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECTS = 5;

// Audio capture (always-on)
let captureCtx = null;
let workletNode = null;
let micStream = null;
let capturing = false;

// Audio playback
let playCtx = null;
let nextPlayTime = 0;
let audioQueue = [];
let isPlaying = false;
let activeSources = new Set();
let assistantAudioActive = false;
let assistantAudioStartedAt = 0;
let lastInterruptAt = 0;

// Debug
let totalBytesReceived = 0;
let framesSent = 0;
let interruptCount = 0;

// Interruption detection
const INTERRUPT_RMS_THRESHOLD = 0.025;
const INTERRUPT_MIN_SPEECH_FRAMES = 2;
const INTERRUPT_GRACE_MS = 450;
const INTERRUPT_COOLDOWN_MS = 900;
let speechFramesWhileAssistant = 0;

// ─── UI helpers ────────────────────────────────────────────────────────────

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

function dbg(text) {
  const div = document.createElement('div');
  div.style.cssText = 'color:#555;font-size:0.75rem;margin:2px 0';
  div.textContent = text;
  transcriptEl.appendChild(div);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  console.log('[VA]', text);
}

let waveInterval = null;
function animateWave(active, speaking) {
  waveformBars.forEach((bar, i) => {
    if (active) {
      bar.style.height = (8 + Math.random() * 28) + 'px';
      bar.className = 'bar ' + (speaking ? 'speaking' : 'active');
    } else {
      const h = [8,16,24,16,8,20,12][i] || 8;
      bar.style.height = h + 'px';
      bar.className = 'bar';
    }
  });
}
function startWave(speaking) {
  if (waveInterval) clearInterval(waveInterval);
  waveInterval = setInterval(() => animateWave(true, speaking), 120);
}
function stopWave() {
  if (waveInterval) { clearInterval(waveInterval); waveInterval = null; }
  animateWave(false, false);
}

// ─── Audio playback ─────────────────────────────────────────────────────────

async function ensurePlayCtx() {
  if (!playCtx) {
    playCtx = new AudioContext({ sampleRate: OUTPUT_SAMPLE_RATE });
  }
  if (playCtx.state === 'suspended') await playCtx.resume();
  return playCtx;
}

async function enqueueAudio(uint8) {
  assistantAudioActive = true;
  if (!assistantAudioStartedAt) assistantAudioStartedAt = performance.now();
  audioQueue.push(uint8);
  if (!isPlaying) drainAudioQueue();
}

async function drainAudioQueue() {
  if (!audioQueue.length) { isPlaying = false; return; }
  isPlaying = true;
  const chunk = audioQueue.shift();
  try {
    const ctx = await ensurePlayCtx();
    // chunk is Uint8Array of raw PCM16 LE mono at OUTPUT_SAMPLE_RATE
    const i16 = new Int16Array(chunk.buffer, chunk.byteOffset, chunk.byteLength >>> 1);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768;

    const buf = ctx.createBuffer(1, f32.length, OUTPUT_SAMPLE_RATE);
    buf.copyToChannel(f32, 0);

    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    activeSources.add(src);

    const now = ctx.currentTime;
    const start = Math.max(now + 0.005, nextPlayTime);
    nextPlayTime = start + buf.duration;
    src.start(start);
    src.onended = () => {
      activeSources.delete(src);
      drainAudioQueue();
    };
  } catch(e) {
    dbg('Playback err: ' + e.message);
    isPlaying = false;
    drainAudioQueue();
  }
}

function clearAudio() {
  audioQueue = [];
  for (const src of activeSources) {
    try { src.stop(); } catch {}
  }
  activeSources.clear();
  isPlaying = false;
  nextPlayTime = 0;
  assistantAudioActive = false;
  assistantAudioStartedAt = 0;
  speechFramesWhileAssistant = 0;
}

function sendJson(msg) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(msg));
  return true;
}

function handleDetectedInterruption(rms) {
  const now = performance.now();
  if (!assistantAudioActive) return;
  if (now - assistantAudioStartedAt < INTERRUPT_GRACE_MS) return;
  if (now - lastInterruptAt < INTERRUPT_COOLDOWN_MS) return;

  lastInterruptAt = now;
  interruptCount++;
  clearAudio();
  stopWave();
  setStatus('Listening — go ahead', 'listening');
  sendJson({ type: 'interrupt', at: Date.now(), rms });
  dbg(`Interrupt ${interruptCount}: rms=${rms.toFixed(4)}`);
}

function handleMicLevel(rms) {
  if (!assistantAudioActive) {
    speechFramesWhileAssistant = 0;
    return;
  }

  if (rms >= INTERRUPT_RMS_THRESHOLD) {
    speechFramesWhileAssistant++;
  } else {
    speechFramesWhileAssistant = Math.max(0, speechFramesWhileAssistant - 1);
  }

  if (speechFramesWhileAssistant >= INTERRUPT_MIN_SPEECH_FRAMES) {
    handleDetectedInterruption(rms);
  }
}

// ─── Audio capture (AudioWorklet, always-on) ─────────────────────────────

// Inline AudioWorklet processor — captures 128-sample frames, downsamples
// from device rate to 16kHz, accumulates into ~100ms chunks and posts to main.
const WORKLET_CODE = `
class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._ratio = sampleRate / 16000;
    this._acc = 0;
  }
  process(inputs) {
    const ch = inputs[0]?.[0];
    if (!ch) return true;
    for (let i = 0; i < ch.length; i++) {
      this._acc += 1;
      if (this._acc >= this._ratio) {
        this._acc -= this._ratio;
        this._buf.push(Math.max(-1, Math.min(1, ch[i])));
      }
    }
    // Post every ~1600 samples = 100ms at 16kHz
    if (this._buf.length >= 1600) {
      const out = new Int16Array(this._buf.length);
      let sumSquares = 0;
      for (let i = 0; i < this._buf.length; i++) {
        const sample = this._buf[i];
        out[i] = sample * 32767 | 0;
        sumSquares += sample * sample;
      }
      const rms = Math.sqrt(sumSquares / this._buf.length);
      this.port.postMessage({ type: 'audio', buffer: out.buffer, rms }, [out.buffer]);
      this._buf = [];
    }
    return true;
  }
}
registerProcessor('capture-processor', CaptureProcessor);
`;

async function startCapture() {
  if (capturing) return;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 }, video: false });
    dbg('Mic granted, device rate=' + (micStream.getAudioTracks()[0]?.getSettings().sampleRate || '?') + 'Hz');

    captureCtx = new AudioContext();  // use device's native rate
    dbg('AudioContext rate=' + captureCtx.sampleRate + 'Hz');

    const blob = new Blob([WORKLET_CODE], { type: 'application/javascript' });
    const url = URL.createObjectURL(blob);
    await captureCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    const source = captureCtx.createMediaStreamSource(micStream);
    workletNode = new AudioWorkletNode(captureCtx, 'capture-processor');
    workletNode.port.onmessage = (e) => {
      if (!e.data || e.data.type !== 'audio') return;
      handleMicLevel(e.data.rms || 0);
      if (!ws || ws.readyState !== WebSocket.OPEN) return;
      ws.send(e.data.buffer);  // ArrayBuffer of Int16 PCM at 16kHz
      framesSent++;
      if (framesSent <= 3 || framesSent % 30 === 0)
        dbg(`Mic frame ${framesSent}: ${e.data.buffer.byteLength}b rms=${(e.data.rms || 0).toFixed(4)}`);
    };
    source.connect(workletNode);
    // Don't connect workletNode to destination — no mic playback
    workletNode.connect(captureCtx.createGain()); // keep graph active

    capturing = true;
    dbg('Capture started');
    setStatus('Listening — just speak', 'listening');
    startWave(false);
  } catch(e) {
    dbg('Mic error: ' + e.message);
    setStatus('Microphone error: ' + e.message, 'error');
  }
}

function stopCapture() {
  if (!capturing) return;
  capturing = false;
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (captureCtx) { captureCtx.close(); captureCtx = null; }
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  dbg('Capture stopped');
}

// ─── WebSocket ──────────────────────────────────────────────────────────────

function connect() {
  const invite = getInviteToken();
  if (!invite) { showError(); return; }

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/ws?invite=${encodeURIComponent(invite)}`;

  setStatus('Connecting…', 'connecting');
  ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';  // get ArrayBuffer directly, skip Blob→ArrayBuffer

  ws.onopen = async () => {
    reconnectAttempts = 0;
    dbg('WebSocket connected');
    // Start mic immediately on open — user must interact first for AudioContext
    setStatus('Tap 🎤 to start listening', '');
    micBtn.disabled = false;
  };

  ws.onmessage = async (event) => {
    if (event.data instanceof ArrayBuffer) {
      totalBytesReceived += event.data.byteLength;
      if (totalBytesReceived <= event.data.byteLength || totalBytesReceived % (OUTPUT_SAMPLE_RATE * 2) < event.data.byteLength)
        dbg(`Audio recv: ${event.data.byteLength}b (total ${totalBytesReceived}b)`);
      setStatus('Speaking…', 'speaking');
      startWave(true);
      await enqueueAudio(new Uint8Array(event.data));
    } else {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'error' && msg.code === 4001) { showError(); ws.close(); return; }
        if (msg.type === 'transcript') addTranscript(msg.role, msg.text);
        if (msg.type === 'interrupted') {
          clearAudio();
          setStatus('Listening — go ahead', 'listening');
        }
        if (msg.type === 'turn_complete') {
          assistantAudioActive = false;
          assistantAudioStartedAt = 0;
          speechFramesWhileAssistant = 0;
          setStatus('Listening — just speak', 'listening');
          stopWave();
        }
        if (msg.type === 'error') { setStatus('Error — refresh', 'error'); dbg('Server: ' + JSON.stringify(msg)); }
        dbg('JSON: ' + JSON.stringify(msg));
      } catch {}
    }
  };

  ws.onerror = () => {
    dbg('WS error');
    setStatus('Connection error', 'error');
  };

  ws.onclose = (e) => {
    micBtn.disabled = true;
    stopWave();
    clearAudio();
    dbg(`WS closed code=${e.code}`);
    if (e.code === 4001) { showError(); return; }
    if (reconnectAttempts < MAX_RECONNECTS) {
      reconnectAttempts++;
      setStatus(`Reconnecting (${reconnectAttempts}/${MAX_RECONNECTS})…`, 'connecting');
      setTimeout(connect, reconnectAttempts * 1500);
    } else {
      setStatus('Disconnected — refresh page', 'error');
    }
  };
}

// ─── Mic button: tap to start, tap again to stop ────────────────────────────

let micActive = false;

async function toggleMic() {
  if (!micActive) {
    micActive = true;
    micBtn.textContent = '🔴';
    micBtn.title = 'Tap to stop';
    // Resume AudioContext created by tap
    if (playCtx && playCtx.state === 'suspended') await playCtx.resume();
    await startCapture();
  } else {
    micActive = false;
    micBtn.textContent = '🎤';
    micBtn.title = 'Tap to start listening';
    stopCapture();
    setStatus('Paused — tap 🎤 to listen again', '');
    stopWave();
  }
}

micBtn.addEventListener('click', toggleMic);

connect();
