import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const engine=process.env.FORGE_BROWSER||'chromium',base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-carousel';
await mkdir(out,{recursive:true});
const browser=await (engine==='webkit'?webkit:chromium).launch(engine==='webkit'?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],requests=[];
const check=(name,value)=>{assert.ok(value,name);checks.push(name);console.log('PASS',name);};
const gate=()=>{let release;const promise=new Promise(r=>release=r);return {promise,release};};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
try{
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push(r.url()));
  await context.addInitScript(()=>localStorage.setItem('forge-sound','off'));
  const first=gate(),next=gate();
  await page.route('**/assets/dusk-wing.webp',async r=>{await first.promise;await r.continue();});
  await page.route('**/assets/tide-archivist.webp',async r=>{await next.promise;await r.continue();});
  await page.goto(base,{waitUntil:'domcontentloaded'});
  await page.locator('.preview-rendering .rendered-card-face').waitFor();
  check('initial card stays blank while artwork loads',await page.locator('.preview-rendering').evaluate(e=>getComputedStyle(e).opacity==='0'));
  first.release();await ready(page);await page.locator('#pause-carousel').click();
  // This must happen without selecting either neighbor. Their frames are derived
  // by the production renderer, not hardcoded guesses in the prefetcher.
  await page.waitForRequest('**/img/planeswalker_bg/U.webp');
  check('both neighboring artworks start before navigation',requests.some(u=>u.endsWith('/tide-archivist.webp'))&&requests.some(u=>u.endsWith('/ember-giant.webp')));
  check('unrelated cards are not eagerly downloaded',!requests.some(u=>u.endsWith('/grove-guardian.webp')));
  await page.waitForTimeout(350);
  check('preloading never adds visible or editable card copies',await page.locator('.card-design-preview').count()===1);
  await page.locator('#next').click();await page.locator('.preview-last-card').waitFor();
  check('snapshot preserves outgoing foil pixels',await page.locator('.preview-last-card canvas').evaluate(e=>e.getContext('2d').getImageData(0,0,e.width,e.height).data.some((v,i)=>i%4===3&&v>0)));
  for(const delay of [300,500,500]){
    await page.waitForTimeout(delay);
    check(`outgoing card stays faded while waiting (${delay}ms sample)`,await page.locator('.preview-last-card').evaluate(e=>Number(getComputedStyle(e).opacity)<.01));
    check(`incomplete incoming card stays hidden (${delay}ms sample)`,await page.locator('.preview-rendering').evaluate(e=>Number(getComputedStyle(e).opacity)===0));
  }
  await page.screenshot({path:`${out}/waiting.png`});
  // A second request must win while the older prefetch is still blocked.
  await page.locator('#next').click();await ready(page);await page.waitForTimeout(350);
  check('rapid navigation reveals only the latest requested card',(await page.locator('.rendered-card-title').innerText()).includes('Crownroot'));
  next.release();await page.waitForTimeout(500);
  check('late neighboring assets cannot restore a stale card',(await page.locator('.rendered-card-title').innerText()).includes('Crownroot')&&await page.locator('.preview-last-card').count()===0);
  await page.locator('#next').click();await ready(page);await page.waitForTimeout(350);
  check('legendary land gets the blue/red frame',await page.locator('.rendered-card-frame img').evaluate(e=>e.src.endsWith('/frames/UR.webp')));
  check('land retains its land panels',await page.locator('.rendered-card-background img').evaluate(e=>e.src.endsWith('/bg/Land.webp')));
  check('foil is ready before the land appears',await page.locator('.premium-card').evaluate(e=>e.hasAttribute('data-foil-webgl-face')||document.documentElement.classList.contains('foil-static-fallback')));
  await page.screenshot({path:`${out}/land-etched.png`});
  await page.locator('#finish').selectOption('ordinary');await ready(page);
  check('regular land uses the same blue/red frame',await page.locator('.rendered-card-frame img').evaluate(e=>e.src.endsWith('/frames/UR.webp')));
  check('editing the land preserves its actual colorless data',await page.evaluate(async()=>{const {savedCards}=await import(new URL('./storage.js',location.href));const land=(await savedCards())[0];return land.type_line==='Legendary Land'&&land.colors.length===0;}));
  await page.screenshot({path:`${out}/land-regular.png`});
  const colors=await page.evaluate(async()=>{
    const {frameColors}=await import(new URL('./cards.js',location.href));
    const land=oracle_text=>frameColors({type_line:'Land',colors:[],oracle_text}).join('');
    return [land('{U}{R}, {T}: Draw a card.'),land('{T}: Add {C}.'),land('{T}: Add {G} or {W}.'),land('{T}: Add one mana of any color.'),frameColors({type_line:'Land — Island Mountain',colors:[],oracle_text:''}).join(''),frameColors({type_line:'Creature',colors:['G'],oracle_text:'{T}: Add {R}.'}).join('')];
  });
  check('frame accents follow produced mana and land subtypes, not activation costs',JSON.stringify(colors)===JSON.stringify(['','','WG','WUBRG','UR','G']));
  await page.emulateMedia({reducedMotion:'reduce'});await page.locator('#browse-examples').click();await ready(page);
  await page.locator('#next').click();await ready(page);
  check('reduced motion has no card transition animations',await page.locator('#card-preview').evaluate(e=>e.getAnimations({subtree:true}).every(a=>a.playState!=='running')));
  check('no model runtime is fetched',!requests.some(u=>/local-models|\.gguf|\.wasm/.test(u)));
  check('no JavaScript errors',errors.length===0);
  await context.close();
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({engine,base,checks,errors},null,2)+'\n');}
