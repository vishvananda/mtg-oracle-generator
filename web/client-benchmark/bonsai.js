import { BONSAI } from './bonsai-config.js';

const $ = (id) => document.getElementById(id);
const seconds = (ms) => ms == null ? '—' : `${(ms / 1000).toFixed(2)} s`;
const storageKey = 'oracle-bonsai-benchmark-v1';
let worker, loaded = false, busy = false, timer, start, loadInfo, currentRun, imageUrl;
let runs = [];
try { runs = JSON.parse(localStorage.getItem(storageKey) || '[]').slice(-20); } catch { /* optional storage */ }
const progress = Object.fromEntries(Object.entries(BONSAI.component_bytes).map(([key, total]) => [key, { total, loaded: 0 }]));
const componentNames = { text_encoder: 'text encoder', transformer: 'image model', vae: 'image decoder' };
function status(text, error = false) {
  $('art-status').textContent = text; $('art-status').classList.toggle('error', error);
}
function controls() {
  $('art-load').disabled = busy || loaded;
  $('art-unload').disabled = busy || !loaded;
  $('art-generate').disabled = busy || !loaded;
  $('art-stop').disabled = !busy;
  for (const id of ['art-prompt', 'art-size', 'art-steps', 'art-random', 'art-use-card']) $(id).disabled = busy;
  $('art-seed').disabled = busy || $('art-random').checked;
  $('art-export').disabled = !runs.length;
}
function history() {
  $('art-history').replaceChildren();
  for (const run of [...runs].reverse()) {
    const tr = document.createElement('tr');
    for (const value of [new Date(run.at).toLocaleTimeString(), `${run.width} × ${run.height}`,
      `${run.steps} steps · seed ${run.seed}`, seconds(run.total_ms), run.finish]) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    $('art-history').append(tr);
  }
}
function finishRun(finish, error) {
  if (!currentRun) return;
  currentRun.finish = finish; currentRun.total_ms = performance.now() - start;
  if (error) currentRun.error = error;
  $('art-total-time').textContent = seconds(currentRun.total_ms);
  runs.push(currentRun); runs = runs.slice(-20); currentRun = null;
  try { localStorage.setItem(storageKey, JSON.stringify(runs)); } catch { /* export still available */ }
  history();
}
function resetWorker() {
  clearInterval(timer); worker?.terminate(); worker = null; loaded = false; busy = false;
  $('art-backend').textContent = 'Bonsai unloaded'; controls();
}
function fail(message) {
  finishRun('error', message); resetWorker(); status(`${message} Load Bonsai to retry.`, true);
}
function onMessage({ data }) {
  if (data.type === 'stage') {
    const stages = { tokenize: 'Preparing prompt…', 'text encode': 'Encoding image prompt…',
      scheduler: 'Preparing image…', 'vae decode': 'Decoding image…', 'png encode': 'Encoding PNG…',
      'to RGB': 'Finishing image…', 'unpack + BN-denorm': 'Preparing image decoder…' };
    const step = data.message.match(/^step (\d+)\/(\d+)/);
    if (step) status(`Denoising · step ${Number(step[1]) + 1} of ${step[2]}…`);
    else if (!data.message.startsWith('tokens:')) status(stages[data.message] || data.message);
  } else if (data.type === 'progress') {
    const p = data.progress;
    if (p.component && progress[p.component]) {
      const c = progress[p.component];
      if (Number.isFinite(p.total) && p.total > 0) c.total = p.total;
      if (Number.isFinite(p.loaded)) c.loaded = Math.min(c.total, Math.max(0, p.loaded));
      $('art-progress').value = Object.values(progress).reduce((n, c) => n + c.loaded, 0) /
        Object.values(progress).reduce((n, c) => n + c.total, 0);
      status(`${p.fromCache ? 'Reading cached' : 'Loading'} ${componentNames[p.component]}${p.total ? ` · ${(p.loaded / 1e9).toFixed(2)} / ${(p.total / 1e9).toFixed(2)} GB` : '…'}`);
    } else status('Loading Bonsai configuration…');
  } else if (data.type === 'ready') {
    clearInterval(timer); loaded = true; busy = false;
    loadInfo = { total_ms: performance.now() - start, pipeline_load_ms: data.pipeline_load_ms,
      device: data.caps, browser: navigator.userAgent, logical_cpus: navigator.hardwareConcurrency };
    $('art-load-time').textContent = seconds(loadInfo.total_ms);
    $('art-progress').value = 1;
    $('art-backend').textContent = `WebGPU · ${data.caps.adapter.description || data.caps.adapter.architecture || data.caps.adapter.vendor || 'adapter available'}`;
    $('art-device').textContent = JSON.stringify(loadInfo, null, 2);
    status('Bonsai ready. Enter an image prompt or use your card description.'); controls();
  } else if (data.type === 'step') {
    $('art-step-count').textContent = `${data.completed} / ${data.total}`;
    status(`Denoising · step ${data.completed} of ${data.total} complete…`);
    currentRun?.step_timings.push({ step: data.completed, elapsed_ms: performance.now() - start });
  } else if (data.type === 'image') {
    clearInterval(timer);
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = URL.createObjectURL(data.blob);
    $('art-image').src = imageUrl; $('art-image').alt = currentRun.prompt; $('art-image').hidden = false;
    $('art-placeholder').hidden = true;
    $('art-png').href = imageUrl; $('art-png').download = `bonsai-${currentRun.seed}.png`; $('art-png').hidden = false;
    currentRun.runtime_generation_ms = data.generation_ms; currentRun.png_bytes = data.blob.size;
    finishRun('complete'); busy = false; status('Image complete.'); controls();
  } else if (data.type === 'unloaded') {
    resetWorker(); status('Bonsai unloaded. Downloaded weights remain cached.');
  } else if (data.type === 'error' || data.type === 'device-lost') fail(data.message);
}
$('art-load').addEventListener('click', () => {
  if (busy || loaded) return;
  if (!navigator.gpu) { status('Bonsai requires WebGPU. Try a current Chrome or Edge browser.', true); return; }
  busy = true; start = performance.now(); controls(); status('Starting Bonsai…');
  for (const c of Object.values(progress)) c.loaded = 0;
  $('art-progress').hidden = false; $('art-progress').value = 0;
  timer = setInterval(() => { $('art-load-time').textContent = seconds(performance.now() - start); }, 100);
  try {
    worker = new Worker(new URL('./bonsai-worker.js', import.meta.url), { type: 'module' });
    worker.onmessage = onMessage;
    worker.onerror = (event) => { event.preventDefault(); fail(event.message || 'Bonsai worker failed.'); };
    worker.postMessage({ type: 'load' });
  } catch (error) { fail(error.message); }
});
$('art-generate').addEventListener('click', () => {
  if (busy || !loaded) return;
  const prompt = $('art-prompt').value.trim();
  if (!prompt) { status('Enter an image prompt.', true); return; }
  if (!$('art-random').checked && !$('art-seed').reportValidity()) return;
  const seed = $('art-random').checked ? crypto.getRandomValues(new Uint32Array(1))[0] >>> 1 : Number($('art-seed').value);
  $('art-seed').value = seed;
  const [width, height] = $('art-size').value.split('x').map(Number);
  const steps = Number($('art-steps').value);
  start = performance.now(); busy = true;
  currentRun = { at: new Date().toISOString(), model: BONSAI.model, model_revision: BONSAI.revision,
    runtime_sha256: BONSAI.runtime_sha256, prompt, width, height, steps, seed, load: loadInfo,
    guidance_scale: 1, step_timings: [], finish: 'running' };
  $('art-step-count').textContent = `0 / ${steps}`; $('art-total-time').textContent = '0.00 s';
  status('Encoding image prompt…'); controls();
  timer = setInterval(() => { $('art-total-time').textContent = seconds(performance.now() - start); }, 100);
  worker.postMessage({ type: 'generate', prompt, width, height, steps, seed });
});
$('art-stop').addEventListener('click', () => {
  finishRun('cancelled'); resetWorker(); status('Stopped and unloaded Bonsai. Load it again to continue; cached weights are retained.');
});
$('art-unload').addEventListener('click', () => {
  busy = true; controls(); status('Unloading Bonsai…'); worker.postMessage({ type: 'unload' });
});
$('art-random').addEventListener('change', controls);
$('art-use-card').addEventListener('click', () => {
  const description = $('description').value.trim();
  let card;
  try { card = JSON.parse($('output').textContent); } catch { /* description works before text generation */ }
  const subject = card?.name && card?.type_line ? `${card.name}, ${card.type_line}. ${description}` : description;
  const scene = (subject || 'A towering red Giant in a mountain pass').trim().replace(/[.!?]+$/, '');
  // Reference the illustration style while describing a standalone painting.
  $('art-prompt').value = `Highly detailed fantasy painting in the illustration style of Magic: The Gathering. ${scene}. Painterly realism, expressive brushwork, intricate material textures, dramatic light and shadow, atmospheric depth, and rich, carefully balanced colors. A strong focal subject and dynamic composition tell a clear visual story. Finished standalone illustration: a single cohesive scene fills the entire image edge to edge, with the surroundings extending naturally beyond the edges.`.slice(0, 4000);
});
$('art-export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({ benchmark: 'bonsai-browser-v1', exported_at: new Date().toISOString(), runs }, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = href; a.download = `bonsai-benchmark-${Date.now()}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
});
window.addEventListener('pagehide', (event) => { if (!event.persisted) worker?.terminate(); });
history(); controls();
