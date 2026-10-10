import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,devices} from 'playwright';
const base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-gentle-motion';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],requests=[];
const check=(name,ok)=>{assert.ok(ok,name);checks.push(name);console.log('PASS',name);};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
const pose=p=>p.locator('.card-tilt').first().evaluate(e=>[e.style.transform,e.style.translate].join('|'));
const moving=async p=>{await p.waitForFunction(()=>Boolean(document.querySelector('.card-tilt')?.style.translate));const before=await pose(p);await p.waitForTimeout(350);return before!==await pose(p);};
const stopped=async p=>{await p.waitForTimeout(350);return p.locator('.card-tilt').first().evaluate(e=>!e.style.translate);};
const centered=p=>p.locator('#finish-toggle').evaluate(e=>{const b=e.getBoundingClientRect(),s=e.querySelector('svg').getBoundingClientRect();return Math.abs(b.x+b.width/2-s.x-s.width/2)<.5&&Math.abs(b.y+b.height/2-s.y-s.height/2)<.5;});
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addInitScript(()=>localStorage.setItem('forge-sound','off'));
 const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>requests.push(r.url()));await p.goto(base);await ready(p);await p.mouse.move(2,2);
 check('idle card moves when unattended',await moving(p));
 check('desktop sparkle is centered in its circle',await centered(p));
 await p.locator('#save-text').hover();check('hover below card keeps idle motion running',await moving(p));
 await p.locator('.art-edit-target').hover();check('hover on card stops idle motion',await stopped(p));
 await p.locator('#save-image').hover();check('leaving card for Save resumes idle motion',await moving(p));
 for(const kind of ['text','image','video']){
  await p.locator('#save-'+kind).click();await p.locator('.share-dialog').waitFor();
  if(kind!=='video')await p.locator(kind==='text'?'.share-dialog pre':'.share-dialog img').waitFor({timeout:60000});
  check(`${kind} dialog pauses idle animation`,await stopped(p));
  await p.getByRole('button',{name:'Close sharing'}).click();await p.locator('.share-dialog').waitFor({state:'detached'});await p.locator('#save-'+kind).hover();
  check(`${kind} close restores focus to Save`,await p.locator('#save-'+kind).evaluate(e=>document.activeElement===e));
  check(`${kind} close resumes idle without clicking jewel`,await moving(p));
 }
 await p.locator('#save-text').focus();await p.keyboard.press('Enter');await p.locator('.share-dialog pre').waitFor();await p.keyboard.press('Escape');await p.locator('.share-dialog').waitFor({state:'detached'});await p.locator('#save-text').hover();
 check('Escape keeps keyboard focus on Save',await p.locator('#save-text').evaluate(e=>document.activeElement===e&&e.matches(':focus-visible')));
 check('keyboard Save focus permits idle animation after Escape',await moving(p));
 await p.locator('#finish-toggle').focus();await p.mouse.move(2,2);check('keyboard focus on card controls pauses idle',await stopped(p));
 await p.locator('#save-text').click();await p.locator('.share-dialog pre').waitFor();await p.getByRole('button',{name:'Close sharing'}).click();await p.locator('#save-text').hover();
 // Dots represent the active carousel card; mouse focus below it must not freeze cycling.
 const current=await p.locator('#dots').innerHTML();
 await p.waitForFunction(previous=>document.querySelector('#dots').innerHTML!==previous,current,{timeout:14000});await ready(p);
 check('carousel advances with mouse focus left on Save',await p.locator('#dots').innerHTML()!==current);
 await p.locator('#pause-carousel').click();await p.mouse.move(2,2);check('explicit pause still stops idle',await stopped(p));
 await p.locator('#save-text').click();await p.locator('.share-dialog pre').waitFor();await p.keyboard.press('Escape');await p.mouse.move(2,2);check('closing Save preserves explicit pause',await stopped(p));
 await p.locator('#pause-carousel').click();await p.emulateMedia({reducedMotion:'reduce'});await p.mouse.move(2,2);check('reduced motion still stops idle',await stopped(p));
 await p.screenshot({path:`${out}/desktop.png`});await context.close();
 const phone=await browser.newContext({...devices['iPhone 13'],defaultBrowserType:undefined});const q=await phone.newPage();q.on('pageerror',e=>errors.push(e.message));await q.goto(base);await ready(q);
 check('mobile sparkle is centered in its circle',await centered(q));await q.screenshot({path:`${out}/mobile.png`});await phone.close();
 check('no model code or weights loaded',!requests.some(u=>/local-models\.js|\.gguf|\.safetensors|\.onnx/.test(u)));
 check('no JavaScript errors',errors.length===0);
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({base,checks,errors},null,2)+'\n');}
