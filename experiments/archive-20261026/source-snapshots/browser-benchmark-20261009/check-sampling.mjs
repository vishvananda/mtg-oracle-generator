import { chromium } from '/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark/node_modules/playwright/index.mjs';
import { readFile,writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const root='/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark/dist';
const out='/home/vishvananda/vizier-editor-artifacts/card-authoring/browser-benchmark-20261009';
const browser=await chromium.launchPersistentContext(out+'/test/chrome-profile',{executablePath:'/home/vishvananda/.local/bin/vizier-chrome',headless:true,viewport:{width:1280,height:1100},args:['--no-sandbox','--disable-dev-shm-usage']});
const errors=[];
try {
 const page=await browser.newPage(); page.on('pageerror',e=>errors.push(e.message));
 for (const [path,file] of [['','index.html'],['app.js','app.js'],['style.css','style.css']]) await page.route('https://tetrarchs.com/model-bench/'+path,async r=>{const response=await r.fetch();await r.fulfill({response,body:await readFile(root+'/'+file)});});
 await page.route('**/system-prompt.txt',r=>r.fulfill({status:200,contentType:'text/plain',body:'Return only a JSON object with a name field.'}));
 await page.goto('https://tetrarchs.com/model-bench/'); await page.waitForFunction(()=>!document.querySelector('#load').disabled);
 assert.equal(await page.locator('#temperature').inputValue(),'0.7'); assert(await page.locator('#random-seed').isChecked());
 await page.locator('#backend').selectOption('cpu'); await page.locator('#threads').fill('8'); await page.locator('#context').selectOption('2048'); await page.locator('#max-tokens').selectOption('128');
 await page.locator('#load').click(); await page.waitForFunction(()=>!document.querySelector('#generate').disabled,null,{timeout:90000});
 await page.locator('#description').fill('Invent a short fantasy name for a Giant.'); await page.locator('#cache').check();
 const runs=[];
 async function generate(){await page.locator('#generate').click(); await page.waitForFunction(()=>!document.querySelector('#generate').disabled,null,{timeout:120000}); const r=await page.evaluate(()=>JSON.parse(localStorage.getItem('oracle-browser-benchmark-v1')).at(-1)); assert.equal(r.finish,'stop'); assert(r.runtime_timings.predicted_n>0); runs.push(r); console.log(JSON.stringify({output:r.output,settings:r.settings,cache:r.runtime_timings.cache_n})); return r;}
 const first=await generate(); const second=await generate();
 assert.equal(first.settings.temperature,0.7); assert.equal(first.settings.top_p,0.8); assert.equal(first.settings.top_k,20); assert.notEqual(first.settings.seed,second.settings.seed); assert(second.runtime_timings.cache_n>0);
 await page.locator('#random-seed').uncheck(); const repeat=await generate(); assert.equal(repeat.settings.seed,second.settings.seed); assert.equal(repeat.output,second.output);
 await page.locator('#temperature').fill('0'); assert(await page.locator('#seed').isDisabled()); assert(await page.locator('#random-seed').isDisabled());
 const greedy=await generate(); assert.equal(greedy.settings.temperature,0); assert.equal(greedy.settings.seed,17);
 assert.equal(errors.length,0); await page.screenshot({path:out+'/sampling-desktop.png',fullPage:true});
 await page.setViewportSize({width:390,height:844}); assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)); await page.screenshot({path:out+'/sampling-mobile.png',fullPage:true});
 await writeFile(out+'/sampling-validation.json',JSON.stringify({diagnostic_system_prompt:'Return only a JSON object with a name field.',runs,errors,random_outputs_differ:first.output!==second.output},null,2));
} finally { await browser.close(); }
