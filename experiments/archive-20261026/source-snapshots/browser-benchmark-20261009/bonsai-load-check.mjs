import {chromium} from '/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark/node_modules/playwright/index.mjs';
import {writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const out='/home/vishvananda/vizier-editor-artifacts/card-authoring/browser-benchmark-20261009';
const b=await chromium.launchPersistentContext(out+'/bonsai-chrome-profile',{executablePath:'/home/vishvananda/.local/bin/vizier-chrome',headless:true,viewport:{width:1280,height:1000},args:['--no-sandbox','--enable-unsafe-webgpu','--use-angle=swiftshader','--enable-features=Vulkan']});
const page=await b.newPage(),errors=[],requests=[];
page.on('pageerror',e=>errors.push(e.message));page.on('requestfailed',r=>errors.push(r.url()+' '+r.failure()?.errorText));page.on('request',r=>requests.push({method:r.method(),url:r.url(),range:r.headers()['range']}));
let timer;
try {
 await page.goto('https://tetrarchs.com/model-bench/#art-demo');await page.waitForFunction(()=>!document.querySelector('#load').disabled);
 assert.equal(await page.locator('#art-size').inputValue(),'512x512');
 await page.locator('#art-load').click();timer=setInterval(async()=>{try{console.log(await page.locator('#art-status').textContent());}catch{}},10000);
 await page.waitForFunction(()=>!document.querySelector('#art-generate').disabled||document.querySelector('#art-status').classList.contains('error'),null,{timeout:240000});
 const status=await page.locator('#art-status').textContent();assert(await page.locator('#art-generate').isEnabled(),status);
 const device=JSON.parse(await page.locator('#art-device').textContent());console.log(JSON.stringify({loaded:true,device}));
 await page.screenshot({path:out+'/bonsai-loaded.png',fullPage:true});
 await page.locator('#art-unload').click();await page.waitForFunction(()=>!document.querySelector('#art-load').disabled);assert(await page.locator('#art-generate').isDisabled());
 await writeFile(out+'/bonsai-load-validation.json',JSON.stringify({full_model_loaded:true,full_image_inference_tested:false,device,errors,requests,unloaded:true},null,2));
}catch(e){await writeFile(out+'/bonsai-load-error.json',JSON.stringify({error:e.stack,status:await page.locator('#art-status').textContent(),errors,requests},null,2));throw e;}finally{clearInterval(timer);await b.close();}
