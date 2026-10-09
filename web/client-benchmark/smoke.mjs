import { chromium } from 'playwright';
import { mkdir, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';

const target = process.env.BENCH_URL || 'https://tetrarchs.com/model-bench/';
const artifacts = process.env.BENCH_ARTIFACTS;
if (!artifacts || !process.env.CHROME_BIN) throw new Error('Set BENCH_ARTIFACTS and CHROME_BIN');
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launchPersistentContext(`${artifacts}/chrome-profile`, {
  executablePath: process.env.CHROME_BIN, headless: true, viewport: { width: 1280, height: 1100 },
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});
const page = await browser.newPage();
const errors = [], requests = [];
page.on('pageerror', (error) => errors.push(error.message));
page.on('request', (req) => requests.push({ method: req.method(), url: req.url() }));
const tick = setInterval(async () => {
  try { console.log(await page.locator('#status').textContent()); } catch { /* closing */ }
}, 10000);
try {
  await page.goto(target);
  await page.locator('#load').waitFor();
  await page.waitForFunction(() => !document.querySelector('#load').disabled);
  assert.equal(await page.evaluate(() => crossOriginIsolated), true);
  await page.locator('#backend').selectOption('cpu');
  await page.locator('#context').selectOption('2048');
  await page.locator('#threads').fill(process.env.BENCH_THREADS || '8');
  await page.locator('#temperature').fill('0');
  await page.locator('#load').click();
  await page.waitForFunction(() => !document.querySelector('#generate').disabled || document.querySelector('#status').classList.contains('error'), null, { timeout: 420000 });
  if (await page.locator('#generate').isDisabled()) throw new Error(await page.locator('#status').textContent());
  await page.locator('#description').fill('A 3/3 red Giant creature that costs {3}{R}, with no abilities.');
  await page.locator('#generate').click();
  await page.waitForFunction(() => document.querySelector('#output').textContent.length > 0 || document.querySelector('#status').classList.contains('error'), null, { timeout: 900000 });
  const observedStreaming = await page.locator('#stop').isEnabled();
  await page.waitForFunction(() => !document.querySelector('#generate').disabled, null, { timeout: 600000 });
  const runs = await page.evaluate(() => JSON.parse(localStorage.getItem('oracle-browser-benchmark-v1')));
  const run = runs.at(-1);
  assert.equal(run.finish, 'stop');
  assert.equal(run.json_syntax_valid, true);
  assert(run.first_token_ms > 0 && run.generation_ms >= run.first_token_ms);
  assert((run.usage?.completion_tokens ?? run.runtime_timings?.predicted_n) > 0);
  assert(observedStreaming);
  assert(requests.every((r) => r.method === 'GET' || r.method === 'HEAD'));
  assert(!requests.some((r) => r.url.includes('/api/card-generator/')));
  await writeFile(`${artifacts}/completed-run.json`, JSON.stringify({ run, observed_streaming: observedStreaming, errors, requests }, null, 2));
  await page.screenshot({ path: `${artifacts}/browser.png`, fullPage: true });
  await page.locator('#cache').check();
  await page.locator('#generate').click();
  await page.waitForFunction(() => document.querySelector('#output').textContent.length > 0, null, { timeout: 120000 });
  await page.locator('#stop').click();
  await page.waitForFunction(() => !document.querySelector('#generate').disabled, null, { timeout: 60000 });
  const stopped = await page.evaluate(() => JSON.parse(localStorage.getItem('oracle-browser-benchmark-v1')).at(-1));
  assert.equal(stopped.finish, 'cancelled');
  const result = { target, observed_streaming: observedStreaming, no_server_inference: true,
    isolation_headers: true, cancellation: true, run, errors, requests };
  await writeFile(`${artifacts}/smoke.json`, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ finish: run.finish, output: run.output, first_token_ms: run.first_token_ms,
    generation_ms: run.generation_ms, tokens_per_second: run.tokens_per_second, cancellation: true }));
} catch (error) {
  await writeFile(`${artifacts}/smoke-error.json`, JSON.stringify({ error: error.stack, errors, requests,
    status: await page.locator('#status').textContent(), logs: await page.locator('#logs').textContent() }, null, 2));
  await page.screenshot({ path: `${artifacts}/browser-error.png`, fullPage: true });
  throw error;
} finally { clearInterval(tick); await browser.close(); }
