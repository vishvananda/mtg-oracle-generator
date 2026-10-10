// Exercise the shipped images and interactions without downloading any model.
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,webkit,devices} from 'playwright';
const base=process.env.FORGE_URL||'https://tetrarchs.com/forge/';
const out=process.env.FORGE_EVIDENCE||'/tmp/forge-logo-check';
const isWebkit=process.env.FORGE_BROWSER==='webkit';
await mkdir(out,{recursive:true});
const browser=await (isWebkit?webkit:chromium).launch(isWebkit?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const receipt={url:base,browser:isWebkit?'webkit':'chromium',checks:[],errors:[]};
const check=(name,value)=>{assert.ok(value,name);receipt.checks.push(name);console.log('PASS',name);};
const url=kind=>{const u=new URL(base);if(kind)u.searchParams.set('logo',kind);return u.href;};
async function settled(page,kind,still=false){await page.waitForFunction(({kind,still})=>{
  const img=document.querySelector('#jewel-toggle img');return img?.complete&&img.naturalWidth>0&&new URL(img.src).pathname.endsWith(`jewel-${kind}${still?'-still':''}.webp`);
},{kind,still});}
const track=page=>page.on('pageerror',e=>receipt.errors.push(e.message));
const cardReady=page=>page.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
try{
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  await context.addInitScript(()=>localStorage.setItem('forge-sound','off'));
  const page=await context.newPage();track(page);const requests=[];page.on('request',r=>requests.push(r.url()));
  await page.goto(base);await settled(page,'dual');
  await cardReady(page);
  check('dual tetrahedron is the first-visit default',await page.locator('#jewel-toggle').getAttribute('data-design')==='dual');
  check('unused cube animation is not downloaded',!requests.some(u=>u.endsWith('/jewel-cube.webp')));
  check('logo imports no 3D engine or model runtime',!requests.some(u=>/three|logo-studio|local-models|\.wasm|\.gguf/.test(u)));
  check('logo creates no canvas or WebGL context',await page.locator('.brand canvas').count()===0);
  const first=await page.locator('#jewel-toggle img').screenshot();await page.waitForTimeout(800);
  check('the native WebP visibly rotates',!first.equals(await page.locator('#jewel-toggle img').screenshot()));
  await page.screenshot({path:`${out}/desktop-dual.png`});
  await page.locator('#jewel-toggle').click();await settled(page,'cube');
  check('click changes to the cube and keeps the card visible',await page.locator('.card-design-preview').isVisible());
  await page.screenshot({path:`${out}/desktop-cube.png`});
  await page.reload();await settled(page,'cube');await cardReady(page);check('choice persists across reloads',true);
  await page.goto(url('dual'));await settled(page,'dual');await cardReady(page);check('comparison URL overrides the stored choice',true);
  await page.locator('#jewel-toggle').focus();await page.keyboard.press('Space');await settled(page,'cube');
  await page.keyboard.press('Enter');await settled(page,'dual');
  check('Space and Enter switch the jewel accessibly',(await page.locator('#jewel-toggle').getAttribute('aria-label')).includes('Switch to cube'));
  await page.emulateMedia({reducedMotion:'reduce'});await settled(page,'dual',true);
  check('reduced motion switches to a still image',true);
  await page.emulateMedia({reducedMotion:'no-preference'});await settled(page,'dual');check('motion preference changes resume the spin',true);
  for(const [width,height]of [[1440,900],[1366,768],[1024,640]]){
    await page.setViewportSize({width,height});check(`desktop page still fits ${width}x${height}`,await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1));
  }
  await context.close();
  const mobile=await browser.newContext({...devices['iPhone 13'],defaultBrowserType:undefined,reducedMotion:'reduce'});
  await mobile.addInitScript(()=>localStorage.setItem('forge-sound','off'));
  const phone=await mobile.newPage();track(phone);const mobileRequests=[];phone.on('request',r=>mobileRequests.push(r.url()));
  await phone.goto(url('cube'));await settled(phone,'cube',true);
  await cardReady(phone);
  check('reduced-motion first visit downloads no animation',!mobileRequests.some(u=>/jewel-(cube|dual)\.webp$/.test(u)));
  await phone.locator('#jewel-toggle').tap();await settled(phone,'dual',true);check('touch switches designs without navigating',new URL(phone.url()).pathname===new URL(base).pathname);
  for(const width of [390,320]){
    await phone.setViewportSize({width,height:844});
    check(`mobile header fits ${width}px without overlap`,await phone.evaluate(()=>{
      const brand=document.querySelector('.brand').getBoundingClientRect(),sound=document.querySelector('#sound-toggle').getBoundingClientRect();
      return brand.right+5<=sound.left&&document.documentElement.scrollWidth<=innerWidth;
    }));
    await phone.screenshot({path:`${out}/mobile-${width}.png`,fullPage:true});
  }
  check('no uncaught JavaScript errors',receipt.errors.length===0);
  await mobile.close();
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify(receipt,null,2)+'\n');}
