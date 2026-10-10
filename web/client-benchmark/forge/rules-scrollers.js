/** Insert into native text selection; printed symbols have their own on-card wheels. */
export function rulesScrollers(wrap,input,symbol){
  const bar=document.createElement('div');bar.className='inline-symbol-tools';wrap.append(bar);
  bar.setAttribute('role','group');bar.setAttribute('aria-label','Insert mana at cursor');
  const plus=document.createElement('span');plus.textContent='+';plus.setAttribute('aria-hidden','true');bar.append(plus);
  let selection=[input.selectionStart,input.selectionEnd];
  const remember=()=>{selection=[input.selectionStart,input.selectionEnd];};
  for(const event of ['select','keyup','pointerup','input'])input.addEventListener(event,remember);
  for(const [value,label]of [['1','generic mana'],['W','white mana'],['U','blue mana'],['B','black mana'],['R','red mana'],['G','green mana'],['C','colorless mana'],['T','tap symbol'],['Q','untap symbol']]){
    const add=document.createElement('button');add.type='button';add.setAttribute('aria-label',`Insert ${label}`);add.title=`Insert {${value}}`;
    const url=symbol?.(value);if(url){const image=document.createElement('img');image.src=url;image.alt='';add.append(image);}else add.textContent=value;
    // Keep the native selection and mobile keyboard when using the toolbar.
    add.onpointerdown=e=>e.preventDefault();
    add.onclick=()=>{input.setRangeText(`{${value}}`,...selection,'end');remember();input.dispatchEvent(new Event('input',{bubbles:true}));input.focus({preventScroll:true});};bar.append(add);
  }
  return {dispose(){for(const event of ['select','keyup','pointerup','input'])input.removeEventListener(event,remember);bar.remove();}};
}
