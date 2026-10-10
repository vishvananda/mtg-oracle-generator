// Keep the runtime's logical safetensors URLs/cache keys stable. Only network
// reads are translated, so cached tensors survive this transport-only change.
export const MAX_ASSET_BYTES = 512_000_000;

export function createChunkedFetch(modelRoot, manifest, fetcher = globalThis.fetch.bind(globalThis)) {
  const root = new URL(modelRoot), files = new Map();
  const resolve = path => {
    const url = new URL(path, root);
    if (url.origin !== root.origin || !url.href.startsWith(root.href) || url.search || url.hash) {
      throw new Error('Invalid model chunk path.');
    }
    return url.href;
  };
  if (manifest.version !== 1 || !Array.isArray(manifest.files)) throw new Error('Unsupported model chunk manifest.');
  for (const file of manifest.files) {
    let offset = 0;
    const chunks = file.chunks.map(chunk => {
      if (!Number.isSafeInteger(chunk.bytes) || chunk.bytes <= 0 || chunk.bytes >= MAX_ASSET_BYTES || chunk.offset !== offset) {
        throw new Error('Invalid model chunk size or offset.');
      }
      offset += chunk.bytes;
      return { ...chunk, url: resolve(chunk.path) };
    });
    if (!chunks.length || offset !== file.bytes) throw new Error('Incomplete model chunk manifest.');
    const url = resolve(file.path);
    if (files.has(url)) throw new Error('Duplicate model chunk mapping.');
    files.set(url, { ...file, chunks });
  }
  return async (input, options = {}) => {
    const url = input instanceof Request ? input.url : String(input);
    const file = files.get(url);
    if (!file) return fetcher(input, options);
    const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
    const signal = options.signal || (input instanceof Request ? input.signal : undefined);
    signal?.throwIfAborted();
    const headers = { 'Content-Type': 'application/octet-stream', 'Accept-Ranges': 'bytes', 'Content-Length': String(file.bytes) };
    if (method === 'HEAD') return new Response(null, { headers });
    const range = new Headers(options.headers || (input instanceof Request ? input.headers : undefined)).get('Range');
    const match = /^bytes=(\d+)-(\d+)$/.exec(range || '');
    if (method !== 'GET' || !match) throw new Error('Model weights require an explicit byte range.');
    const start = Number(match[1]), end = Number(match[2]) + 1;
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start >= end || end > file.bytes) {
      return new Response(null, { status: 416, headers: { 'Content-Range': `bytes */${file.bytes}` } });
    }
    const chunks = file.chunks.filter(chunk => chunk.offset < end && chunk.offset + chunk.bytes > start);
    let index = 0, reader, remaining = 0;
    // Stream only the requested portions; never assemble a multi-GB weight file.
    const body = new ReadableStream({
      async pull(controller) {
        try {
          signal?.throwIfAborted();
          if (!reader) {
            const chunk = chunks[index++];
            if (!chunk) { controller.close(); return; }
            const first = Math.max(start - chunk.offset, 0), last = Math.min(end - chunk.offset, chunk.bytes) - 1;
            const response = await fetcher(chunk.url, { method: 'GET', headers: { Range: `bytes=${first}-${last}` }, signal, credentials: 'omit' });
            const expected = `bytes ${first}-${last}/${chunk.bytes}`;
            if (response.status !== 206 || response.headers.get('Content-Range') !== expected ||
                Number(response.headers.get('Content-Length')) !== last - first + 1 || !response.body) {
              await response.body?.cancel();
              throw new Error('Model chunk server returned an invalid byte range.');
            }
            remaining = last - first + 1;
            reader = response.body.getReader();
          }
          const { done, value } = await reader.read();
          if (done) {
            reader.releaseLock(); reader = null;
            if (remaining !== 0) throw new Error('Model chunk download was truncated.');
            return this.pull(controller);
          }
          remaining -= value.byteLength;
          if (remaining < 0) throw new Error('Model chunk download exceeded its byte range.');
          controller.enqueue(value);
        } catch (error) { await reader?.cancel().catch(() => {}); controller.error(error); }
      },
      async cancel(reason) { await reader?.cancel(reason); },
    });
    return new Response(body, { status: 206, headers: { ...headers,
      'Content-Length': String(end - start), 'Content-Range': `bytes ${start}-${end - 1}/${file.bytes}` } });
  };
}

export async function loadChunkedFetch(modelRoot, fetcher = globalThis.fetch.bind(globalThis)) {
  const response = await fetcher(new URL('transport.json', modelRoot), { credentials: 'omit' });
  if (!response.ok) throw new Error(`Model chunk manifest download failed (${response.status}).`);
  return createChunkedFetch(modelRoot, await response.json(), fetcher);
}
