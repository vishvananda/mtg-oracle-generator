import {rulesScrollers} from './rules-scrollers.js';
import {holdCardSize,matchCardType} from './editing-geometry.js';
/** Native inputs on the printed card: keyboard/IME support, no contenteditable HTML. */
export function editOnCard(host,part,value,{save,cancel,symbol}) {
  const releaseSize=holdCardSize(host.closest('.card-stage'));
  const name=part==='name',wrap=document.createElement('div');
  wrap.className=`inline-card-editor inline-card-${part}`;
  const input=document.createElement(name?'input':'textarea');
  input.id=name?'edit-name':'edit-oracle_text';input.className='inline-card-input';
  input.value=value;input.maxLength=name?200:5000;input.spellcheck=!name;
  input.setAttribute('aria-label',name?'Card name':'Card rules text');
  input.setAttribute('aria-describedby','inline-edit-hint');
  if(name)input.enterKeyHint='done';
  const tools=document.createElement('div');tools.className='inline-card-tools';
  const hint=document.createElement('span');hint.id='inline-edit-hint';hint.textContent=name?'Enter to save · Esc to cancel':'Ctrl / ⌘ Enter to save · Esc to cancel';
  const done=document.createElement('button');done.type='button';done.textContent='Done';done.setAttribute('aria-label','Save card text');
  const undo=document.createElement('button');undo.type='button';undo.textContent='Cancel';
  const field=document.createElement('div');field.className='inline-card-field';field.append(input);
  tools.append(hint,undo,done);wrap.append(field,tools);
  host.classList.add('inline-editing',`inline-editing-${part}`);
  const tilt=host.querySelector('.card-tilt');tilt.append(wrap);
  const symbols=!name&&symbol?rulesScrollers(wrap,input,symbol):null;
  let closed=false;
  function finish(commit,restore=false){
    if(closed)return;closed=true;const next=input.value;
    observer.disconnect();document.fonts.removeEventListener('loadingdone',position);document.removeEventListener('pointerdown',outside,true);
    symbols?.dispose();wrap.remove();host.classList.remove('inline-editing',`inline-editing-${part}`);releaseSize();
    if(commit)save(next,restore);else cancel(restore);
  }
  function outside(event){if(!wrap.contains(event.target))finish(true);}
  function position(){
    const box=tilt.getBoundingClientRect();
    const source=host.querySelector(name?'.rendered-card-title':'.rendered-card-rules,.rendered-special-rules');
    if(!source)return;
    const rect=source.getBoundingClientRect(),style=getComputedStyle(source);
    let left=rect.left-box.left,top=rect.top-box.top,width=rect.width,height=rect.height;
    let font=style;
    if(name){
      const label=source.querySelector('strong'),mana=source.querySelector('.rendered-card-mana');
      const start=label?.getBoundingClientRect().left||rect.left+6;
      width=(mana?mana.getBoundingClientRect().left-5:rect.right-6)-start;left=start-box.left;
      font=getComputedStyle(label||source);
    }else {
      font=getComputedStyle(source.querySelector('.rendered-special-text,.rendered-card-rule-line')||source);
      const x=parseFloat(style.paddingLeft)||0,y=parseFloat(style.paddingTop)||0;
      left+=x;top+=y;width-=x+(parseFloat(style.paddingRight)||0);height-=y+(parseFloat(style.paddingBottom)||0);
    }
    Object.assign(wrap.style,{left:`${left}px`,top:`${top}px`,width:`${width}px`,height:`${height}px`});
    matchCardType(input,font);
  }
  // The preview's drag-to-tilt must never capture text selection or scroll.
  for(const event of ['pointerdown','pointermove','pointerup','pointercancel','click'])wrap.addEventListener(event,e=>e.stopPropagation());
  wrap.addEventListener('keydown',e=>{
    e.stopPropagation();if(e.isComposing)return;
    if(e.key==='Escape'){e.preventDefault();finish(false,true);}
    if(e.key==='Enter'&&(name||e.ctrlKey||e.metaKey)){e.preventDefault();finish(true,true);}
  });
  wrap.addEventListener('focusout',event=>{if(wrap.contains(event.relatedTarget))return;setTimeout(()=>{if(!closed&&!wrap.contains(document.activeElement))finish(true);},0);});
  done.onclick=()=>finish(true,true);undo.onclick=()=>finish(false,true);
  const observer=new ResizeObserver(position);observer.observe(tilt);document.fonts.addEventListener('loadingdone',position);position();
  document.addEventListener('pointerdown',outside,true);
  // Focus synchronously so the iPhone opens its keyboard from the tap gesture.
  input.focus({preventScroll:true});if(name)input.select();
  return {commit:()=>finish(true),cancel:()=>finish(false)};
}
