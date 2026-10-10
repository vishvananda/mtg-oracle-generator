import {assetUrls,imageReady} from './preview-ready.js';

// Ask the real renderer for its assets; do not duplicate its frame-selection rules.
// The disposable ordinary face creates no foil canvases or etching jobs. A shadow
// root keeps its markup out of the visible card's editing and accessibility tree.
export function neighborPreloader(mount,stylesheet){
  let timer,controller;
  const warmed=new Set();
  function cancel(){clearTimeout(timer);controller?.abort();controller=null;}
  async function warm(faces,signal){
    const host=document.createElement('div');host.inert=true;host.setAttribute('aria-hidden','true');
    host.style.cssText='position:fixed;left:-10000px;top:0;width:488px;height:680px;visibility:hidden;pointer-events:none;contain:strict';
    const shadow=host.attachShadow({mode:'closed'}),root=document.createElement('div'),link=document.createElement('link');
    link.rel='stylesheet';link.href=stylesheet;
    let mounted;
    const dispose=()=>{mounted?.unmount();mounted=null;host.remove();};
    const loaded=new Promise((resolve,reject)=>{link.onload=resolve;link.onerror=reject;});
    signal.addEventListener('abort',dispose,{once:true});
    shadow.append(link,root);document.body.append(host);
    try{
      // Both neighbors' art starts together, even if one remote image is slow.
      for(const face of faces)if(face.illustration)void imageReady(face.illustration).catch(()=>{});
      await loaded;
      for(const face of faces){
        if(signal.aborted)return;
        const key=JSON.stringify(face);if(warmed.has(key))continue;
        mounted=await mount(root,{face,finishId:'ordinary',onPart(){}});
        await new Promise(resolve=>requestAnimationFrame(resolve));
        if(signal.aborted)return;
        const urls=assetUrls(root,face);
        void Promise.all([...urls].map(imageReady)).then(()=>{
          warmed.add(key);if(warmed.size>24)warmed.delete(warmed.values().next().value);
        }).catch(()=>{}); // Prefetch failures must never block navigation.
        mounted.unmount();mounted=null;
      }
    }finally{signal.removeEventListener('abort',dispose);dispose();}
  }
  return {cancel,schedule(faces){
    cancel();const fresh=[...new Map(faces.map(face=>[JSON.stringify(face),face])).values()].filter(face=>!warmed.has(JSON.stringify(face)));
    if(!fresh.length)return;
    controller=new AbortController();const signal=controller.signal;
    timer=setTimeout(()=>{if(!signal.aborted)void warm(fresh,signal).catch(()=>{});},250);
  }};
}
