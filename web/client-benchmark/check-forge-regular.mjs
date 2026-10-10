// Check Crownroot's regular-frame geometry at phone and keyboard viewport sizes.
import {chromium,webkit,devices} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const engine=process.env.FORGE_BROWSER||'chromium';
const base=process.env.FORGE_URL||'https://tetrarchs.com/forge/';
const out=process.env.FORGE_EVIDENCE||'/tmp/forge-regular';
await mkdir(out,{recursive:true});
const browser=await (engine==='webkit'?webkit:chromium).launch(engine==='webkit'?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const context=await browser.newContext({...devices['iPhone 13']}),page=await context.newPage();
const checks=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));
// The Linux WebKit test container has no audio device. Presentation stays real.
await page.addInitScript(()=>localStorage.setItem('forge-sound','off'));
const settled=()=>page.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
const geometry=()=>page.evaluate(()=>{
 const face=document.querySelector('.rendered-card-face'),bounds=face.getBoundingClientRect();
 return Object.fromEntries(['background','frame','crown','title','type','art','rules','stats'].map(part=>{
  const rect=face.querySelector(`.rendered-card-${part}`).getBoundingClientRect();
  return [part,[(rect.x-bounds.x)/bounds.width,(rect.y-bounds.y)/bounds.height,rect.width/bounds.width,rect.height/bounds.height]];
 }));
});
const near=(actual,expected)=>Object.entries(actual).every(([part,rect])=>rect.every((v,i)=>Math.abs(v-expected[part][i])<.001));
try{
 await page.goto(base);await settled();await page.locator('#next').click();await page.locator('#next').click();await settled();
 assert.equal(await page.locator('#card-caption').innerText(),'Crownroot, Spring Eternal');
 const etched=await geometry();await page.screenshot({path:`${out}/green-etched.png`});
 await page.locator('#finish-toggle').click();await page.locator('[data-finish="'+'ordinary'+'"]').click();await settled();
 assert.ok(near(await geometry(),etched));checks.push('Crownroot frame rectangles remain aligned when switching etched to regular');
 assert.equal(await page.locator('.rendered-card-face').evaluate(e=>getComputedStyle(e).opacity),'1');
 checks.push('Regular shows the native card face');
 for(const [width,height] of [[390,664],[430,932],[390,390],[390,664]]){
  await page.setViewportSize({width,height});await page.waitForTimeout(150);
  assert.ok(near(await geometry(),etched));checks.push(`Regular geometry survives viewport ${width}x${height}`);
  assert.ok(await page.evaluate(()=>{
    for(const parent of document.querySelectorAll('.rendered-image-slice,.rendered-image-plate')){
      const image=parent.querySelector('img');if(!image?.naturalWidth)return false;
      const a=parent.getBoundingClientRect(),b=image.getBoundingClientRect();
      if(Math.abs(a.width-b.width)>.2 || Math.abs(b.height/b.width-image.naturalHeight/image.naturalWidth)>.005)return false;
      if(parent.classList.contains('rendered-card-type')&&Math.abs(a.bottom-b.bottom)>.2)return false;
    }return true;
  }));checks.push(`Frame image slices preserve intrinsic proportions and bottom alignment at ${width}x${height}`);
  await page.screenshot({path:`${out}/green-regular-${width}-${height}.png`});
 }
 assert.deepEqual(errors,[]);checks.push('No uncaught JavaScript errors');
}finally{
 await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({engine,url:base,checks,errors,limitations:'Browser emulation, not a physical iPhone. Geometry checks do not establish pixel equivalence between DOM and foil canvas.'},null,2)+'\n');
}
console.log(`${checks.length} ${engine} regular-frame checks passed.`);
