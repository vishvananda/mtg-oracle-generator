// Showcase cards must pass the same unmodified Wasm flow exposed to users.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium} from 'playwright';
const base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-demos';
await mkdir(out,{recursive:true});const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],cards=[];
try{
 const context=await browser.newContext({viewport:{width:1440,height:900},reducedMotion:'reduce'});await context.addInitScript(()=>localStorage.setItem('forge-sound','off'));
 const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base);
 const demos=await p.evaluate(async()=>{const{demos}=await import('./cards.js');return demos;});
 for(const card of demos){
  await p.getByRole('button',{name:`Show ${card.name}`,exact:true}).click();
  await p.waitForFunction(name=>document.querySelector('#card-caption').textContent===name&&document.querySelector('#card-preview').getAttribute('aria-busy')==='false'&&document.querySelector('#oracle-status').dataset.state!=='checking',card.name);
  const actual=await p.locator('#oracle-status').getAttribute('data-state'),label=await p.locator('#oracle-label').innerText();
  assert.equal(actual,'passed',`${card.name}: ${label}`);assert.equal(await p.locator('#oracle-fix').isVisible(),false);
  checks.push(`${card.name}: complete card parses in browser`);cards.push({name:card.name,type_line:card.type_line,oracle_text:card.oracle_text,status:actual,label});console.log('PASS',checks.at(-1));
  if(cards.length<=2)await p.screenshot({path:`${out}/${card.id}.png`});
 }
 assert.equal(errors.length,0);checks.push('no JavaScript errors');
 assert.equal(await p.evaluate(()=>performance.getEntriesByType('resource').some(e=>/local-models|\.gguf|bonsai-worker/.test(e.name))),false);checks.push('no generation models needed');
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({base,checks,cards,errors},null,2)+'\n');}
