import { Wllama } from './vendor/wllama/index.js';

const $ = (id) => document.getElementById(id);
const seconds = (ms) => ms == null ? '—' : `${(ms / 1000).toFixed(2)} s`;
const base = new URL('./', location.href);
const url = (path) => new URL(path, base).href;
const storageKey = 'oracle-browser-benchmark-v1';
let engine, manifest, systemPrompt, busy = false, loaded = false, controller;
let loadInfo, activeRun, timer, gpuLayers = null;
const logs = [];
let runs = [];
try { runs = JSON.parse(localStorage.getItem(storageKey) || '[]').slice(-20); } catch { /* optional local storage */ }
const device = { user_agent: navigator.userAgent, logical_cpus: navigator.hardwareConcurrency,
  reported_memory_gb: navigator.deviceMemory ?? null, cross_origin_isolated: crossOriginIsolated };
$('threads').value = Math.min(8, Math.max(1, Math.floor((navigator.hardwareConcurrency || 2) / 2)));

function status(text, error = false) { $('status').textContent = text; $('status').classList.toggle('error', error); }
function controls() {
  $('load').disabled = busy || loaded || !manifest;
  $('unload').disabled = busy || !loaded;
  $('generate').disabled = busy || !loaded;
  $('stop').disabled = !busy || !controller;
  for (const id of ['backend', 'threads', 'context']) $(id).disabled = busy || loaded;
  for (const id of ['description', 'max-tokens', 'cache']) $(id).disabled = busy;
  for (const button of document.querySelectorAll('[data-example]')) button.disabled = busy;
}
function log(level, ...args) {
  const text = args.map((a) => typeof a === 'string' ? a : JSON.stringify(a)).join(' ');
  const match = text.match(/offloaded\s+(\d+)\/(\d+)\s+layers to GPU/i);
  if (match) gpuLayers = { offloaded: Number(match[1]), total: Number(match[2]) };
  logs.push(text); if (logs.length > 200) logs.shift();
  $('logs').textContent = logs.join('\n');
  if (level === 'error' || level === 'warn') console[level](...args);
}
function renderHistory() {
  $('history').replaceChildren();
  for (const run of [...runs].reverse()) {
    const tr = document.createElement('tr');
    for (const value of [new Date(run.at).toLocaleTimeString() + (run.finish === 'stop' ? '' : ` · ${run.finish}`),
      run.backend, seconds(run.first_token_ms), seconds(run.generation_ms), run.tokens_per_second?.toFixed(2) ?? '—']) {
      const td = document.createElement('td'); td.textContent = value; tr.append(td);
    }
    $('history').append(tr);
  }
  $('export').disabled = !runs.length;
}
function save(run) {
  runs.push(run); runs = runs.slice(-20);
  try { localStorage.setItem(storageKey, JSON.stringify(runs)); } catch { /* export still works */ }
  renderHistory();
}
function backendLabel(requested) {
  if (requested === 'cpu' || gpuLayers?.offloaded === 0) return `CPU · ${engine.isMultithread() ? $('threads').value : 1} threads`;
  if (gpuLayers?.offloaded > 0) return `WebGPU · ${gpuLayers.offloaded}/${gpuLayers.total} layers`;
  return 'GPU requested · offload unconfirmed';
}
async function loadModel() {
  if (busy || loaded) return;
  busy = true; gpuLayers = null; controls(); status('Preparing model download…');
  const start = performance.now();
  timer = setInterval(() => { $('load-time').textContent = seconds(performance.now() - start); }, 150);
  try {
    engine = new Wllama({ default: url('vendor/wllama/wasm/wllama.wasm') }, {
      parallelDownloads: 3,
      logger: Object.fromEntries(['debug', 'log', 'warn', 'error'].map((level) => [level, (...args) => log(level, ...args)])),
    });
    engine.setCompat({ wasm: url('vendor/compat/wllama.wasm'), worker: url('vendor/compat/wllama.js') });
    const selected = $('backend').value;
    const adapter = selected === 'auto' && navigator.gpu ? await navigator.gpu.requestAdapter() : null;
    device.gpu = adapter?.info ? { vendor: adapter.info.vendor, architecture: adapter.info.architecture,
      device: adapter.info.device, description: adapter.info.description } : null;
    const requested = adapter && engine.isSupportWebGPU() ? 'gpu' : 'cpu';
    const modelUrl = url(manifest.first_shard);
    const cached = (await engine.modelManager.getModels()).find((m) => m.url === modelUrl && m.validate() === 'valid');
    const fromCache = Boolean(cached);
    status(fromCache ? 'Loading cached model…' : 'Downloading model to this device…');
    const downloadStart = performance.now();
    $('progress').hidden = false;
    const model = await engine.modelManager.getModelOrDownload({ url: modelUrl }, {
      progressCallback: ({ loaded: bytes, total }) => {
        $('progress').value = total ? bytes / total : 0;
        status(`Downloading ${(bytes / 1e9).toFixed(2)} / ${(total / 1e9).toFixed(2)} GB…`);
      },
    });
    const downloadMs = performance.now() - downloadStart;
    $('progress').value = 1;
    status(`Loading model on ${requested === 'gpu' ? 'WebGPU' : 'CPU'}…`);
    const initStart = performance.now();
    const settings = { n_ctx: Number($('context').value), n_threads: Math.min(32, Math.max(1, Number($('threads').value) || 1)),
      n_gpu_layers: requested === 'gpu' ? 99 : 0, n_batch: 256, n_ubatch: 128, n_parallel: 1,
      seed: 17, jinja: true, default_template_kwargs: { enable_thinking: false } };
    await engine.loadModel(model, settings);
    loaded = true;
    loadInfo = { total_ms: performance.now() - start, download_or_cache_ms: downloadMs,
      initialization_ms: performance.now() - initStart, model_was_cached: fromCache, requested,
      backend: backendLabel(requested), gpu_layers: gpuLayers, settings,
      runtime: { wllama: manifest.runtime, llama: Wllama.getLibllamaVersion() } };
    $('load-time').textContent = seconds(loadInfo.total_ms);
    $('engine').textContent = loadInfo.backend;
    $('device').textContent = JSON.stringify({ ...device, model: manifest.label, load: loadInfo }, null, 2);
    status(`Ready · ${fromCache ? 'model loaded from cache' : 'model downloaded and cached'} · generation runs in this browser.`);
  } catch (error) {
    status(`Model load failed: ${error.message}. Try CPU mode or a smaller context.`, true);
    log('error', error.stack || error.message);
    try { await engine?.exit(); } catch { /* worker may already be terminated */ }
    engine = null;
  } finally { clearInterval(timer); busy = false; controls(); }
}
async function generate() {
  if (busy || !loaded) return;
  const description = $('description').value.trim();
  if (!description) { status('Enter a card description.', true); return; }
  busy = true; controller = new AbortController(); controls();
  $('output').textContent = ''; $('json-status').textContent = '';
  for (const id of ['first-time', 'total-time', 'speed']) $(id).textContent = '—';
  $('tokens').textContent = 'Runtime token count';
  status('Processing prompt…');
  const start = performance.now();
  activeRun = { at: new Date().toISOString(), model_sha256: manifest.source_sha256,
    system_prompt_sha256: manifest.system_prompt_sha256,
    model: manifest.label, device: { ...device }, load: loadInfo, backend: loadInfo.backend,
    description, output: '', first_token_ms: null, generation_ms: null, tokens_per_second: null,
    usage: null, runtime_timings: null, finish: 'unknown',
    settings: { max_tokens: Number($('max-tokens').value), temperature: 0, seed: 17, cache_prompt: $('cache').checked } };
  timer = setInterval(() => { $('total-time').textContent = seconds(performance.now() - start); }, 100);
  try {
    await engine.createChatCompletion({
      messages: [{ role: 'system', content: systemPrompt }, { role: 'user', content: description }],
      ...activeRun.settings, chat_template_kwargs: { enable_thinking: false },
      stream: true, timings_per_token: true, return_progress: true, abortSignal: controller.signal,
      onData(chunk) {
        const choice = chunk.choices?.[0];
        const delta = choice?.delta?.content;
        if (delta) {
          if (activeRun.first_token_ms == null) {
            activeRun.first_token_ms = performance.now() - start;
            $('first-time').textContent = seconds(activeRun.first_token_ms);
          }
          activeRun.output += delta; $('output').textContent = activeRun.output; status('Generating…');
        } else if (chunk.prompt_progress && activeRun.first_token_ms == null) {
          const p = chunk.prompt_progress;
          status(p.processed ? `Processing prompt · ${p.processed} / ${p.total} tokens…` : `Processing ${p.total}-token prompt…`);
        }
        if (chunk.usage) activeRun.usage = chunk.usage;
        if (chunk.timings) activeRun.runtime_timings = chunk.timings;
        if (choice?.finish_reason) activeRun.finish = choice.finish_reason;
      },
    });
    if (controller.signal.aborted) activeRun.finish = 'cancelled';
    status(activeRun.finish === 'length' ? 'Output limit reached · increase it for a complete card.' : activeRun.finish === 'cancelled' ? 'Generation stopped.' : 'Generation complete.');
  } catch (error) {
    activeRun.finish = controller.signal.aborted ? 'cancelled' : 'error';
    activeRun.error = error.message;
    status(activeRun.finish === 'cancelled' ? 'Generation stopped.' : `Generation failed: ${error.message}`, activeRun.finish === 'error');
    if (activeRun.finish === 'error') log('error', error.stack || error.message);
  } finally {
    clearInterval(timer);
    activeRun.generation_ms = performance.now() - start;
    activeRun.tokens_per_second = activeRun.runtime_timings?.predicted_per_second ?? null;
    activeRun.json_syntax_valid = false;
    try { JSON.parse(activeRun.output); activeRun.json_syntax_valid = true; } catch { /* display raw output */ }
    $('json-status').textContent = activeRun.json_syntax_valid ? 'Valid JSON syntax' : 'Incomplete or non-JSON output';
    $('total-time').textContent = seconds(activeRun.generation_ms);
    $('speed').textContent = activeRun.tokens_per_second == null ? '—' : `${activeRun.tokens_per_second.toFixed(2)} t/s`;
    const count = activeRun.usage?.completion_tokens ?? activeRun.runtime_timings?.predicted_n;
    $('tokens').textContent = count == null ? 'Token count unavailable' : `${count} output tokens`;
    save(activeRun); activeRun = null; busy = false; controller = null; controls();
  }
}
$('load').addEventListener('click', loadModel);
$('generate').addEventListener('click', generate);
$('stop').addEventListener('click', () => { controller?.abort(); status('Stopping…'); });
$('unload').addEventListener('click', async () => {
  busy = true; controls();
  try { await engine.exit(); engine = null; loaded = false; $('engine').textContent = 'Model unloaded'; status('Model unloaded. Cached files remain available.'); }
  catch (error) { status(error.message, true); }
  finally { busy = false; controls(); }
});
for (const button of document.querySelectorAll('[data-example]')) button.addEventListener('click', () => { $('description').value = button.dataset.example; });
$('export').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({ benchmark: 'oracle-browser-v1', exported_at: new Date().toISOString(), runs }, null, 2)], { type: 'application/json' });
  const href = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = href;
  a.download = `oracle-benchmark-${new Date().toISOString().replace(/[:.]/g, '-')}.json`; a.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
});
try {
  [manifest, systemPrompt] = await Promise.all([
    fetch(url('model.json')).then((r) => { if (!r.ok) throw new Error('Missing model manifest'); return r.json(); }),
    fetch(url('system-prompt.txt')).then((r) => { if (!r.ok) throw new Error('Missing system prompt'); return r.text(); }),
  ]);
  $('identity').textContent = `${manifest.label} · wllama ${manifest.runtime}`;
  $('device').textContent = JSON.stringify(device, null, 2);
  $('engine').textContent = navigator.gpu ? 'WebGPU API available · checked on load' : 'CPU mode available';
  status(crossOriginIsolated ? 'Ready to load. The initial model download is 2.37 GB.' : 'Ready to load. CPU threading is unavailable without browser isolation headers.');
  renderHistory(); controls();
} catch (error) { status(`Setup failed: ${error.message}`, true); }
