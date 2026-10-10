import { BONSAI } from './bonsai-config.js';
import { loadBonsaiRuntime } from './bonsai-loader.js';
import { loadChunkedFetch } from './bonsai-chunks.js';

let pipeline, busy = false, unloading = false;
const send = (type, data = {}) => postMessage({ type, ...data });
self.onmessage = async ({ data }) => {
  if (busy) { send('error', { message: 'Bonsai is already busy.' }); return; }
  busy = true;
  try {
    if (data.type === 'load') {
      if (!navigator.gpu) throw new Error('Bonsai requires WebGPU. Try a current Chrome or Edge browser.');
      send('stage', { message: 'Preparing Bonsai WebGPU runtime…' });
      const { Flux2KleinPipeline } = await loadBonsaiRuntime();
      const start = performance.now();
      const modelRoot = new URL(BONSAI.model_path, import.meta.url).href;
      const chunkedFetch = await loadChunkedFetch(modelRoot);
      pipeline = await Flux2KleinPipeline.from_pretrained(modelRoot, {
        fetch: chunkedFetch,
        cacheName: 'tetrarchs-bonsai-ternary-2c24c81',
        onProgress: (progress) => send('progress', { progress }),
      });
      const caps = pipeline.runtime.caps();
      pipeline.runtime.host.device.lost.then((info) => {
        if (!unloading) send('device-lost', { message: info.message || 'The browser released the Bonsai GPU device.' });
      });
      send('ready', { caps, pipeline_load_ms: performance.now() - start });
    } else if (data.type === 'generate') {
      if (!pipeline) throw new Error('Load Bonsai before generating an image.');
      const { prompt, width, height, steps, seed } = data;
      if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000 ||
          ![width, height].every((n) => Number.isInteger(n) && n >= 256 && n <= 1024 && n % 16 === 0) ||
          ![1, 2, 4, 8].includes(steps) || !Number.isInteger(seed) || seed < 0 || seed > 2147483647) {
        throw new Error('Invalid image generation settings.');
      }
      const start = performance.now();
      const result = await pipeline.generate({ prompt, width, height, seed,
        num_inference_steps: steps, guidance_scale: 1,
        log: (message) => send('stage', { message }),
        callback_on_step_end: (_pipeline, step) => send('step', { completed: step + 1, total: steps }),
      });
      const blob = result.toBlob(); result.bytes = null;
      send('image', { blob, generation_ms: performance.now() - start });
    } else if (data.type === 'unload') {
      unloading = true; await pipeline?.destroy(); pipeline = null;
      send('unloaded'); self.close();
    }
  } catch (error) {
    unloading = true;
    try { await pipeline?.destroy(); } catch { /* device may already be lost */ }
    pipeline = null;
    send('error', { message: error.message || String(error) });
    self.close();
  } finally { busy = false; }
};
