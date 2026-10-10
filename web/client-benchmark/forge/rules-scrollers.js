/** Insert into native text selection; printed symbols have their own on-card wheels. */
export function rulesScrollers(wrap,input){
  const bar=document.createElement('div');bar.className='inline-symbol-tools';wrap.append(bar);
  let selection=[input.selectionStart,input.selectionEnd];
  const remember=()=>{selection=[input.selectionStart,input.selectionEnd];};
  for(const event of ['select','keyup','pointerup'])input.addEventListener(event,remember);
  const add=document.createElement('button');add.type='button';add.textContent='+ Mana';add.setAttribute('aria-label','Insert mana at cursor');
  add.onclick=()=>{input.setRangeText('{1}',...selection,'end');remember();input.focus({preventScroll:true});};bar.append(add);
  return {dispose(){for(const event of ['select','keyup','pointerup'])input.removeEventListener(event,remember);bar.remove();}};
}
