const decoded=new Map();
const frame=()=>new Promise(resolve=>requestAnimationFrame(resolve));
export function imageReady(url){
  if(!decoded.has(url)){
    const image=new Image();image.crossOrigin='anonymous';image.src=url;
    const ready=image.decode().catch(error=>{decoded.delete(url);throw error;});
    decoded.set(url,ready);if(decoded.size>128)decoded.delete(decoded.keys().next().value);
  }
  return decoded.get(url);
}
function cancellable(promise,signal){
  return new Promise((resolve,reject)=>{
    const abort=()=>reject(signal.reason);if(signal.aborted){abort();return;}
    signal.addEventListener('abort',abort,{once:true});
    promise.then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));
  });
}
export function assetUrls(root,face){
  const urls=new Set(face.illustration?[face.illustration]:[]);
  for(const node of root.querySelectorAll('*')){
    if(node instanceof HTMLImageElement&&node.src)urls.add(node.src);
    for(const pseudo of [null,'::before','::after']){
      const style=getComputedStyle(node,pseudo);
      for(const value of [style.backgroundImage,style.maskImage,style.webkitMaskImage]){
        for(const match of (value||'').matchAll(/url\((?:"([^"]*)"|'([^']*)'|([^)]*))\)/g))urls.add(match[1]??match[2]??match[3]);
      }
    }
  }
  return urls;
}
async function assetsReady(root,face,signal){
  // Vue, style calculation and the foil watcher must see the new face first.
  await cancellable(frame(),signal);
  await cancellable(Promise.all([...assetUrls(root,face)].map(imageReady)),signal);
  // The foil canvas can finish before the hidden DOM artwork has decoded. It
  // must also be ready before an inline edit reveals that native face.
  await cancellable(Promise.all([...root.querySelectorAll('img')].filter(image=>image.src).map(image=>image.decode())),signal);
  await cancellable(document.fonts.ready,signal);
  await cancellable(frame(),signal);
}
function coatingReady(root,finish,signal){
  if(finish==='ordinary')return Promise.resolve();
  return new Promise((resolve,reject)=>{
    let scheduled;
    const cleanup=()=>{observer.disconnect();cancelAnimationFrame(scheduled);signal.removeEventListener('abort',abort);};
    const abort=()=>{cleanup();reject(signal.reason);};
    const check=()=>{
      const premium=root.querySelector('.premium-card');
      const fallback=document.documentElement.classList.contains('foil-static-fallback');
      const ready=premium?.hasAttribute('data-foil-webgl-face')&&premium.querySelector('canvas.premium-foil-coating')?.width>0&&premium.dataset.premiumCss!=='true';
      if(fallback||ready){cleanup();resolve();}
    };
    const observer=new MutationObserver(()=>{cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(check);});
    observer.observe(root,{childList:true,subtree:true,attributes:true,attributeFilter:['data-foil-webgl-face','data-premium-css','width']});
    observer.observe(document.documentElement,{attributes:true,attributeFilter:['class']});
    signal.addEventListener('abort',abort,{once:true});if(signal.aborted)abort();else check();
  });
}
function snapshotCard(root){
  const source=root.querySelector('.card-tilt');if(!source)return null;
  const copy=source.cloneNode(true);copy.classList.add('preview-last-card');copy.inert=true;copy.setAttribute('aria-hidden','true');
  const originals=source.querySelectorAll('canvas');
  copy.querySelectorAll('canvas').forEach((canvas,i)=>{
    canvas.width=originals[i].width;canvas.height=originals[i].height;
    canvas.getContext('2d').drawImage(originals[i],0,0);
  });
  copy.querySelectorAll('button,[id]').forEach(node=>node.tagName==='BUTTON'?node.remove():node.removeAttribute('id'));
  return copy;
}
/** Keep the last complete card (including its foil pixels) until the next is ready. */
export function presentWhenReady(host,root,mounted,placeTargets){
  let requested='',complete='',controller,lastCard,pending=Promise.resolve(),disposed=false,fadeOut=null,reveal=null;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  async function update(value,{transition=false}={}){
    const key=JSON.stringify([value.face,value.finishId]);
    if(key===requested){mounted.update({selectedPart:value.selectedPart});return pending;}
    requested=key;controller?.abort();controller=new AbortController();const signal=controller.signal;
    if(complete&&!lastCard){
      lastCard=snapshotCard(root);if(lastCard){lastCard.style.opacity=getComputedStyle(root).opacity;host.append(lastCard);}
    }
    reveal?.cancel();reveal=null;
    // A navigation snapshot only fades once, even when a second request arrives.
    if(transition&&lastCard&&!fadeOut){
      const animation=lastCard.animate([{opacity:getComputedStyle(lastCard).opacity},{opacity:0}],{duration:reduced.matches?0:180,fill:'forwards',easing:'ease-out'});
      fadeOut=animation.finished.catch(()=>{});
    }
    root.classList.add('preview-rendering');host.setAttribute('aria-busy','true');delete host.dataset.previewError;host.dispatchEvent(new CustomEvent('preview-pending'));
    mounted.update(value);
    const current=controller,timeout=setTimeout(()=>current.abort(new Error('The card assets took too long to load.')),20000);
    pending=(async()=>{
      try{
        await assetsReady(root,value.face,signal);await coatingReady(root,value.finishId,signal);
        if(fadeOut)await cancellable(fadeOut,signal);signal.throwIfAborted();
        root.classList.remove('preview-rendering');host.setAttribute('aria-busy','false');host.dataset.previewAvailable='true';
        const animateIn=Boolean(fadeOut)||transition;
        lastCard?.remove();lastCard=null;fadeOut=null;complete=key;placeTargets();
        if(animateIn&&!reduced.matches)reveal=root.animate([{opacity:0},{opacity:1}],{duration:320,easing:'ease-out'});
        host.dispatchEvent(new CustomEvent('preview-ready'));
      }catch(error){
        if(current!==controller||disposed)return;
        requested='';host.dataset.previewError='true';
        host.dispatchEvent(new CustomEvent('preview-error',{detail:error}));
      }finally{clearTimeout(timeout);}
    })();
    return pending;
  }
  return {update,dispose(){disposed=true;controller?.abort();reveal?.cancel();lastCard?.remove();}};
}
