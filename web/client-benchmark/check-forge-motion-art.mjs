import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,devices} from 'playwright';
const base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-motion-art';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],check=(name,value)=>{assert.ok(value,name);checks.push(name);console.log('PASS',name);};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
const pose=p=>p.locator('.card-tilt').first().evaluate(e=>[e.style.transform,e.style.translate,e.style.getPropertyValue('--foil-pointer-x'),e.style.getPropertyValue('--foil-pointer-y')].join('|'));
const release=p=>p.evaluate(()=>document.activeElement?.blur());
try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}});await context.addInitScript(()=>localStorage.setItem('forge-sound','off'));
 const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await ready(p);await p.mouse.move(2,2);
 await p.waitForTimeout(900);const a=await pose(p);await p.waitForTimeout(500);check('unattended card drifts, tilts and updates foil lighting',a!==await pose(p)&&await p.locator('.card-tilt').first().evaluate(e=>Boolean(e.style.translate)));
 await p.locator('#finish-toggle').hover();await p.waitForTimeout(150);const still=await pose(p);await p.waitForTimeout(500);check('hovering the card controls stops idle motion',still===await pose(p));
 await p.locator('#finish-toggle').click();check('sparkle opens direct finish choices beside card',await p.locator('#finish-panel').isVisible()&&await p.locator('#finish-panel button').count()>=5);
 await p.screenshot({path:`${out}/finish-picker.png`});
 await p.keyboard.press('Escape');check('Escape closes finishes and returns focus',await p.locator('#finish-panel').isHidden()&&await p.locator('#finish-toggle').evaluate(e=>document.activeElement===e));
 await p.mouse.move(2,2);await p.waitForTimeout(600);check('keyboard focus keeps the card still',!(await p.locator('.card-tilt').first().evaluate(e=>Boolean(e.style.translate))));
 await release(p);await p.waitForTimeout(600);check('idle motion resumes after interaction ends',await p.locator('.card-tilt').first().evaluate(e=>Boolean(e.style.translate)));
 await p.locator('#pause-carousel').click();await release(p);await p.mouse.move(2,2);await p.waitForTimeout(400);check('pause carousel also pauses drift',!(await p.locator('.card-tilt').first().evaluate(e=>Boolean(e.style.translate))));
 for(const [id,finish]of [['demo-scholar','vizier_prismatic_v1'],['demo-stag','vizier_opal_v1'],['demo-observatory','vizier_cold_v1']]){
  await p.locator('#next').click();await ready(p);check(`${id} opens with ${finish}`,await p.locator('.premium-card').first().getAttribute('data-premium-finish')===finish);
 }
 await p.locator('#finish-toggle').click();await p.locator('[data-finish="ordinary"]').click();await ready(p);
 check('one click applies a finish and closes the palette',await p.locator('#finish-panel').isHidden()&&await p.locator('.premium-card').first().getAttribute('data-premium-finish')==='ordinary');
 await p.reload();await ready(p);check('finish choice persists on the edited copy',await p.locator('#finish-toggle').getAttribute('title')==='Card finish: Regular');
 await p.emulateMedia({reducedMotion:'reduce'});await release(p);await p.mouse.move(2,2);await p.waitForTimeout(350);check('reduced motion disables idle drift',!(await p.locator('.card-tilt').first().evaluate(e=>Boolean(e.style.translate))));
 await context.close();
 const mobile=await browser.newContext({...devices['iPhone 13'],defaultBrowserType:undefined});const phone=await mobile.newPage();await phone.goto(base);await ready(phone);await phone.locator('#finish-toggle').tap();
 const bounds=await phone.locator('#finish-panel').boundingBox();check('phone finish choices stay within the viewport',bounds.x>=0&&bounds.x+bounds.width<=390&&await phone.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await phone.screenshot({path:`${out}/mobile-finishes.png`});await mobile.close();
 const fixture=await browser.newContext({viewport:{width:1440,height:1000}});await fixture.addInitScript(()=>localStorage.setItem('forge-sound','off'));
 await fixture.route('**/local-models.js*',r=>r.fulfill({contentType:'text/javascript',body:`export class LocalModels {
  ready=false;async load(){this.ready=true;}
  async art(prompt){await new Promise((resolve,reject)=>{this.reject=reject;this.timer=setTimeout(resolve,1800);});if(prompt==='fail')throw Error('Fixture generation failed');return {blob:await fetch(new URL('./assets/ember-giant.webp',location.href)).then(r=>r.blob()),seed:23,width:768,height:512,steps:8,total_ms:1800};}
  cancel(){clearTimeout(this.timer);this.reject?.(new DOMException('Stopped','AbortError'));}dispose(){}
 }`}));
 const q=await fixture.newPage();q.on('pageerror',e=>errors.push(e.message));await q.goto(base);await ready(q);await q.locator('#pause-carousel').click();await q.locator('#load-button').click();await q.locator('#create-form').waitFor();
 const artwork=()=>q.evaluate(async()=>{const {savedCards}=await import(new URL('./storage.js',location.href));const c=(await savedCards())[0];const bytes=await(c?.art_blob||await fetch(document.querySelector('.rendered-card-art img').src).then(r=>r.blob())).arrayBuffer();return {hash:[...new Uint8Array(await crypto.subtle.digest('SHA-256',bytes))].join(','),prompt:c?.art_prompt,artist:c?.artist,history:!!c?.previous_art};});
 // Start from an existing demo: adoption supplies its original image and credit.
 await q.locator('.art-edit-target').click();const originalPrompt=await q.locator('#edit-art_prompt').inputValue();
 const old=await artwork();await q.locator('#edit-art_prompt').fill('A red giant at his forge.');await q.locator('#edit-save').click();await q.locator('#card-busy').waitFor();
 await q.waitForFunction(()=>document.querySelector('#jewel-toggle').dataset.motion==='still'&&document.querySelector('#jewel-toggle img').src.includes('-still.webp'));
 check('jewel uses a still while artwork generation runs',await q.locator('#card-busy').isVisible());await q.locator('#card-busy').waitFor({state:'hidden'});await ready(q);
 await q.waitForFunction(()=>document.querySelector('#jewel-toggle').dataset.motion==='spinning'&&!document.querySelector('#jewel-toggle img').src.includes('-still.webp'));
 check('jewel resumes after successful generation',true);const fresh=await artwork();check('regeneration keeps previous artwork and changes current image',fresh.hash!==old.hash&&fresh.history&&await q.locator('#restore-art').isVisible());
 await q.reload();await ready(q);check('previous artwork survives reload',await q.locator('#restore-art').isVisible());await q.locator('#restore-art').click();await ready(q);const restored=await artwork();
 check('Restore brings back original art, prompt and credit',restored.hash===old.hash&&restored.prompt===originalPrompt&&restored.artist==='MTG CardForge'&&!restored.history);
 await q.locator('#load-button').click();await q.locator('#create-form').waitFor();
 for(const outcome of ['fail','cancel']){
  await q.locator('.art-edit-target').click();await q.locator('#edit-art_prompt').fill(outcome);await q.locator('#edit-save').click();await q.locator('#card-busy').waitFor();
  if(outcome==='cancel')await q.locator('#stop-button').click();await q.locator('#card-busy').waitFor({state:'hidden'});await ready(q);
  const kept=await artwork();check(`${outcome} preserves original art and prompt without false undo`,kept.hash===old.hash&&kept.prompt===originalPrompt&&!kept.history);
  await q.waitForFunction(()=>document.querySelector('#jewel-toggle').dataset.motion==='spinning');check(`${outcome} resumes jewel animation`,true);
 }
 check('no JavaScript errors',errors.length===0);await fixture.close();
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({base,checks,errors,inference:'Explicit artwork fixture; actual generation quality/speed not tested'},null,2)+'\n');}
