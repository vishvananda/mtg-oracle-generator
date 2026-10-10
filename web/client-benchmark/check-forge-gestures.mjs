import {chromium,devices} from 'playwright';
import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-gestures';await mkdir(out,{recursive:true});
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});const checks=[];
const check=(name,ok)=>{assert.ok(ok,name);checks.push(name);console.log('PASS',name);};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
try{
 for(const mobile of [false,true]){
  const c=await browser.newContext(mobile?devices['iPhone 13']:{viewport:{width:1280,height:900}});await c.addInitScript(()=>localStorage.setItem('forge-sound','off'));const p=await c.newPage();await p.goto(base);await ready(p);await p.locator('#pause-carousel').click();await p.locator('#next').click();await ready(p);await p.locator('#next').click();await ready(p);await p.locator('[data-card-design-part="stats"]').click();const wheel=p.getByRole('group',{name:'power scroller'}),value=()=>wheel.locator('.scroll-status').innerText();
  if(!mobile){
   const box=await wheel.locator('.scroll-grid').boundingBox();await p.mouse.move(box.x+box.width/2,box.y+box.height/2);const sy=await p.evaluate(()=>scrollY);await p.mouse.wheel(0,120);await p.waitForTimeout(100);check('wheel increments power without scrolling the page',await value()==='5'&&await p.evaluate(()=>scrollY)===sy);
   await p.mouse.move(box.x+box.width/2,box.y+box.height-15);await p.waitForTimeout(1100);check('off-center mouse hover repeats in the intended direction',Number(await value())>5);await p.mouse.move(20,20);await p.waitForTimeout(50);const stopped=await value();await p.waitForTimeout(600);check(`moving away stops auto-scrolling (${stopped} to ${await value()})`,await value()===stopped);
   const b=await wheel.locator('.scroll-cell[data-dy="-1"]').boundingBox();await p.mouse.click(b.x+b.width/2,b.y+b.height/2);check('clicking a neighboring item snaps it to the center',Number(await value())===Number(stopped)-1);
  }else{
   const b=await wheel.locator('.scroll-grid').boundingBox(),before=Number(await value());const cd=await c.newCDPSession(p);
   const point=(x,y)=>[{x,y,id:1,radiusX:9,radiusY:9}];await cd.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:point(b.x+b.width/2,b.y+b.height/2)});await cd.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:point(b.x+b.width/2,b.y+b.height/2-65)});await cd.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});check('touch drag changes power and snaps once',Number(await value())===before+1);check('touch control has larger rows',b.height>=250);check('mobile picker stays inside viewport',await p.locator('.card-scroll-editor').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight;}));await p.screenshot({path:`${out}/mobile-scroller.png`});
  }
  await p.locator('.card-scroll-editor > footer').getByRole('button',{name:'Cancel',exact:true}).click();await ready(p);await c.close();
 }
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({base,checks,limits:'Touch events are emulated, not physical iPhone input.'},null,2));}
