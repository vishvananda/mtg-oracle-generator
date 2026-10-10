/** A small direct-choice palette anchored to the card, with native buttons. */
export function finishPicker({button,panel,finishes,onOpen,onChange}){
  let selected='';
  const choices=finishes.map(({id,label})=>{
    const choice=document.createElement('button');choice.type='button';choice.dataset.finish=id;choice.textContent=label;
    choice.onclick=()=>{close(true);onChange(id);};panel.append(choice);return choice;
  });
  function close(focus=false){panel.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus();}
  button.onclick=()=>{
    if(!panel.hidden){close();return;}onOpen();panel.hidden=false;button.setAttribute('aria-expanded','true');
    (choices.find(b=>b.dataset.finish===selected)||choices[0])?.focus();
  };
  function outside(e){if(!panel.hidden&&!panel.contains(e.target)&&!button.contains(e.target))close();}
  document.addEventListener('pointerdown',outside,true);
  panel.onkeydown=e=>{
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();close(true);}
    const at=choices.indexOf(document.activeElement);
    if(['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
      e.preventDefault();e.stopPropagation();const index=e.key==='Home'?0:e.key==='End'?choices.length-1:at+(['ArrowDown','ArrowRight'].includes(e.key)?1:-1);
      choices[(index+choices.length)%choices.length].focus();
    }
  };
  panel.onfocusout=()=>queueMicrotask(()=>{if(!panel.contains(document.activeElement)&&document.activeElement!==button)close();});
  return {get isOpen(){return !panel.hidden;},close,update(id){selected=id;for(const choice of choices)choice.setAttribute('aria-pressed',String(choice.dataset.finish===id));button.title=`Card finish: ${finishes.find(f=>f.id===id)?.label||id}`;},disable(value){button.disabled=value;if(value)close();},dispose(){document.removeEventListener('pointerdown',outside,true);}};
}
