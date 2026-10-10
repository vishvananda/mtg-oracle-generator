import assert from'node:assert/strict';import{mkdir,writeFile}from'node:fs/promises';import{chromium,webkit}from'playwright';
const engine=process.env.FORGE_BROWSER||'chromium',base=process.env.FORGE_URL||'http://127.0.0.1:8793/forge/',out=process.env.FORGE_EVIDENCE||'/tmp/forge-share';await mkdir(out,{recursive:true});
const b=await(engine==='webkit'?webkit:chromium).launch(engine==='webkit'?{}:{executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});const checks=[],errors=[];const check=(name,v)=>{assert.ok(v,name);checks.push(name);console.log('PASS',name);};
const p=await b.newPage({viewport:{width:1440,height:900}});p.on('pageerror',e=>errors.push(e.message));
try{
 await p.addInitScript(()=>{localStorage.setItem('forge-sound','off');navigator.canShare=()=>true;navigator.share=async d=>{window.sharedFile={name:d.files[0].name,size:d.files[0].size,type:d.files[0].type};};});
 await p.goto(base);await p.waitForFunction(()=>document.querySelector('#card-preview')?.getAttribute('aria-busy')==='false');await p.locator('#pause-carousel').click();
 check('save group has three labeled icon buttons',await p.locator('.save-box button').count()===3&&await p.locator('.save-box svg').count()===3);
 await p.locator('#save-image').click();await p.locator('.share-dialog img').waitFor({timeout:60000});
 check('image preview is full resolution',await p.locator('.share-dialog img').evaluate(e=>e.naturalWidth===732&&e.naturalHeight===1020));
 await p.screenshot({path:`${out}/image-preview.png`});
 await p.getByRole('button',{name:'Share',exact:true}).click();check('share receives the prepared PNG file',await p.evaluate(()=>window.sharedFile.type==='image/png'&&window.sharedFile.size>10000));
 let download=p.waitForEvent('download');await p.getByRole('button',{name:'Download',exact:true}).click();let item=await download;await item.saveAs(`${out}/card.png`);check('download uses the same PNG',item.suggestedFilename().endsWith('.png'));await p.getByRole('button',{name:'Close sharing'}).click();
 const canRecord=await p.evaluate(()=>Boolean(globalThis.MediaRecorder&&HTMLCanvasElement.prototype.captureStream));
 if(canRecord){
 await p.locator('#save-video').click();await p.locator('.share-dialog video').waitFor({timeout:60000});await p.locator('.share-dialog video').evaluate(async v=>{if(v.readyState<1)await new Promise((r,j)=>{v.onloadedmetadata=r;v.onerror=()=>j(Error('Video decode failed.'));});});
 const metadata=await p.locator('.share-dialog video').evaluate(v=>({width:v.videoWidth,height:v.videoHeight,duration:v.duration}));console.log(metadata);
 check('video decodes at 720 by 1080',metadata.width===720&&metadata.height===1080);check('video is approximately six seconds',metadata.duration>5.5&&metadata.duration<7.5);
 download=p.waitForEvent('download');await p.getByRole('button',{name:'Download',exact:true}).click();item=await download;const ext=item.suggestedFilename().split('.').at(-1);await item.saveAs(`${out}/card.${ext}`);
 await p.getByRole('button',{name:'Share',exact:true}).click();check('share receives the same video format',await p.evaluate(()=>window.sharedFile.type.startsWith('video/')&&window.sharedFile.size>10000));
 for(const time of [.2,.45,1.6,2.65]){await p.locator('.share-dialog video').evaluate(async(v,time)=>{v.pause();const seeked=new Promise(resolve=>v.addEventListener('seeked',resolve,{once:true}));v.currentTime=time;await seeked;},time);await p.locator('.share-dialog video').screenshot({path:`${out}/tilt-${time}.png`});}
 await p.locator('.share-dialog video').evaluate(v=>{v.currentTime=5.6;});await p.waitForTimeout(300);await p.screenshot({path:`${out}/video-end.png`});await p.getByRole('button',{name:'Close sharing'}).click();
 }else{await p.locator('#save-video').click();await p.waitForFunction(()=>document.querySelector('.share-status').textContent.includes('cannot record'));check('missing video encoder offers image fallback',true);await p.getByRole('button',{name:'Close sharing'}).click();}
 await p.locator('#save-text').click();await p.locator('.share-dialog pre').waitFor();check('text export substitutes the displayed card name', (await p.locator('.share-dialog pre').innerText()).includes('Vesper, Eclipse Sovereign'));
 download=p.waitForEvent('download');await p.getByRole('button',{name:'Save editable card (.json)',exact:true}).click();item=await download;const stream=await item.createReadStream();let json='';for await(const part of stream)json+=part;const card=JSON.parse(json);check('editable JSON export keeps artwork and card data',card.name==='Vesper, Eclipse Sovereign'&&card.art_data_url.startsWith('data:image/'));await p.getByRole('button',{name:'Close sharing'}).click();
 await p.locator('#save-video').click();await p.getByRole('button',{name:'Close sharing'}).click();await p.waitForFunction(()=>!document.querySelector('#save-image').disabled);check('cancelled preparation restores card controls',await p.locator('#save-image').isEnabled());
 const mesh=await p.evaluate(async()=>{
  const {drawFrame,animationPose}=await import('./share-card.js');
  const source=document.createElement('canvas');source.width=732;source.height=1020;const ink=source.getContext('2d');ink.fillStyle='white';ink.fillRect(0,0,732,1020);
  const output=document.createElement('canvas');output.width=720;output.height=1080;const ctx=output.getContext('2d');drawFrame(ctx,source,animationPose(0));
  const data=ctx.getImageData(120,180,480,620).data;let cracks=0;for(let i=0;i<data.length;i+=4)if(Math.min(data[i],data[i+1],data[i+2])<250)cracks++;return cracks;
 });check('warped card has no dark seams between mesh triangles',mesh===0);
 const fixtures=await p.evaluate(async()=>{const{createCardDesignCapture}=await import('./renderer/card-preview.js');const{demos,faceFor}=await import('./cards.js');const results=[];for(const finish of ['ordinary','vizier_prismatic_v1','vizier_etched_v1','vizier_opal_v1','vizier_cold_v1']){const capture=await createCardDesignCapture(faceFor(demos[1]),finish);const c=document.createElement('canvas');c.width=732;c.height=1020;const ctx=c.getContext('2d',{willReadFrequently:true});capture.paint({x:.15,y:.7});ctx.drawImage(capture.canvas,0,0);const a=ctx.getImageData(0,0,732,1020).data;capture.paint({x:.8,y:.2});ctx.clearRect(0,0,732,1020);ctx.drawImage(capture.canvas,0,0);const d=ctx.getImageData(0,0,732,1020).data;let changed=0;for(let i=0;i<a.length;i+=4)if(Math.abs(a[i]-d[i])>3)changed++;capture.dispose();results.push({finish,changed,released:capture.canvas.width===0});}return results;});
 for(const f of fixtures){check(`${f.finish} exports and releases its surface`,f.released);check(`${f.finish} foil responds correctly to tilt`,f.finish==='ordinary'?f.changed===0:f.changed>1000);}
 const contours=await p.evaluate(async()=>{
  const {createCardDesignCapture}=await import('./renderer/card-preview.js'),{demos,faceFor}=await import('./cards.js');
  const empty=document.createElement('canvas');empty.width=empty.height=2;
  const blankURL=URL.createObjectURL(await new Promise(r=>empty.toBlob(r))),artURL=URL.createObjectURL(await fetch(demos[0].illustration).then(r=>r.blob()));
  const c=document.createElement('canvas');c.width=732;c.height=1020;const ctx=c.getContext('2d',{willReadFrequently:true});
  const pixels=capture=>{capture.paint({x:.8,y:.2});ctx.clearRect(0,0,732,1020);ctx.drawImage(capture.canvas,0,0);return ctx.getImageData(0,0,732,1020).data;};
  const created=[],revoked=[],create=URL.createObjectURL,revoke=URL.revokeObjectURL;
  URL.createObjectURL=function(blob){const url=create.call(this,blob);if(blob.type==='image/png')created.push(url);return url;};URL.revokeObjectURL=function(url){revoked.push(url);return revoke.call(this,url);};
  const results=[];
  try{for(const [source,finish]of [['file','vizier_etched_v1'],['blob','vizier_etched_v1'],['blob','vizier_opal_v1']]){
   const face=faceFor({...demos[0],illustration:source==='blob'?artURL:demos[0].illustration});
   const auto=await createCardDesignCapture(face,finish),blank=await createCardDesignCapture({...face,etchedIllustration:blankURL},finish);
   const a=pixels(auto),b=pixels(blank);let changed=0;
   for(let y=150;y<480;y++)for(let x=100;x<630;x++){const k=(y*732+x)*4;if(Math.abs(a[k]-b[k])+Math.abs(a[k+1]-b[k+1])+Math.abs(a[k+2]-b[k+2])>5)changed++;}
   auto.dispose();blank.dispose();results.push({source,finish,changed});
  }return {results,ownedMasksReleased:created.length>=3&&created.every(url=>revoked.includes(url))};}
  finally{URL.createObjectURL=create;URL.revokeObjectURL=revoke;URL.revokeObjectURL(blankURL);URL.revokeObjectURL(artURL);}
 });
 for(const c of contours.results)check(`${c.source} ${c.finish} exports etched illustration contours`,c.changed>1000);
 check('export releases every locally derived mask',contours.ownedMasksReleased);
 const motion=await p.evaluate(async()=>{const{animationPose,animationCorners}=await import('./share-card.js');const frames=Array.from({length:601},(_,i)=>animationCorners(i/100));return {held:[3.8,4,5,6].every(t=>JSON.stringify(animationPose(t))==='{"x":0,"y":0,"roll":0,"scale":1,"dx":0,"dy":0}'),fits:frames.every(q=>q.every(([x,y])=>x>0&&x<720&&y>0&&y<1020)),corners:[0,1,2,3].map(i=>{const points=frames.map(q=>q[i]);return {x:Math.max(...points.map(p=>p[0]))-Math.min(...points.map(p=>p[0])),y:Math.max(...points.map(p=>p[1]))-Math.min(...points.map(p=>p[1]))};})};});
 check('last 2.2 seconds stay exactly centered',motion.held);check('whole card stays inside the video frame',motion.fits);check('all four corners move in both directions',motion.corners.every(c=>c.x>30&&c.y>40));
 for(const[w,h]of [[1440,900],[1024,640],[390,844],[320,780]]){await p.setViewportSize({width:w,height:h});check(`save controls fit ${w}px`,await p.locator('.save-box').evaluate(e=>e.getBoundingClientRect().right<=innerWidth&&e.getBoundingClientRect().left>=0));}
 check('no uncaught JavaScript errors',errors.length===0);
}finally{await b.close();await writeFile(`${out}/receipt.json`,JSON.stringify({engine,base,checks,errors},null,2)+'\n');}
