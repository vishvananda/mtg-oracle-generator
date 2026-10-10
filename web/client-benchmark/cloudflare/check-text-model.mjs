// Optional real inference smoke test. Uses CPU/Wasm on the CI host, not a GPU benchmark.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.FORGE_URL||'https://cardforge.fyi/',out=process.env.FORGE_EVIDENCE||'/tmp/cardforge-text-model';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});let result;
try{
 const p=await browser.newPage({viewport:{width:1200,height:900},reducedMotion:'reduce'});p.on('console',m=>{if(m.type()==='log'&&m.text().startsWith('MODEL '))console.log(m.text());});await p.goto(base);
 result=await p.evaluate(async()=>{
  const {Wllama}=await import('/model-bench/vendor/wllama/index.js');
  const manifest=await fetch('/model-bench/model.json').then(r=>r.json()),system=await fetch('/model-bench/system-prompt.txt').then(r=>r.text());
  const url=path=>new URL('/model-bench/'+path,location.origin).href;
  const engine=new Wllama({default:url('vendor/wllama/wasm/wllama.wasm')},{parallelDownloads:3,logger:{debug(){},log(){},warn(){},error:console.error}});
  engine.setCompat({wasm:url('vendor/compat/wllama.wasm'),worker:url('vendor/compat/wllama.js')});
  let percent=-1;const start=performance.now();
  try{
   const model=await engine.modelManager.getModelOrDownload({url:manifest.first_shard},{progressCallback:({loaded,total})=>{const n=Math.floor(loaded/total*10);if(n>percent){percent=n;console.log('MODEL download '+n*10+'%');}}});
   const downloaded=performance.now();console.log('MODEL initializing CPU/Wasm');
   await engine.loadModel(model,{n_ctx:2048,n_threads:4,n_gpu_layers:0,n_batch:128,n_ubatch:64,n_parallel:1,jinja:true,default_template_kwargs:{enable_thinking:false}});
   const ready=performance.now();console.log('MODEL generating');let text='';
   await engine.createChatCompletion({messages:[{role:'system',content:system},{role:'user',content:'A common red Giant creature named Emberhill Giant. It costs 3R, is 3/3, and has no abilities.'}],max_tokens:384,temperature:.2,seed:42,chat_template_kwargs:{enable_thinking:false},stream:true,onData:c=>{text+=c.choices?.[0]?.delta?.content||'';}});
   const done=performance.now(),match=text.match(/\{[\s\S]*\}/);if(!match)throw Error('No card JSON: '+text);
   const card=JSON.parse(match[0]);const {OracleValidator}=await import(new URL('./oracle-validator.js',location.href));const validator=new OracleValidator();const validation=await validator.check(card);validator.dispose();
   return {backend:'CPU/Wasm, 4 threads; not representative of WebGPU device speed',download_ms:downloaded-start,load_ms:ready-downloaded,generation_ms:done-ready,card,validation};
  }finally{await engine.exit();}
 });
 assert.equal(result.card.name,'Emberhill Giant');assert.equal(result.validation.parse_complete,true);console.log(JSON.stringify(result,null,2));
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify(result||{failed:true},null,2)+'\n');}
