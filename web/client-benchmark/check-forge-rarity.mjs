import {chromium,webkit,devices} from 'playwright';
import {mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const engine=process.env.FORGE_BROWSER||'chromium',base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-rarity';
await mkdir(out,{recursive:true});
const browser=await (engine==='webkit'?webkit:chromium).launch(engine==='webkit'?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],samples={};const check=(name,value)=>{assert.ok(value,name);checks.push(name);console.log('PASS',name);};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
async function prepare(options={}){
  const c=await browser.newContext(options);await c.addInitScript(()=>localStorage.setItem('forge-sound','off'));
  const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await ready(p);
  await p.locator('.set-edit-target').click();const wheel=p.locator('.set-wheel');await wheel.waitFor();await p.mouse.move(5,5);
  while(Number(await wheel.getAttribute('data-column'))>1)await wheel.press('ArrowLeft');
  const box=await wheel.boundingBox();return {p,c,wheel,center:{x:box.x+box.width/2,y:box.y+box.height/2}};
}
const state=wheel=>wheel.evaluate(e=>({x:Number(e.dataset.column),y:Number(e.dataset.row),speed:Number(e.dataset.speed||0),target:Number(e.dataset.targetSpeed||0),progress:Number(e.dataset.progress||0),translate:e.querySelector('[aria-pressed=true]').style.translate}));
try{
  const {p,c,wheel,center}=await prepare({viewport:{width:1440,height:900}}),initial=await state(wheel);
  await p.mouse.move(center.x+6,center.y);await p.waitForTimeout(400);
  check('small horizontal movements keep the current rarity',JSON.stringify(await state(wheel))===JSON.stringify(initial));
  await p.mouse.move(center.x+20,center.y);await p.waitForTimeout(180);samples.near=await state(wheel);
  check('rarity moves fractionally before its first snap',samples.near.x===initial.x&&samples.near.progress>0&&samples.near.progress<.5&&samples.near.translate!=='');
  check('speed starts below its target and ramps up',samples.near.speed>0&&samples.near.speed<samples.near.target);
  const namesBefore=await wheel.locator('.scroll-set-name').evaluateAll(els=>els.map(e=>e.getBoundingClientRect().x));
  await p.mouse.move(center.x+39,center.y);await p.waitForTimeout(100);samples.farEarly=await state(wheel);await p.waitForTimeout(180);samples.farLater=await state(wheel);
  check('moving farther sideways gradually increases speed',samples.farLater.target>samples.near.target&&samples.farLater.speed>samples.farEarly.speed&&samples.farLater.speed<=2.4);
  check('horizontal movement preserves the chosen set',samples.farLater.y===initial.y);
  const namesAfter=await wheel.locator('.scroll-set-name').evaluateAll(els=>els.map(e=>e.getBoundingClientRect().x));
  check('set names stay still while rarity slides',namesBefore.every((x,i)=>Math.abs(x-namesAfter[i])<1));
  await p.mouse.move(center.x+39,center.y+9);await p.waitForTimeout(450);
  check('small diagonal drift stays on the rarity axis',(await state(wheel)).y===initial.y);
  await p.mouse.move(center.x,center.y);await p.waitForTimeout(220);const stopped=await state(wheel);await p.waitForTimeout(350);
  check('returning to center snaps and stops',stopped.progress===0&&stopped.speed===0&&(await state(wheel)).x===stopped.x);
  check('a held sideways movement advances rarity',stopped.x>initial.x);
  while(Number(await wheel.getAttribute('data-column'))>2)await wheel.press('ArrowLeft');
  const reverseStart=await state(wheel);await p.mouse.move(center.x-39,center.y);await p.waitForTimeout(700);await p.mouse.move(5,5);await p.waitForTimeout(200);
  check('leftward movement reverses rarity without changing set',(await state(wheel)).x<reverseStart.x&&(await state(wheel)).y===initial.y);
  while(Number(await wheel.getAttribute('data-column'))>1)await wheel.press('ArrowLeft');
  while(Number(await wheel.getAttribute('data-column'))<1)await wheel.press('ArrowRight');
  // A real trackpad gesture includes vertical jitter. Keep its initial axis and
  // accumulate small deltas rather than throwing them away after each threshold.
  await wheel.evaluate(e=>{for(const [deltaX,deltaY]of [[12,2],[8,11],[12,3],[18,1]])e.dispatchEvent(new WheelEvent('wheel',{deltaX,deltaY,bubbles:true,cancelable:true}));});
  check('trackpad motion shows partial progress',(await state(wheel)).progress>.5);
  await p.waitForTimeout(230);const trackpad=await state(wheel);
  check('trackpad gesture snaps one rarity despite diagonal noise',trackpad.x===2&&trackpad.y===initial.y&&trackpad.progress===0);
  await p.screenshot({path:`${out}/rarity.png`});await p.getByRole('button',{name:'Save edit',exact:true}).click();await ready(p);
  check('selected rarity persists',await p.evaluate(async()=>{const {savedCards}=await import(new URL('./storage.js',location.href));return (await savedCards())[0].rarity==='rare';}));
  await c.close();
  if(engine==='chromium'){
    const mobile=await prepare({...devices['iPhone 13']}),cd=await mobile.c.newCDPSession(mobile.p),{x,y}=mobile.center;
    const touches=(x,y)=>[{id:1,x,y,radiusX:10,radiusY:10}];
    await cd.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:touches(x,y)});
    await cd.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:touches(x+43,y+4)});await mobile.p.waitForTimeout(750);
    await cd.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await mobile.p.waitForTimeout(220);const lifted=await state(mobile.wheel);
    check('touch moves rarity horizontally and settles on release',lifted.x>1&&lifted.y===initial.y&&lifted.progress===0);
    await mobile.p.waitForTimeout(350);check('lifting the finger stops movement',(await state(mobile.wheel)).x===lifted.x);
    await mobile.c.close();
  }
  check('no JavaScript errors',errors.length===0);
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({engine,base,checks,samples,errors},null,2)+'\n');}
