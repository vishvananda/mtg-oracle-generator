import {faceFor} from './cards.js';
const blobOf=(canvas,type='image/png')=>new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(Error('The image could not be exported.')),type));
export function cardText(card){return [card.name,card.mana_cost,card.type_line,card.oracle_text.replaceAll('CARDNAME',card.name),card.power!=null?`${card.power}/${card.toughness}`:card.loyalty!=null?`Loyalty: ${card.loyalty}`:'',card.rarity?`Rarity: ${card.rarity}`:'',card.artist?`Art: ${card.artist}`:''].filter(Boolean).join('\n\n');}
export function animationPose(seconds){
 // A quick two-way flash, then a slower inspection and a readable final hold.
 const keys=[[0,0,0],[.16,.10,-.185],[.40,-.12,.185],[.70,.06,-.12],[1.6,-.045,.11],[2.65,.035,-.075],[3.8,0,0]];
 if(seconds>=3.8)return {x:0,y:0};
 const i=Math.max(1,keys.findIndex(k=>k[0]>=Math.max(0,seconds))),a=keys[i-1],b=keys[i];
 const t=Math.max(0,Math.min(1,(seconds-a[0])/(b[0]-a[0]))),e=t*t*(3-2*t);
 return {x:a[1]+(b[1]-a[1])*e,y:a[2]+(b[2]-a[2])*e};
}

