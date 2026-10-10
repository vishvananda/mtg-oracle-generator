// Real renderer regression checks. Keyboard resize is simulated, not a physical iOS keyboard.
import {chromium,devices} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.FORGE_URL||'https://tetrarcum.com/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-polish';
await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[];
const check=(name,value)=>{assert.ok(value,name);checks.push(name);console.log('PASS',name);};
const settled=page=>page.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
const gate=()=>{let release;const promise=new Promise(r=>release=r);return {promise,release};};
async function sameType(page,field,source){
 return page.evaluate(({field,source})=>{
  const a=getComputedStyle(document.querySelector(field)),b=getComputedStyle(document.querySelector(source));
  const scale=new DOMMatrixReadOnly(a.transform).a;
  const matchLength=key=>a[key]==='normal'?b[key]==='normal':Math.abs(parseFloat(a[key])*scale-parseFloat(b[key]))<.02;
  return a.fontFamily===b.fontFamily&&a.fontWeight===b.fontWeight&&a.fontStyle===b.fontStyle&&matchLength('fontSize')&&matchLength('letterSpacing')&&matchLength('lineHeight')&&parseFloat(a.fontSize)>=16;
 },{field,source});
}
try{
 const context=await browser.newContext({...devices['iPhone 13']}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await settled(page);
 const original=await page.locator('.card-stage').boundingBox();
 await page.locator('#edit-own-card').click();
 check('mobile title matches printed font, size, line height and tracking',await sameType(page,'#edit-name','.rendered-card-title strong'));
 check('scaled input creates no viewport overflow',await page.evaluate(()=>document.documentElement.scrollWidth===innerWidth&&visualViewport.scale===1));
 check('pinch zoom remains enabled',await page.locator('meta[name="viewport"]').evaluate(e=>!/(?:maximum-scale|user-scalable)/.test(e.content)));
 await page.screenshot({path:`${out}/mobile-title.png`});
 await page.setViewportSize({width:390,height:390});
 const smaller=await page.locator('.card-stage').boundingBox();
 check('keyboard-sized viewport preserves card width and height',Math.abs(original.width-smaller.width)<.1&&Math.abs(original.height-smaller.height)<.1);
 check('title still matches after keyboard resize',await sameType(page,'#edit-name','.rendered-card-title strong'));
 await page.keyboard.press('Escape');
 check('card stays full size while the keyboard is closing',Math.abs((await page.locator('.card-stage').boundingBox()).width-original.width)<.1);
 await page.setViewportSize({width:390,height:664});await page.waitForFunction(()=>!document.querySelector('.card-stage').classList.contains('card-size-held'));
 check('normal responsive sizing resumes after the keyboard closes',true);
 await page.locator('[data-card-design-part="rules"]').click();
 check('rules input matches the printed font and size',await sameType(page,'#edit-oracle_text','.rendered-card-rule-line'));
 await page.screenshot({path:`${out}/mobile-rules.png`});await page.keyboard.press('Escape');
 await page.locator('.frame-edit-target').first().click();await page.locator('#edit-frame_style').selectOption('retro');await page.locator('#edit-save').click();await settled(page);
 await page.locator('[data-card-design-part="name"]').click();
 check('old-frame title keeps its Goudy font and metrics',await sameType(page,'#edit-name','.retro-title strong'));
 await page.keyboard.press('Escape');await context.close();

 const slow=await browser.newContext({viewport:{width:1280,height:900}}),gallery=await slow.newPage();gallery.on('pageerror',e=>errors.push(e.message));
 const initial=gate(),next=gate();
 await gallery.route('**/assets/dusk-wing.webp',async route=>{await initial.promise;await route.continue();});
 await gallery.goto(base,{waitUntil:'domcontentloaded'});
 await gallery.locator('.preview-rendering .rendered-card-face').waitFor();
 check('first card is blank while its artwork loads',await gallery.locator('.preview-rendering').evaluate(e=>getComputedStyle(e).opacity==='0'));
 check('no fallback copy is exposed on first load',await gallery.locator('.preview-last-card').count()===0);
 initial.release();await settled(gallery);
 await gallery.route('**/assets/tide-archivist.webp',async route=>{await next.promise;await route.continue();});
 await gallery.locator('#next').click();await gallery.locator('.preview-last-card').waitFor();
 check('previous card remains visible while the next loads',(await gallery.locator('.preview-last-card .rendered-card-title').innerText()).includes('Vesper'));
 check('retained preview includes its rendered foil pixels',await gallery.locator('.preview-last-card canvas').evaluate(e=>e.getContext('2d').getImageData(0,0,e.width,e.height).data.some((v,i)=>i%4===3&&v>0)));
 check('new fallback card remains hidden',await gallery.locator('.preview-rendering').evaluate(e=>getComputedStyle(e).opacity==='0'));
 await gallery.screenshot({path:`${out}/previous-during-load.png`});
 await gallery.locator('#next').click();await settled(gallery);
 check('rapid navigation shows the latest requested card',(await gallery.locator('.rendered-card-title').innerText()).includes('Crownroot'));
 next.release();await gallery.waitForTimeout(250);
 check('late assets do not replace the newer card',(await gallery.locator('.rendered-card-title').innerText()).includes('Crownroot')&&await gallery.locator('.preview-last-card').count()===0);
 await gallery.locator('[data-card-design-part="rules"]').hover();
 check('editable areas use a soft highlight instead of dashed outlines',await gallery.locator('[data-card-design-part="rules"]').evaluate(e=>getComputedStyle(e).outlineStyle==='none'&&getComputedStyle(e).backgroundColor!=='rgba(0, 0, 0, 0)'));
 await gallery.keyboard.press('Tab');
 check('keyboard controls keep a visible focus indicator',await gallery.evaluate(()=>getComputedStyle(document.activeElement).outlineStyle==='solid'));
 await slow.close();
 check('no uncaught JavaScript errors',errors.length===0);
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({url:base,checks,errors,limits:'Chromium mobile emulation and simulated viewport resize; physical iOS keyboard not exercised.'},null,2)+'\n');}
