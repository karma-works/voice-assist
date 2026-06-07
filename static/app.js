'use strict';

// Gemini Live expects 16kHz PCM16 mono input, outputs 24kHz PCM16 mono
const INPUT_SAMPLE_RATE = 16000;
const OUTPUT_SAMPLE_RATE = 24000;
const INPUT_CHUNK_SAMPLES = 480; // 30 ms at 16 kHz; Gemini Live recommends 20-40 ms chunks.

const statusEl = document.getElementById('status');
const micBtn = document.getElementById('mic-btn');
const transcriptEl = document.getElementById('transcript');
const mainCard = document.getElementById('main-card');
const errorPage = document.getElementById('error-page');
const waveformBars = document.querySelectorAll('.bar');
const voiceReadyEl = document.getElementById('voice-ready');
const calendarReadyEl = document.getElementById('calendar-ready');
const outageEl = document.getElementById('outage');

let ws = null;
let reconnectAttempts = 0;
const MAX_RECONNECTS = 5;
let traceSessionId = null;

// Audio capture (always-on)
let captureCtx = null;
let workletNode = null;
let micStream = null;
let capturing = false;
let keepAliveGain = null;

// Audio playback
let playCtx = null;
let nextPlayTime = 0;
let audioQueue = [];
let isPlaying = false;
let schedulingAudio = false;
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

function setReadinessRow(el, label, state) {
  el.className = 'readiness-item ' + (state.ready ? 'ready' : 'down');
  el.children[0].textContent = label;
  el.children[1].textContent = state.ready ? 'Ready' : 'Unavailable';
}

async function checkReadiness() {
  setStatus('Checking readiness...', 'connecting');
  try {
    const res = await fetch('/readiness', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    setReadinessRow(voiceReadyEl, 'Voice', data.voice || { ready: false });
    setReadinessRow(calendarReadyEl, 'Calendar', data.calendar || { ready: false });

    if (data.ready) {
      outageEl.style.display = 'none';
      return true;
    }

    const messages = [];
    if (!data.voice?.ready) messages.push('Voice service is temporarily unavailable. Please try again later.');
    if (!data.calendar?.ready) messages.push('Calendar connection is unavailable. Scheduling is temporarily offline.');
    outageEl.textContent = messages.join(' ');
    outageEl.style.display = 'block';
    setStatus('Scheduling unavailable', 'error');
    micBtn.disabled = true;
    return false;
  } catch (e) {
    setReadinessRow(voiceReadyEl, 'Voice', { ready: false });
    setReadinessRow(calendarReadyEl, 'Calendar', { ready: false });
    outageEl.textContent = 'Readiness check failed. Please try again later.';
    outageEl.style.display = 'block';
    setStatus('Scheduling unavailable', 'error');
    micBtn.disabled = true;
    dbg('Readiness error: ' + e.message);
    return false;
  }
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
    playCtx = new AudioContext();
  }
  if (playCtx.state === 'suspended') await playCtx.resume();
  return playCtx;
}

async function enqueueAudio(uint8) {
  assistantAudioActive = true;
  if (!assistantAudioStartedAt) assistantAudioStartedAt = performance.now();
  audioQueue.push(uint8);
  await scheduleAudioQueue();
}

async function scheduleAudioQueue() {
  if (schedulingAudio) return;
  schedulingAudio = true;
  try {
    const ctx = await ensurePlayCtx();
    if (nextPlayTime < ctx.currentTime) {
      if (nextPlayTime > 0 && ctx.currentTime - nextPlayTime > 0.04) {
        traceClient('playback_underrun', { gapMs: Math.round((ctx.currentTime - nextPlayTime) * 1000) });
      }
      nextPlayTime = ctx.currentTime + 0.02;
    }

    while (audioQueue.length) {
      const chunk = audioQueue.shift();
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

      const start = Math.max(ctx.currentTime + 0.005, nextPlayTime);
      nextPlayTime = start + buf.duration;
      isPlaying = true;
      src.start(start);
      src.onended = () => {
        activeSources.delete(src);
        if (!activeSources.size && !audioQueue.length) {
          isPlaying = false;
          nextPlayTime = 0;
        }
      };
    }
  } catch(e) {
    dbg('Playback err: ' + e.message);
    isPlaying = false;
  } finally {
    schedulingAudio = false;
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

function traceClient(event, metadata = {}) {
  sendJson({ type: 'trace', event, metadata });
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
  traceClient('interrupt_detected', { rms, interruptCount });
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
// from device rate to 16kHz, accumulates into 30ms chunks and posts to main.
const WORKLET_CODE = `
const TARGET_SAMPLE_RATE = ${INPUT_SAMPLE_RATE};
const CHUNK_SAMPLES = ${INPUT_CHUNK_SAMPLES};

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buf = [];
    this._ratio = sampleRate / TARGET_SAMPLE_RATE;
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
    // Post every 30ms. Larger buffers add avoidable first-audio latency.
    if (this._buf.length >= CHUNK_SAMPLES) {
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
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        channelCount: 1
      },
      video: false
    });
    dbg('Mic granted, device rate=' + (micStream.getAudioTracks()[0]?.getSettings().sampleRate || '?') + 'Hz');

    captureCtx = new AudioContext();  // use device's native rate
    dbg('AudioContext rate=' + captureCtx.sampleRate + 'Hz');
    traceClient('mic_started', {
      deviceSampleRate: micStream.getAudioTracks()[0]?.getSettings().sampleRate || null,
      audioContextSampleRate: captureCtx.sampleRate,
    });

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
      if (framesSent === 1) traceClient('first_mic_frame', { bytes: e.data.buffer.byteLength, rms: e.data.rms || 0 });
      if (framesSent % 100 === 0) traceClient('mic_frame_summary', { framesSent, rms: e.data.rms || 0 });
      if (framesSent <= 3 || framesSent % 30 === 0)
        dbg(`Mic frame ${framesSent}: ${e.data.buffer.byteLength}b rms=${(e.data.rms || 0).toFixed(4)}`);
    };
    source.connect(workletNode);
    // Keep the graph active without playing microphone audio.
    keepAliveGain = captureCtx.createGain();
    keepAliveGain.gain.value = 0;
    workletNode.connect(keepAliveGain).connect(captureCtx.destination);

    capturing = true;
    dbg('Capture started');
    setStatus('Listening — just speak', 'listening');
    startWave(false);
  } catch(e) {
    dbg('Mic error: ' + e.message);
    traceClient('mic_error', { message: e.message });
    setStatus('Microphone error: ' + e.message, 'error');
  }
}

function stopCapture() {
  if (!capturing) return;
  capturing = false;
  if (workletNode) { workletNode.disconnect(); workletNode = null; }
  if (keepAliveGain) { keepAliveGain.disconnect(); keepAliveGain = null; }
  if (captureCtx) { captureCtx.close(); captureCtx = null; }
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  dbg('Capture stopped');
  traceClient('mic_stopped', { framesSent });
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
        if (msg.type === 'trace_session') {
          traceSessionId = msg.session_id;
          dbg('Trace session: ' + traceSessionId);
        }
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

async function boot() {
  const invite = getInviteToken();
  if (!invite) { showError(); return; }
  const ready = await checkReadiness();
  if (ready) connect();
}

boot();