function projected(x,y,rx,ry){
 const xx=x*Math.cos(ry)+y*Math.sin(rx)*Math.sin(ry),yy=y*Math.cos(rx),z=-x*Math.sin(ry)+y*Math.sin(rx)*Math.cos(ry),f=1400/(1400+z);
 return [360+xx*f,536+yy*f];
}
function triangle(ctx,source,src,dest){
 const [[u0,v0],[u1,v1],[u2,v2]]=src,[[x0,y0],[x1,y1],[x2,y2]]=dest;
 const det=(u1-u0)*(v2-v0)-(u2-u0)*(v1-v0);if(!det)return;
 const a=((x1-x0)*(v2-v0)-(x2-x0)*(v1-v0))/det,c=((u1-u0)*(x2-x0)-(u2-u0)*(x1-x0))/det;
 const b=((y1-y0)*(v2-v0)-(y2-y0)*(v1-v0))/det,d=((u1-u0)*(y2-y0)-(u2-u0)*(y1-y0))/det;
 ctx.save();ctx.beginPath();for(let i=0;i<3;i++){const p=dest[i];if(i)ctx.lineTo(...p);else ctx.moveTo(...p);}ctx.closePath();ctx.clip();ctx.setTransform(a,b,c,d,x0-a*u0-c*v0,y0-b*u0-d*v0);ctx.drawImage(source,0,0);ctx.restore();
}
function drawFrame(ctx,source,pose){
 ctx.setTransform(1,0,0,1,0,0);const gradient=ctx.createRadialGradient(360,450,50,360,520,800);gradient.addColorStop(0,'#30444b');gradient.addColorStop(1,'#0b151d');ctx.fillStyle=gradient;ctx.fillRect(0,0,720,1080);
 const w=632,h=w*680/488;
 if(pose.x===0&&pose.y===0){ctx.save();ctx.beginPath();ctx.roundRect(360-w/2,536-h/2,w,h,34);ctx.clip();ctx.drawImage(source,360-w/2,536-h/2,w,h);ctx.restore();}
 else{
  for(let y=0;y<8;y++)for(let x=0;x<6;x++){
   const points=[[x/6,y/8],[(x+1)/6,y/8],[(x+1)/6,(y+1)/8],[x/6,(y+1)/8]];
   const dest=points.map(([u,v])=>projected((u-.5)*w,(v-.5)*h,pose.x,pose.y)),src=points.map(([u,v])=>[u*source.width,v*source.height]);
   triangle(ctx,source,[src[0],src[1],src[2]],[dest[0],dest[1],dest[2]]);triangle(ctx,source,[src[0],src[2],src[3]],[dest[0],dest[2],dest[3]]);
  }
 }
 ctx.fillStyle='#c7b985';ctx.font='15px system-ui';ctx.textAlign='center';ctx.fillText('MTG CardForge',360,1040);
}
export async function createShareFile(card,kind,{signal,progress=()=>{}}={}){
 const stem=card.name.replace(/[^a-z0-9]+/gi,'-').toLowerCase().slice(0,80)||'card';
 if(kind==='text')return new File([cardText(card)],stem+'.txt',{type:'text/plain'});
 if(kind==='video'&&(!globalThis.MediaRecorder||!HTMLCanvasElement.prototype.captureStream))throw Error('This browser cannot record a video. You can save an image instead.');
 const {createCardDesignCapture}=await import('./renderer/card-preview.js');
 const capture=await createCardDesignCapture(faceFor(card),card.finish_id||'vizier_etched_v1',signal);
 let stream,recorder;
 try{
  signal?.throwIfAborted();
  if(kind==='image'){
   capture.paint({x:.5,y:.5});const output=document.createElement('canvas');output.width=732;output.height=1020;
   const ctx=output.getContext('2d');ctx.beginPath();ctx.roundRect(0,0,732,1020,42);ctx.clip();ctx.drawImage(capture.canvas,0,0);
   return new File([await blobOf(output)],stem+'.png',{type:'image/png'});
  }
  if(!globalThis.MediaRecorder||!HTMLCanvasElement.prototype.captureStream)throw Error('This browser cannot record a video. You can save an image instead.');
  const mime=['video/mp4;codecs=avc1.42E01E','video/mp4;codecs=avc1','video/webm;codecs=vp9','video/webm;codecs=vp8','video/webm'].find(t=>MediaRecorder.isTypeSupported(t));
  if(!mime)throw Error('No supported video encoder. You can save an image instead.');
  const canvas=document.createElement('canvas');canvas.width=720;canvas.height=1080;const ctx=canvas.getContext('2d',{alpha:false});
  drawFrame(ctx,capture.canvas,{x:0,y:0});stream=canvas.captureStream(30);recorder=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:6000000});
  const chunks=[];
  const recorded=new Promise((resolve,reject)=>{recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};recorder.onerror=e=>reject(e.error||Error('Video recording failed.'));recorder.onstop=()=>resolve(new Blob(chunks,{type:recorder.mimeType}));});
  // Attach the rejection handler before recording, including cancellation paths.
  recorded.catch(()=>{});recorder.start(200);
  await new Promise((resolve,reject)=>{
   let frame;const start=performance.now();
   const stop=()=>{cancelAnimationFrame(frame);signal?.removeEventListener('abort',aborted);document.removeEventListener('visibilitychange',hidden);};
   const aborted=()=>{stop();reject(new DOMException('Export stopped.','AbortError'));};
   const hidden=()=>{if(document.hidden){stop();reject(Error('Keep this page visible while recording. Please try again.'));}};
   signal?.addEventListener('abort',aborted,{once:true});document.addEventListener('visibilitychange',hidden);
   const tick=()=>{
    try{
     signal?.throwIfAborted();const seconds=(performance.now()-start)/1000,pose=animationPose(seconds);
     capture.paint({x:.5+pose.y/.38,y:.5+pose.x/.30});drawFrame(ctx,capture.canvas,pose);progress(Math.min(1,seconds/6));
     if(seconds>=6){stop();resolve();}else frame=requestAnimationFrame(tick);
    }catch(error){stop();reject(error);}
   };frame=requestAnimationFrame(tick);
  });
  recorder.stop();const blob=await recorded;if(!blob.size)throw Error('The browser produced an empty video.');
  return new File([blob],stem+(mime.startsWith('video/mp4')?'.mp4':'.webm'),{type:blob.type.split(';')[0]});
 }finally{if(recorder&&recorder.state!=='inactive')recorder.stop();stream?.getTracks().forEach(t=>t.stop());capture.dispose();}
}
const download=file=>{const a=document.createElement('a'),url=URL.createObjectURL(file);a.href=url;a.download=file.name;a.click();setTimeout(()=>URL.revokeObjectURL(url),60000);};
export async function showShare(card,kind,{exportJSON}){
 const dialog=document.createElement('dialog');dialog.className='share-dialog';
 const title=document.createElement('h2');title.textContent=kind==='video'?'Share foil animation':kind==='image'?'Share card image':'Save card text';title.id='share-title';dialog.setAttribute('aria-labelledby',title.id);
 const close=document.createElement('button');close.type='button';close.className='close-button';close.textContent='×';close.setAttribute('aria-label','Close sharing');close.onclick=()=>dialog.close();
 const status=document.createElement('p');status.className='share-status';status.setAttribute('role','status');status.textContent='Preparing…';
 const media=document.createElement('div');media.className='share-media';
 const actions=document.createElement('div');actions.className='share-actions';const share=document.createElement('button'),save=document.createElement('button');
 share.type=save.type='button';share.className='primary';save.className='quiet-button';share.textContent='Share';save.textContent='Download';share.hidden=true;save.disabled=true;actions.append(share,save);
 const controller=new AbortController();let file,previewURL;
 const finished=new Promise(resolve=>dialog.addEventListener('close',()=>{controller.abort();if(previewURL)URL.revokeObjectURL(previewURL);dialog.remove();resolve();},{once:true}));
 dialog.append(close,title,media,status,actions);
 if(kind==='text'){const editable=document.createElement('button');editable.type='button';editable.className='quiet-button share-data';editable.textContent='Save editable card (.json)';editable.onclick=()=>void exportJSON();dialog.append(editable);}
 document.body.append(dialog);dialog.showModal();close.focus();
 save.onclick=()=>{if(file)download(file);};
 share.onclick=async()=>{if(!file)return;try{await navigator.share({files:[file]});}catch(error){if(error.name!=='AbortError')status.textContent='Sharing was unavailable. Use Download to keep the file.';}};
 void createShareFile(card,kind,{signal:controller.signal,progress:f=>status.textContent=`Recording… ${Math.round(f*100)}%`}).then(result=>{
  if(controller.signal.aborted)return;file=result;save.disabled=false;share.hidden=!(navigator.canShare?.({files:[file]})&&navigator.share);
  if(kind==='text'){const pre=document.createElement('pre');pre.textContent=cardText(card);media.append(pre);}
  else{previewURL=URL.createObjectURL(file);const e=document.createElement(kind==='video'?'video':'img');e.src=previewURL;if(kind==='video'){e.controls=true;e.playsInline=true;e.muted=true;e.preload='auto';}else e.alt=card.name;media.append(e);}
  status.textContent=kind==='video'?`6 seconds · 720 × 1080 · ${file.name.endsWith('.mp4')?'MP4':'WebM'} · ${(file.size/1e6).toFixed(1)} MB`:kind==='image'?'PNG · 732 × 1020':'Plain text';
 }).catch(error=>{if(!controller.signal.aborted)status.textContent=error.message;});
 await finished;
}
