const locks=new WeakMap();
/** Hold the card's physical size while a touch keyboard changes the viewport. */
export function holdCardSize(stage) {
  if(!stage||!matchMedia('(pointer: coarse)').matches)return ()=>{};
  let lock=locks.get(stage);
  if(!lock){
    const view=window.visualViewport,screenWidth=innerWidth,visibleHeight=view?.height||innerHeight;
    lock={users:0};locks.set(stage,lock);
    stage.style.setProperty('--editing-card-width',`${stage.getBoundingClientRect().width}px`);
    stage.classList.add('card-size-held');
    const release=()=>{
      const rotated=Math.abs(innerWidth-screenWidth)>4;
      const keyboardClosed=(view?.height||innerHeight)>=visibleHeight-48;
      if(!rotated&&(lock.users||!keyboardClosed||document.activeElement?.matches('input,textarea')))return;
      stage.classList.remove('card-size-held');stage.style.removeProperty('--editing-card-width');locks.delete(stage);
      window.removeEventListener('resize',release);view?.removeEventListener('resize',release);document.removeEventListener('focusout',afterBlur);
    };
    const afterBlur=()=>requestAnimationFrame(release);
    lock.release=afterBlur;
    window.addEventListener('resize',release);view?.addEventListener('resize',release);document.addEventListener('focusout',afterBlur);
  }
  lock.users++;
  return ()=>{lock.users--;lock.release();};
}
/** Keep a 16px native control for iOS focus, but paint at the card's exact size. */
export function matchCardType(input,font) {
  const size=parseFloat(font.fontSize),scale=Math.min(1,size/16);
  const rgb=font.color.match(/[\d.]+/g)?.slice(0,3).map(Number);
  const light=rgb&&(.2126*rgb[0]+.7152*rgb[1]+.0722*rgb[2])>160;
  input.style.background=light?'#262c34':'#e8edf0';
  const length=value=>value==='normal'?'normal':`${parseFloat(value)/scale}px`;
  Object.assign(input.style,{
    fontFamily:font.fontFamily,fontWeight:font.fontWeight,fontStyle:font.fontStyle,
    fontVariant:font.fontVariant,fontStretch:font.fontStretch,fontKerning:font.fontKerning,
    fontFeatureSettings:font.fontFeatureSettings,fontVariationSettings:font.fontVariationSettings,
    fontSize:`${size/scale}px`,letterSpacing:length(font.letterSpacing),wordSpacing:length(font.wordSpacing),lineHeight:length(font.lineHeight),
    color:font.color,width:`${100/scale}%`,height:`${100/scale}%`,transform:`scale(${scale})`,
  });
}
