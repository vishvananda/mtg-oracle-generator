import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';
import {chromium,webkit} from 'playwright';
const engine=process.env.FORGE_BROWSER||'chromium',base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-silver';
await mkdir(out,{recursive:true});
const browser=await (engine==='webkit'?webkit:chromium).launch(engine==='webkit'?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
const checks=[],errors=[],pixels={};const check=(name,value)=>{assert.ok(value,name);checks.push(name);console.log('PASS',name);};
const ready=p=>p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');
async function border(p,label){await p.locator('.border-edit-target').first().focus();await p.keyboard.press('Enter');await p.getByRole('button',{name:label,exact:true}).click();await ready(p);}
async function frame(p,label){await p.locator('.frame-edit-target').focus();await p.keyboard.press('Enter');await p.getByRole('button',{name:label,exact:true}).click();await ready(p);}
async function pixel(p){return p.locator('canvas.premium-foil-coating').evaluate(source=>{
 const c=document.createElement('canvas');c.width=source.width;c.height=source.height;const ctx=c.getContext('2d');ctx.drawImage(source,0,0);
 return [...ctx.getImageData(Math.round(c.width*.025),Math.round(c.height*.5),1,1).data];
});}
try{
 const context=await browser.newContext({viewport:{width:1280,height:900}}),p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));
 await p.addInitScript(()=>localStorage.setItem('forge-sound','off'));await p.goto(base);await ready(p);await p.locator('#pause-carousel').click();
 await p.locator('.border-edit-target').first().focus();await p.keyboard.press('Enter');
 check('border choices offer Match set, Black, White and Silver',await p.getByRole('button',{name:'Silver',exact:true}).isVisible()&&await p.getByRole('button',{name:'Black',exact:true}).isVisible()&&await p.getByRole('button',{name:'White',exact:true}).isVisible());
 await p.getByRole('button',{name:'Silver',exact:true}).click();await ready(p);
 check('silver applies immediately and closes the panel',!(await p.locator('#edit-panel').isVisible()));
 await p.locator('#finish').selectOption('ordinary');await ready(p);
 check('modern Regular paints silver stock with dark footer ink',await p.locator('.rendered-card-face').evaluate(e=>getComputedStyle(e).getPropertyValue('--renderer-stock-color').trim()==='#b8bbc2'&&getComputedStyle(e).getPropertyValue('--renderer-stock-ink').trim()==='#17140f'));
 await p.screenshot({path:`${out}/modern-regular.png`});
 for(const [layout,label]of [['modern','Modern'],['retro','Old frame']]){
  await frame(p,label);
  if(layout==='retro'){
   await p.waitForFunction(()=>document.querySelector('.retro-border')&&getComputedStyle(document.querySelector('.retro-border')).maskImage!=='none');
   check('old frame recolors its border mask silver',await p.locator('.retro-border').evaluate(e=>getComputedStyle(e).backgroundColor==='rgb(184, 187, 194)'));
   await p.screenshot({path:`${out}/retro-regular.png`});
  }
  for(const finish of ['vizier_prismatic_v1','vizier_etched_v1','vizier_cold_v1','vizier_opal_v1']){
   await p.locator('#finish').selectOption(finish);await ready(p);await p.waitForTimeout(100);
   const rgba=await pixel(p);pixels[`${layout}-${finish}`]=rgba;
   check(`${layout} ${finish} paints silver border pixels`,rgba[3]===255&&rgba.slice(0,3).every(n=>n>80));
  }
  await p.screenshot({path:`${out}/${layout}-foil.png`});await p.locator('#finish').selectOption('ordinary');await ready(p);
 }
 await p.reload();await ready(p);
 check('silver survives reload with the old frame',await p.evaluate(async()=>{const {savedCards}=await import(new URL('./storage.js',location.href));const card=(await savedCards())[0];return card.border_color==='silver'&&card.frame_style==='retro';}));
 await border(p,'White');check('white is still selectable',await p.locator('.rendered-card-face').evaluate(e=>getComputedStyle(e).getPropertyValue('--retro-white').trim()==='#eeedeb'));
 await border(p,'Black');check('black is still selectable',await p.locator('.rendered-card-face').evaluate(e=>getComputedStyle(e).getPropertyValue('--retro-white').trim()==='#080808'));
 await p.setViewportSize({width:320,height:780});await p.locator('.border-edit-target').first().focus();await p.keyboard.press('Enter');
 check('four border options fit a narrow phone viewport',await p.getByRole('button',{name:'Silver',exact:true}).evaluate(e=>e.getBoundingClientRect().right<=innerWidth&&e.getBoundingClientRect().left>=0));
 await p.getByRole('button',{name:'Silver',exact:true}).click();await ready(p);const download=p.waitForEvent('download');await p.locator('#export-card').click();const record=await download;const stream=await record.createReadStream();let json='';for await(const part of stream)json+=part;
 check('card export retains silver border',JSON.parse(json).border_color==='silver');
 await p.setViewportSize({width:1280,height:900});await p.locator('#browse-examples').click();await ready(p);
 await p.locator('#dots button').nth(1).click();await ready(p);
 const centered=()=>p.evaluate(()=>{
  const stamp=document.querySelector('.etched-set-mark').getBoundingClientRect(),rules=document.querySelector('.rendered-special-rules').getBoundingClientRect();
  return {deltaX:Math.abs(stamp.x+stamp.width/2-rules.x-rules.width/2),deltaY:Math.abs(stamp.y+stamp.height/2-rules.y-rules.height/2),inside:stamp.y>rules.y&&stamp.bottom<rules.bottom};
 });
 let alignment=await centered();check('three-ability walker stamp is centered in the rules panel',alignment.deltaX<1&&alignment.deltaY<1&&alignment.inside);
 await p.screenshot({path:`${out}/walker-three.png`});
 await p.locator('[data-card-design-part="rules"]').focus();await p.keyboard.press('Enter');
 await p.locator('#edit-oracle_text').fill('+1: Scry 2.\n0: Draw a card.\n−2: Return target nonland permanent to its owner’s hand.\n−7: Draw seven cards.');
 await p.locator('#edit-oracle_text').press('Control+Enter');await ready(p);
 alignment=await centered();check('four-ability walker stamp moves up with the taller rules panel',alignment.deltaX<1&&alignment.deltaY<1&&alignment.inside);
 await p.screenshot({path:`${out}/walker-four.png`});
 check('no JavaScript errors',errors.length===0);await context.close();
}finally{await browser.close();await writeFile(`${out}/receipt.json`,JSON.stringify({engine,base,checks,pixels,errors},null,2)+'\n');}
