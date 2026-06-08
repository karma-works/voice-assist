'use strict';

// Silero VAD v5 wrapper, fed manually from the existing capture pipeline.
// Runs onnxruntime-web on the main thread (single-threaded SIMD wasm, so no
// SharedArrayBuffer / COOP-COEP headers required). The model consumes 512-sample
// windows of 16 kHz mono float32 audio and returns a speech probability in [0,1].
//
// Exposes a global `SileroVAD`. No build step — load after ort.min.js.

const SILERO_WINDOW = 512;        // samples per inference (32 ms at 16 kHz)
const SILERO_STATE_LEN = 2 * 1 * 128;

class SileroVAD {
  constructor() {
    this.session = null;
    this.state = new Float32Array(SILERO_STATE_LEN);
    this.sr = null;
    this.ready = false;
  }

  // Loads ort wasm + the model. Resolves to true on success, false on failure
  // (caller falls back to RMS). Never throws.
  async init({ wasmPath = '/vendor/', modelUrl = '/vendor/silero_vad.onnx' } = {}) {
    try {
      if (typeof ort === 'undefined') throw new Error('onnxruntime-web (ort) not loaded');
      ort.env.wasm.wasmPaths = wasmPath;
      ort.env.wasm.numThreads = 1;   // avoids SharedArrayBuffer requirement
      ort.env.wasm.simd = true;      // only ort-wasm-simd.wasm is vendored
      ort.env.logLevel = 'error';

      this.session = await ort.InferenceSession.create(modelUrl, {
        executionProviders: ['wasm'],
        graphOptimizationLevel: 'all',
      });
      this.sr = new ort.Tensor('int64', BigInt64Array.from([16000n]), []);  // scalar
      this.reset();
      this.ready = true;
      return true;
    } catch (e) {
      this.ready = false;
      this._error = e;
      return false;
    }
  }

  reset() {
    this.state.fill(0);
  }

  // window: Float32Array of exactly SILERO_WINDOW samples. Returns speech prob.
  async process(window) {
    const input = new ort.Tensor('float32', window, [1, window.length]);
    const stateTensor = new ort.Tensor('float32', this.state, [2, 1, 128]);
    const out = await this.session.run({ input, state: stateTensor, sr: this.sr });
    // v5 outputs: `output` (prob) and `stateN` (recurrent state to carry forward)
    this.state = out.stateN.data;
    return out.output.data[0];
  }
}

SileroVAD.WINDOW = SILERO_WINDOW;
window.SileroVAD = SileroVAD;
