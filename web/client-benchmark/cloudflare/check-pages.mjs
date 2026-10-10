// Exercise the deployed origin, including CORS reads of real R2 tensor bytes.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.FORGE_URL||'https://cardforge-8am.pages.dev/',out=process.env.FORGE_EVIDENCE||'/tmp/cardforge-pages';
await mkdir(out,{recursive:true});const checks=[],errors=[];
const check=(name,ok)=>{assert.ok(ok,name);checks.push(name);console.log('PASS',name);};
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
try{
 const p=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});p.on('pageerror',e=>errors.push(e.message));await p.goto(base);
 await p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false'&&document.querySelector('#oracle-status').dataset.state==='passed');
 check('Pages is cross-origin isolated',await p.evaluate(()=>crossOriginIsolated&&typeof SharedArrayBuffer==='function'));
 await p.locator('#oracle-info').click();check('Details links mtgish to its GitHub repository',await p.locator('#oracle-detail a').getAttribute('href')==='https://github.com/i5jb/mtgish'&&await p.locator('#oracle-detail a').isVisible());
 const runtime=await p.evaluate(async()=>{
  const model=await fetch('/model-bench/model.json').then(r=>r.json());
  const {BONSAI}=await import('/model-bench/bonsai-config.js');
  const {loadBonsaiRuntime}=await import('/model-bench/bonsai-loader.js');
  const module=await loadBonsaiRuntime();
  const {Wllama}=await import('/model-bench/vendor/wllama/index.js');
  return {text:model.first_shard,image:BONSAI.model_path,bonsai:typeof module.Flux2KleinPipeline,wllama:typeof Wllama};
 });
 check('card and art model manifests target R2',runtime.text.startsWith('https://models.cardforge.fyi/')&&runtime.image.startsWith('https://models.cardforge.fyi/'));
 check('both actual model runtimes import successfully',runtime.bonsai==='function'&&runtime.wllama==='function');
 const ranges=await p.evaluate(async()=>{
  const m=await fetch('/model-bench/model.json').then(r=>r.json());
  const root=new URL(m.first_shard),results=[];
  for(const shard of m.shards){const url=new URL(shard.name,root),r=await fetch(url,{headers:{Range:'bytes=0-15'}}),b=new Uint8Array(await r.arrayBuffer());results.push({name:shard.name,status:r.status,length:r.headers.get('Content-Length'),range:r.headers.get('Content-Range'),etag:r.headers.get('ETag'),magic:new TextDecoder().decode(b.slice(0,4))});}
  const {BONSAI}=await import('/model-bench/bonsai-config.js'),{loadChunkedFetch}=await import('/model-bench/bonsai-chunks.js');
  const manifest=await fetch(BONSAI.model_path+'transport.json').then(r=>r.json()),request=await loadChunkedFetch(BONSAI.model_path);
  for(const file of manifest.files){const r=await request(BONSAI.model_path+file.path,{headers:{Range:'bytes=0-7'}});const bytes=new Uint8Array(await r.arrayBuffer());results.push({name:file.path,status:r.status,length:r.headers.get('Content-Length'),range:r.headers.get('Content-Range'),headerBytes:bytes.length});}
  return results;
 });
 for(const r of ranges){check(`${r.name}: CORS exposes exact byte-range response`,r.status===206&&Number(r.length)===(r.magic?16:8)&&r.range?.startsWith('bytes 0-'));if(r.magic)check(`${r.name}: valid GGUF header`,r.magic==='GGUF');}
 await p.screenshot({path:`${out}/desktop.png`});check('no browser errors',errors.length===0);
 await writeFile(`${out}/transport.json`,JSON.stringify({runtime,ranges},null,2)+'\n');
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({base,checks,errors},null,2)+'\n');}
