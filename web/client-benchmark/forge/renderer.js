import { faceFor } from './cards.js';
export async function mountRenderer(host,card,onPart) {
  const root=document.createElement('div');
  const urls=['./renderer/card-preview.css'];
  await Promise.all(urls.map(path=>new Promise((resolve,reject)=>{
    const link=document.createElement('link');link.rel='stylesheet';link.href=new URL(path,import.meta.url).href;link.onload=resolve;link.onerror=()=>reject(new Error('The card renderer styles could not load.'));document.head.append(link);
  })));
  const styles=document.createElement('style');
  styles.textContent=`#card-preview{color:#141210;--card-corner:5.8% / 4.15%;--seat:#d6b877;--f-data:ui-monospace,monospace;--m-w:#efe7cb;--m-u:#3d7ac3;--m-b:#33313c;--m-r:#c2432e;--m-g:#3b8a4e;--m-c:#9aa4ae}.card-design-preview{max-width:none!important;width:100%!important}.art-edit-target{position:absolute;background:none;border:0;border-radius:0;cursor:pointer;z-index:2;padding:0}.art-edit-target:hover,.art-edit-target:focus-visible{outline:1px dashed #dfb866;outline-offset:2px}.art-edit-target span{position:absolute;right:9px;bottom:9px;background:#0b1920db;color:#edd8a9;border:1px solid #dfb86666;font:10px system-ui;padding:7px 9px;border-radius:4px;opacity:0;transition:opacity .2s}.art-edit-target:hover span,.art-edit-target:focus-visible span{opacity:1}`;
  host.append(styles,root);
  const {mountCardDesignPreview,setSymbolAsset}=await import('./renderer/card-preview.js');
  const mounted=await mountCardDesignPreview(root,{face:faceFor(card),finishId:'vizier_etched_v1',onPart});
  const art=document.createElement('button');art.type='button';art.className='art-edit-target';art.setAttribute('aria-label','Edit artwork prompt');
  const label=document.createElement('span');label.textContent='✧ Edit artwork';art.append(label);art.onclick=()=>onPart('art');
  const set=document.createElement('button'),artist=document.createElement('button');
  for(const [button,part,label]of [[set,'set','Edit set symbol'],[artist,'artist','Edit artist credit']]){button.type='button';button.className=`${part}-edit-target detail-edit-target`;button.setAttribute('aria-label',label);button.title=label;button.onclick=()=>onPart(part);}
  const rails={border:[],frame:[]};
  for(const part of ['border','frame'])for(let side=0;side<4;side++){
    const button=document.createElement('button');button.type='button';button.className=`${part}-edit-target detail-edit-target`;button.setAttribute('aria-label',part==='border'?'Edit border color':'Edit art frame');button.title=part==='border'?'Black or white border':'Modern or old frame';if(side)button.tabIndex=-1;button.onclick=()=>onPart(part);rails[part].push(button);
  }
  function placeArtButton(){
    const tilt=root.querySelector('.card-tilt'),face=root.querySelector('.rendered-card-face');
    if(!tilt||!face)return;
    if(art.parentElement!==tilt)tilt.append(art);
    for(const button of [set,artist,...rails.border,...rails.frame])if(button.parentElement!==tilt)tilt.append(button);
    const css=getComputedStyle(face);
    for(const [property,key,fallback] of [['left','x','6.929%'],['top','y','10.742%'],['width','w','86.142%'],['height','h','44.882%']])art.style[property]=css.getPropertyValue(`--face-art-${key}`).trim()||fallback;
    set.style.right='auto';set.style.left=`${parseFloat(css.getPropertyValue('--face-type-x'))+parseFloat(css.getPropertyValue('--face-type-w'))-10}%`;
    set.style.top=css.getPropertyValue('--face-type-y').trim()||'56.13%';set.style.height=css.getPropertyValue('--face-type-h').trim()||'5.737%';
    for(const [property,key,fallback]of [['top','y','92.5%'],['left','x','7%'],['width','w','65%'],['height','h','4.3%']])artist.style[property]=css.getPropertyValue(`--face-footer-${key}`).trim()||fallback;
    artist.style.bottom='auto';
    const artBox=['x','y','w','h'].map((key,i)=>parseFloat(css.getPropertyValue(`--face-art-${key}`))||[6.929,10.742,86.142,44.882][i]);
    const [x,y,w,h]=artBox;
    const boxes={border:[[3.5,0,93,3],[3.5,97,93,3],[0,3,3.5,94],[96.5,3,3.5,94]],frame:[[x-2,y-1.4,w+4,1.4],[x-2,y+h,w+4,1.1],[x-2,y,2,h],[x+w,y,2,h]]};
    for(const part of ['border','frame'])rails[part].forEach((button,i)=>{const box=boxes[part][i];for(const [j,key]of ['left','top','width','height'].entries())button.style[key]=`${box[j]}%`;});
  }
  let scheduled;
  const observer=new MutationObserver(()=>{cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(placeArtButton);});
  observer.observe(root,{childList:true,subtree:true});placeArtButton();
  return {finishes:mounted.presentation.finishes,manaSymbol:mounted.presentation.manaSymbolAsset,setSymbol:setSymbolAsset,update(card,finish,selectedPart){mounted.update({face:faceFor(card),finishId:finish,selectedPart});requestAnimationFrame(placeArtButton);},setBusy(busy){root.inert=busy;},focus(part){(part==='art'?art:part==='set'?set:part==='artist'?artist:rails[part]?.[0]||root.querySelector(`[data-card-design-part="${part}"]`))?.focus();},unmount(){observer.disconnect();cancelAnimationFrame(scheduled);mounted.unmount();}};
}
