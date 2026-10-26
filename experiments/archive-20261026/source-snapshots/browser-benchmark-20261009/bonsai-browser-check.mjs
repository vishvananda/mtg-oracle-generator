import {chromium} from '/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark/node_modules/playwright/index.mjs';
import {readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root='/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark';
const out='/home/vishvananda/vizier-editor-artifacts/card-authoring/browser-benchmark-20261009';
const base='https://tetrarchs.com/model-bench/';
const b=await chromium.launch({executablePath:'/home/vishvananda/.local/bin/vizier-chrome',headless:true,args:['--no-sandbox','--enable-unsafe-webgpu','--use-angle=swiftshader','--enable-features=Vulkan']});
try {
 const context=await b.newContext({viewport:{width:1280,height:1000}}),p=await context.newPage();
 const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await context.route(base+'**',async route=>{
  const path=new URL(route.request().url()).pathname.slice('/model-bench/'.length)||'index.html';
  if(['index.html','app.js','style.css','bonsai.js','bonsai-worker.js','bonsai-config.js','bonsai-loader.js'].includes(path)) return route.fulfill({status:200,contentType:path.endsWith('.js')?'text/javascript':path.endsWith('.css')?'text/css':'text/html',headers:{'Cross-Origin-Opener-Policy':'same-origin','Cross-Origin-Embedder-Policy':'require-corp'},body:await readFile(root+'/'+path)});
  return route.continue();
 });
 await p.goto(base);await p.waitForFunction(()=>!document.querySelector('#load').disabled);
 const probe=await p.evaluate(async(base)=>{
  const {loadBonsaiRuntime}=await import(base+'bonsai-loader.js'); const mod=await loadBonsaiRuntime();
  const rt=await mod.createRuntime();try{const tensor=rt.tensorFromTypedArray('float32',[4],new Float32Array([1,2,3,4]));const result=Array.from(await rt.readTensor(tensor));tensor.destroy();return{pipeline_api:typeof mod.Flux2KleinPipeline.from_pretrained,caps:rt.caps(),tensor_roundtrip:result};}finally{await rt.destroy();}
 },base);
 assert.deepEqual(probe.tensor_roundtrip,[1,2,3,4]);assert.equal(probe.pipeline_api,'function');
 console.log('Actual pinned runtime imported; WebGPU tensor upload/readback passed.');
 const workerProbe=await p.evaluate(async(base)=>{
  const code=`import {loadBonsaiRuntime} from '${base}bonsai-loader.js'; try { const m=await loadBonsaiRuntime();const rt=await m.createRuntime();postMessage({ok:true,caps:rt.caps()});await rt.destroy();}catch(e){postMessage({error:e.message});}`;
  const url=URL.createObjectURL(new Blob([code],{type:'text/javascript'}));const w=new Worker(url,{type:'module'});
  try{return await new Promise((resolve,reject)=>{w.onmessage=e=>resolve(e.data);w.onerror=e=>reject(Error(e.message));});}finally{w.terminate();URL.revokeObjectURL(url);}
 },base);
 assert.equal(workerProbe.ok,true,JSON.stringify(workerProbe));console.log('Actual pinned runtime initializes in a module worker.');
 await context.route(base+'bonsai-worker.js',route=>route.fulfill({status:200,contentType:'text/javascript',headers:{'Cross-Origin-Embedder-Policy':'require-corp','Cross-Origin-Opener-Policy':'same-origin'},body:`
 self.onmessage=async({data:d})=>{if(d.type==='load'){postMessage({type:'progress',progress:{component:'text_encoder',loaded:100,total:100,fromCache:true}});postMessage({type:'ready',caps:{adapter:{description:'Test fixture'},f16:true},pipeline_load_ms:1});}
 else if(d.type==='generate'){postMessage({type:'stage',message:'text encode'});await new Promise(r=>setTimeout(r,d.steps===8?10000:100));postMessage({type:'step',completed:d.steps,total:d.steps});const b=Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='),x=>x.charCodeAt(0));postMessage({type:'image',blob:new Blob([b],{type:'image/png'}),generation_ms:100});}
 else if(d.type==='unload'){postMessage({type:'unloaded'});self.close();}};`}));
 await p.locator('#art-use-card').click(); assert.match(await p.locator('#art-prompt').inputValue(),/rummages/);
 await p.locator('#art-load').click();await p.waitForFunction(()=>!document.querySelector('#art-generate').disabled || document.querySelector('#art-status').classList.contains('error')); assert(await p.locator('#art-generate').isEnabled(),await p.locator('#art-status').textContent());
 await p.locator('#art-generate').click();await p.waitForFunction(()=>document.querySelector('#art-status').textContent==='Image complete.');
 assert(await p.locator('#art-png').isVisible());assert(await p.locator('#art-export').isEnabled());
 const complete=await p.evaluate(()=>JSON.parse(localStorage.getItem('oracle-bonsai-benchmark-v1')).at(-1));assert.equal(complete.finish,'complete');assert.equal(complete.width,512);assert.equal(complete.steps,4);
 await p.locator('#art-steps').selectOption('8');await p.locator('#art-generate').click();await p.locator('#art-stop').click();assert(await p.locator('#art-load').isEnabled());assert(await p.locator('#art-generate').isDisabled());
 const stopped=await p.evaluate(()=>JSON.parse(localStorage.getItem('oracle-bonsai-benchmark-v1')).at(-1));assert.equal(stopped.finish,'cancelled');
 await p.locator('#art-demo').scrollIntoViewIfNeeded();await p.screenshot({path:out+'/bonsai-desktop.png',fullPage:true});
 await p.setViewportSize({width:390,height:844});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await p.screenshot({path:out+'/bonsai-mobile.png',fullPage:true});
 assert.equal(errors.length,0);
 await writeFile(out+'/bonsai-validation.json',JSON.stringify({runtime_probe:probe,worker_probe:workerProbe,ui_with_fixture:{generation:true,png_download:true,metadata:true,cancellation:true,mobile_layout:true},full_image_inference_tested:false,errors},null,2));
 console.log('UI fixture: image display, timing/export, cancellation and mobile layout passed.');
}finally{await b.close();}
