import { chromium } from '/home/vishvananda/vizier-editor/mtg-oracle-generator/web/client-benchmark/node_modules/playwright/index.mjs';
import { writeFile } from 'node:fs/promises';
const root='/home/vishvananda/vizier-editor-artifacts/card-authoring/browser-benchmark-20261009';
const results=[];
for(const threads of [1,8]){
 const b=await chromium.launchPersistentContext(root+'/test/chrome-profile',{executablePath:'/home/vishvananda/.local/bin/vizier-chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 const p=await b.newPage();
 await p.route('**/system-prompt.txt',r=>r.fulfill({status:200,contentType:'text/plain',body:'Return a JSON object with one field: greeting.'}));
 await p.goto('https://tetrarchs.com/model-bench/');
 await p.waitForFunction(()=>!document.querySelector('#load').disabled);
 await p.locator('#backend').selectOption('cpu');await p.locator('#context').selectOption('2048');await p.locator('#threads').fill(String(threads));await p.locator('#load').click();
 await p.waitForFunction(()=>!document.querySelector('#generate').disabled,null,{timeout:120000});
 await p.locator('#description').fill('Say hello.');await p.locator('#max-tokens').selectOption('128');
 const start=Date.now();await p.locator('#generate').click();
 try{await p.waitForFunction(()=>!document.querySelector('#generate').disabled,null,{timeout:90000});}
 catch(e){console.log('TIMEOUT',threads);}
 const result={threads,elapsed_ms:Date.now()-start,status:await p.locator('#status').textContent(),output:await p.locator('#output').textContent(),device:await p.locator('#device').textContent(),logs:await p.locator('#logs').textContent()};
 results.push(result);console.log(JSON.stringify({threads,elapsed_ms:result.elapsed_ms,status:result.status,output:result.output}));
 await writeFile(root+'/diagnostics.json',JSON.stringify(results,null,2));await b.close();
}
