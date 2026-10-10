// Visual diagnostic: Scryfall scan beside the standalone renderer, optionally Canvas2D.
// Reference images are downloaded to the evidence directory, never bundled in Forge.
import {chromium} from 'playwright';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
const out=process.env.FORGE_EVIDENCE||'/tmp/forge-planeswalker',tag=process.env.TAG||'current';
const base=process.env.FORGE_URL||'https://tetrarcum.com/';
await mkdir(out,{recursive:true});
async function cached(name,url){
  try{return await readFile(`${out}/${name}`);}catch(error){if(error.code!=='ENOENT')throw error;}
  const response=await fetch(url);if(!response.ok)throw Error(`${response.status}: ${url}`);
  const bytes=Buffer.from(await response.arrayBuffer());await writeFile(`${out}/${name}`,bytes);return bytes;
}
const card=JSON.parse(await cached('jace-soi.json','https://api.scryfall.com/cards/soi/69'));
const source=card.scryfall_uri;
card.image_uris.png='data:image/png;base64,'+(await cached('jace-png.png',card.image_uris.png)).toString('base64');
card.image_uris.art_crop='data:image/jpeg;base64,'+(await cached('jace-art_crop.jpg',card.image_uris.art_crop)).toString('base64');
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||undefined,args:['--no-sandbox','--enable-unsafe-swiftshader']});
try{
 const canvasURL=process.env.CANVAS_URL;
 const page=await browser.newPage({viewport:{width:canvasURL?1584:1072,height:752},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('requestfailed',r=>console.error('Request failed:',r.url().slice(0,180),r.failure()?.errorText));
 page.on('console',m=>{if(m.type()==='error')console.error(m.text().slice(0,300));});
 if(canvasURL){
  // Keep the diagnostic same-origin; forward only Vite module paths through Node.
  for(const prefix of ['/src/','/node_modules/','/@'])await page.route(new URL(prefix+'**',base).href,async route=>{
    const requested=new URL(route.request().url());
    await route.fulfill({response:await route.fetch({url:new URL(requested.pathname+requested.search,canvasURL).href})});
  });
 }
 const url=new URL('renderer-comparison',base).href;
 await page.route(url,route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta charset="utf-8"><title>Planeswalker comparison</title><link rel="stylesheet" href="${new URL('renderer/card-preview.css',base)}"><style>body{margin:0;background:#192125;color:white;font:12px system-ui}main{display:flex;gap:24px;padding:24px;align-items:start}figure{margin:0;width:488px}figcaption{height:22px}img,#render,canvas{width:488px;height:680px}#render{--card-corner:5.8% / 4.15%}canvas{border-radius:5.8% / 4.15%}.card-design-preview{width:100%;max-width:none}.card-tilt{transform:none!important}</style><main><figure><figcaption>Scryfall scan · SOI 69</figcaption><img id="reference"></figure><figure><figcaption>Shared DOM renderer · current Oracle text</figcaption><div id="render"></div></figure></main>`}));
 await page.goto(url);
 await page.evaluate(async({card,base,canvasURL})=>{
  const {mountCardDesignPreview}=await import(new URL('renderer/card-preview.js',base));
  document.querySelector('#reference').src=card.image_uris.png;
  const face={name:card.name,manaSymbols:card.mana_cost.match(/\{[^}]+\}/g).map(s=>s.slice(1,-1)),colors:card.colors,typeLine:card.type_line,rulesText:card.oracle_text,loyalty:card.loyalty,rarity:card.rarity,setCode:'SOI',artist:card.artist,illustration:card.image_uris.art_crop};
  await mountCardDesignPreview(document.querySelector('#render'),{face,finishId:'ordinary'});
  await document.fonts.ready;
  if(canvasURL){
   const {composeCardTexture}=await import(new URL('/src/shared/cards/presentation/canvas-face.ts',base));
   const canvas=await composeCardTexture({face,printingId:null,printedImage:null},'inspect',new AbortController().signal);
   const figure=document.createElement('figure'),caption=document.createElement('figcaption');caption.textContent='Shared canvas renderer · foil source';figure.append(caption,canvas);document.querySelector('main').append(figure);
  }
 },{card,base,canvasURL});
 await page.waitForFunction(()=>[...document.images].every(i=>i.complete));
 await page.waitForTimeout(1500);
 await page.screenshot({path:`${out}/jace-${tag}-comparison.png`});
 await page.locator('#render').screenshot({path:`${out}/jace-${tag}.png`});
 const metrics=await page.evaluate(()=>Object.fromEntries(['.rendered-special-rules','.rendered-special-badge','.rendered-special-text','.rendered-card-stats'].map(s=>{const e=document.querySelector(s),c=getComputedStyle(e),r=e.getBoundingClientRect();return[s,{x:r.x,y:r.y,w:r.width,h:r.height,font:c.font}]})));
 await writeFile(`${out}/jace-${tag}-metrics.json`,JSON.stringify({source,metrics,errors},null,2)+'\n');
 if(errors.length)throw Error(errors.join('\n'));
 console.log(`Saved comparison and metrics: ${out}/jace-${tag}-comparison.png`);
}finally{await browser.close();}
