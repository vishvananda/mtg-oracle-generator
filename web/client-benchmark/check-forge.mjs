// UI integration check. Model inference is replaced only in the final test context.
import {chromium,devices} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.FORGE_URL||'https://tetrarcum.com/';
const out=process.env.FORGE_EVIDENCE||'/tmp/forge-browser-check';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined,headless:true,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const receipt={url:base,checks:[],inference:'UI fixture only; real GPU inference is not claimed'};
const check=(name,value)=>{assert.ok(value,name);receipt.checks.push(name);console.log('PASS',name);};
const errors=[];
async function settled(page){await page.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');}
async function pageFor(options={}){const context=await browser.newContext(options);const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto(base);await page.locator('.card-design-preview').waitFor();await settled(page);return {page,context};}
async function stored(page){return page.evaluate(async()=>{const mod=await import(new URL('./storage.js',location.href));return (await mod.savedCards()).map(c=>({...c,art_blob:c.art_blob?{size:c.art_blob.size,type:c.art_blob.type}:null}));});}
try{
  const {page,context}=await pageFor({viewport:{width:1440,height:1000}});
  await page.locator('.rendered-card-art-etching').waitFor();
  check('demo artwork has a derived etching mask',await page.locator('.rendered-card-art-etching').count()===1);
  check('Forge symbol appears on the type line',(await page.locator('.rendered-card-set-mark').getAttribute('src')).startsWith('data:image/svg+xml,'));
  check('Forge symbol is used for the foil stamp',await page.locator('.etched-set-mark-mask').count()===1);
  check('page is named MTG CardForge and has no outgoing navigation',await page.title()==='MTG CardForge'&&await page.locator('a').count()===1);
  check('desktop can edit before loading models',await page.locator('#edit-own-card').isVisible()&&await page.locator('#load-button').isVisible());
  check('initial renderer imports no model runtime',await page.evaluate(()=>!performance.getEntriesByType('resource').some(e=>/local-models|wllama|\.wasm|\.gguf|bonsai-worker/.test(e.name))));
  check('music is not downloaded before interaction',await page.evaluate(()=>!performance.getEntriesByType('resource').some(e=>e.name.includes('opening.m4a'))));
  await page.screenshot({path:`${out}/desktop.png`});
  for(const [width,height]of [[1440,900],[1366,768],[1024,640]]){await page.setViewportSize({width,height});check(`card and controls fit ${width}x${height}`,await page.evaluate(()=>document.querySelector('.card-tools').getBoundingClientRect().bottom<=innerHeight&&document.documentElement.scrollHeight<=innerHeight+1));}
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('#next').click();await settled(page);check('carousel shows a planeswalker',await page.locator('#card-caption').innerText()==='Neris, Keeper of Lost Tides');
  check('standalone rules accessibility copy is visually clipped',await page.locator('.rendered-special-sr').first().evaluate(e=>e.getBoundingClientRect().width===1&&getComputedStyle(e).overflow==='hidden'));
  check('loyalty costs fit inside their badges',await page.locator('.rendered-special-badge').first().evaluate(e=>parseFloat(getComputedStyle(e).fontSize)/e.getBoundingClientRect().height<.56));
  await page.screenshot({path:`${out}/planeswalker.png`});await page.locator('#next').click();await settled(page);check('carousel navigation',await page.locator('#card-caption').innerText()==='Crownroot, Spring Eternal');
  await page.waitForFunction(()=>document.querySelector('audio')?.readyState>=2);
  check('opening theme plays after interaction',await page.evaluate(()=>!document.querySelector('audio').paused));
  await page.locator('#sound-toggle').click();check('sound toggle mutes music',await page.evaluate(()=>document.querySelector('audio').paused));
  await page.locator('#edit-own-card').click();await page.locator('#edit-name').fill('My First Creation');await page.keyboard.press('Enter');await settled(page);
  check('manual name edit appears in carousel',await page.locator('#card-caption').innerText()==='My First Creation');
  await page.locator('[data-card-design-part="name"]').click();
  check('name edits inside the card without a popup',await page.locator('.card-tilt #edit-name').isVisible()&&!(await page.locator('#edit-panel').isVisible()));
  check('editing freezes card tilt',await page.locator('.card-tilt').evaluate(e=>getComputedStyle(e).transform)==='none');
  await page.locator('#edit-name').fill('Discard this edit');await page.keyboard.press('Escape');
  check('Escape restores the previous name',await page.locator('#card-caption').innerText()==='My First Creation');
  await page.locator('[data-card-design-part="rules"]').click();
  await page.locator('#edit-oracle_text').fill('Flying\nWhenever CARDNAME attacks, draw a card.');
  await page.keyboard.press('Enter');await page.keyboard.insertText('Vigilance');
  await page.screenshot({path:`${out}/inline-rules.png`});
  check('rules editing supports real multiline text',await page.locator('#edit-oracle_text').inputValue()==='Flying\nWhenever CARDNAME attacks, draw a card.\nVigilance');
  await page.keyboard.press('Control+Enter');
  check('rules save on the card',(await stored(page))[0].oracle_text.endsWith('\nVigilance'));
  await page.locator('[data-card-design-part="name"]').click();await page.locator('#edit-name').fill('A Temporary Name');
  await page.locator('[data-card-design-part="rules"]').click();await page.locator('#edit-oracle_text').waitFor();
  check('switching fields saves and opens the next inline edit',await page.locator('#card-caption').innerText()==='A Temporary Name'&&await page.locator('#edit-oracle_text').isVisible());
  await page.keyboard.press('Escape');await page.locator('[data-card-design-part="name"]').click();await page.locator('#edit-name').fill('My First Creation');
  await page.locator('#card-details').click();await page.locator('#edit-panel').waitFor();
  check('clicking outside saves and opens the requested control',await page.locator('#card-caption').innerText()==='My First Creation'&&await page.locator('#edit-panel').isVisible());
  await page.keyboard.press('Escape');

  await page.locator('.artist-edit-target').click();await page.locator('#edit-artist').fill('Forge Tester');await page.locator('#edit-save').click();await settled(page);
  check('artist credit is directly editable',await page.locator('.rendered-card-footer-artist').innerText()==='Forge Tester');
  await page.locator('.set-edit-target').click();await page.getByRole('group',{name:'Set and rarity scroller'}).waitFor();await page.getByRole('group',{name:'Set and rarity scroller'}).pressSequentially('kaldheim');await page.getByRole('button',{name:'Save edit',exact:true}).click();await settled(page);
  check('searchable set picker updates card symbol',(await page.locator('.rendered-card-set-mark').getAttribute('src')).endsWith('/khm.svg'));
  check('chosen set is saved with the card',(await stored(page))[0].set_code==='KHM');
  await page.locator('[data-card-design-part="mana"]').click();
  while(await page.locator('.direct-peer[aria-label^="Edit mana symbol"]').count())await page.getByRole('button',{name:'Remove symbol',exact:true}).click();
  for(const symbol of ['U','B','1']){await page.getByRole('button',{name:'Add mana symbol',exact:true}).click();const wheel=page.getByRole('group',{name:'Mana symbol scroller'});if(symbol!=='1')for(let i=0;i<(symbol==='U'?31:32);i++)await wheel.press('ArrowDown');}
  await page.getByRole('button',{name:'Save edit',exact:true}).click();await settled(page);
  check('scrollable mana symbols build and order the saved cost',(await stored(page))[0].mana_cost==='{1}{U}{B}');
  await page.locator('.frame-edit-target').focus();await page.keyboard.press('Enter');await page.getByRole('button',{name:'Old frame',exact:true}).click();await settled(page);await page.locator('.rendered-layout-retro .retro-frame').waitFor();
  check('art-frame click switches to the new renderer’s old frame',await page.locator('.rendered-layout-retro').count()===1);
  await page.locator('.border-edit-target').first().click();await page.getByRole('button',{name:'White',exact:true}).click();await settled(page);await page.locator('.retro-border').waitFor();
  check('outer-border click selects white stock',(await stored(page))[0].border_color==='white');
  await page.screenshot({path:`${out}/retro-white.png`});
  await page.locator('#finish').selectOption('vizier_cold_v1');await settled(page);
  check('Cold foil is available and saved',(await stored(page))[0].finish_id==='vizier_cold_v1');
  await page.locator('#finish').selectOption('vizier_etched_v1');await settled(page);
  await page.locator('.frame-edit-target').focus();await page.keyboard.press('Enter');await page.getByRole('button',{name:'Modern',exact:true}).click();await settled(page);
  check('frame can switch back to modern',await page.locator('.rendered-layout-retro').count()===0);
  await page.locator('.art-edit-target').click();check('artwork URL and file available before loading',await page.locator('#edit-art_url').isVisible()&&await page.locator('#edit-art_file').isVisible());
  let artworkURL=new URL('./assets/dusk-wing.webp',base).href;
  // A localhost probe still exercises the HTTPS-only import rule with real image bytes.
  if(base.startsWith('http://127.0.0.1')){
    const source=await context.request.get(artworkURL);artworkURL='https://forge-test.invalid/dusk-wing.webp';
    await page.route(artworkURL,async route=>route.fulfill({contentType:'image/webp',headers:{'access-control-allow-origin':'*'},body:await source.body()}));
  }
  await page.locator('#edit-art_url').fill(artworkURL);await page.locator('#edit-artist').fill('');await page.locator('#edit-save').click();await settled(page);await page.locator('#edit-panel').waitFor({state:'hidden'});
  let saved=await stored(page);check('URL artwork is stored as a validated image blob',saved[0].art_blob.size>0&&saved[0].artist==='');
  await page.reload();await page.locator('.card-design-preview').waitFor();await settled(page);check('edits persist without models',await page.locator('#card-caption').innerText()==='My First Creation'&&(await stored(page))[0].oracle_text.endsWith('\nVigilance'));
  check('mute preference survives reload',await page.locator('#sound-toggle').innerText()==='Sound off');
  await page.locator('.art-edit-target').click();await page.locator('#edit-art_file').setInputFiles({name:'bad.png',mimeType:'image/png',buffer:Buffer.from('not an image')});await page.locator('#edit-save').click();await settled(page);await page.waitForFunction(()=>document.querySelector('#edit-error').textContent.includes('not a supported image'));check('invalid artwork keeps the editor open',await page.locator('#edit-panel').isVisible());await page.keyboard.press('Escape');
  const download=page.waitForEvent('download');await page.locator('#export-card').click();const file=await download;check('manual card export works',file.suggestedFilename()==='my-first-creation.json');
  await context.close();

  const mobile=await pageFor({...devices['iPhone 13'],defaultBrowserType:undefined});
  check('mobile shows carousel and edit button',await mobile.page.locator('#edit-own-card').isVisible());
  check('mobile hides model loading',!(await mobile.page.locator('#load-button').isVisible()));
  check('mobile has no horizontal overflow',await mobile.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.page.screenshot({path:`${out}/mobile.png`,fullPage:true});
  await mobile.page.locator('#edit-own-card').click();
  check('mobile focuses its inline name field without a popup',await mobile.page.locator('#edit-name').evaluate(e=>document.activeElement===e&&parseFloat(getComputedStyle(e).fontSize)>=16)&&!(await mobile.page.locator('#edit-panel').isVisible()));
  await mobile.page.screenshot({path:`${out}/mobile-inline-name.png`,fullPage:true});
  await mobile.page.locator('#edit-name').fill('Mobile Creation');await mobile.page.getByRole('button',{name:'Save card text',exact:true}).click();await mobile.page.locator('.art-edit-target').click();await mobile.page.locator('#edit-art_url').waitFor();
  check('mobile art editor uses URL or file',await mobile.page.locator('#edit-art_url').isVisible()&&!(await mobile.page.locator('#edit-art_prompt').count()));
  await mobile.page.screenshot({path:`${out}/mobile-art-editor.png`,fullPage:true});
  check('mobile never requests model code or weights',await mobile.page.evaluate(()=>!performance.getEntriesByType('resource').some(e=>/local-models|wllama|\.wasm|\.gguf|bonsai-worker/.test(e.name))));
  await mobile.context.close();

  const fixture=await browser.newContext({viewport:{width:1440,height:1000}});
  await fixture.route('**/local-models.js*',route=>route.fulfill({contentType:'text/javascript',body:`export class LocalModels {
    ready=false; async load(report){report('Fixture models ready',1);this.ready=true;}
    async card(){await new Promise(r=>setTimeout(r,200));return {seed:42,text:JSON.stringify({name:'Fixture Phoenix',mana_cost:'{3}{R}',type_line:'Creature — Phoenix',oracle_text:'Flying',colors:['R'],rarity:'rare',power:'3',toughness:'3'})};}
    async art(prompt,report){report('Fixture artwork');await new Promise((resolve,reject)=>{this.reject=reject;setTimeout(resolve,600);});return {seed:43,blob:await fetch(new URL('./assets/ember-giant.webp',location.href)).then(r=>r.blob()),width:768,height:512,steps:4,total_ms:600};}
    cancel(){this.reject?.(new DOMException('Stopped','AbortError'));} dispose(){}
  }`}));
  const generated=await fixture.newPage();generated.on('pageerror',e=>errors.push(e.message));await generated.goto(base);await generated.locator('.card-design-preview').waitFor();await settled(generated);await generated.locator('#load-button').click();await generated.locator('#description').waitFor();await generated.locator('#generate-button').click();await generated.locator('#card-busy').waitFor();await generated.locator('#card-busy').waitFor({state:'hidden'});
  check('fixture generation replaces demo with designed card and artwork',(await stored(generated))[0].art_blob.size>0&&await generated.locator('#card-caption').innerText()==='Fixture Phoenix');
  await generated.locator('.art-edit-target').click();await generated.locator('#edit-art_prompt').waitFor();check('loaded models expose editable art prompt',await generated.locator('#edit-art_prompt').isVisible());await generated.locator('#edit-art_prompt').fill('A phoenix over a mountain.');await generated.locator('#edit-save').click();await generated.locator('#card-busy').waitFor({state:'hidden'});
  saved=await stored(generated);check('art regeneration preserves rules and records new prompt',saved[0].oracle_text==='Flying'&&saved[0].art_prompt==='A phoenix over a mountain.');
  await generated.locator('#generate-button').click();await generated.locator('#card-busy').waitFor({state:'hidden'});check('another generation appends to carousel',(await stored(generated)).length===2);
  await generated.locator('#generate-button').click();await generated.waitForTimeout(350);await generated.locator('#stop-button').click();await generated.locator('#card-busy').waitFor({state:'hidden'});check('cancel retains finished text',await generated.locator('#message').innerText()==='Stopped. Any completed card text has been kept.');
  await fixture.close();
  check('all tested flows have no uncaught JavaScript errors',errors.length===0);
} finally {await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({...receipt,errors},null,2)+'\n');}
