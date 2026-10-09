import { BONSAI } from './bonsai-config.js';

// The upstream Space currently publishes a compiled demo, not an importable package.
// Fetch its pinned bundle from the publisher, verify it, and expose only the pipeline.
// No upstream bundle, landing-page code, analytics, or sharing UI is vendored in this repo.
export async function loadBonsaiRuntime() {
  let cache;
  try { cache = await caches.open('oracle-bonsai-runtime-v1'); } catch { /* optional cache */ }
  let response = await cache?.match(BONSAI.runtime_url);
  if (!response) {
    response = await fetch(BONSAI.runtime_url, { credentials: 'omit' });
    if (!response.ok) throw new Error(`Bonsai runtime download failed (${response.status}).`);
  }
  const bytes = await response.arrayBuffer();
  const digest = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))]
    .map((byte) => byte.toString(16).padStart(2, '0')).join('');
  if (digest !== BONSAI.runtime_sha256) {
    await cache?.delete(BONSAI.runtime_url);
    throw new Error('Bonsai runtime integrity check failed. Reload to retry.');
  }
  try { await cache?.put(BONSAI.runtime_url, new Response(bytes)); } catch { /* optional cache */ }
  const source = new TextDecoder().decode(bytes);
  const start = source.indexOf('var e=Object.freeze({float16:');
  const end = source.indexOf('var Gi={LEFT:0');
  if (start < 0 || end <= start || !source.slice(start, end).includes('Bi=class e{')) {
    throw new Error('The pinned Bonsai runtime has an unexpected module layout.');
  }
  const moduleUrl = URL.createObjectURL(new Blob([
    source.slice(start, end), '\nexport { Bi as Flux2KleinPipeline, a as createRuntime };\n',
  ], { type: 'text/javascript' }));
  try { return await import(moduleUrl); } finally { URL.revokeObjectURL(moduleUrl); }
}
