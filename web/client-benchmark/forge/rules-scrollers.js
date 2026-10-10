import {scrollPicker,glyph,numbers} from './scroll-picker.js';
import {manaValues} from './card-scrollers.js';
/** Symbol and signed loyalty wheels operate on text ranges, preserving other wording. */
export function rulesScrollers(wrap,input,symbol){
  const bar=document.createElement('div');bar.className='inline-symbol-tools';wrap.append(bar);
  let picker,holder,range=null,selection=[0,0];
  const remember=()=>{selection=[input.selectionStart,input.selectionEnd];};
  input.addEventListener('select',remember);input.addEventListener('keyup',remember);input.addEventListener('pointerup',remember);
  const button=(label,action)=>{const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=action;bar.append(b);return b;};
  function close(){picker?.dispose();picker=null;holder?.remove();holder=null;range=null;}
  function open(start,end,value,loyalty=false){
    close();range=[start,end];holder=document.createElement('div');holder.className='inline-symbol-picker card-scroll-editor';holder.style.position='absolute';wrap.append(holder);
    const values=loyalty?[...numbers(-20,20).map(n=>Number(n)>0?`+${n}`:n),'-X','+X']:manaValues.concat(['T','Q','E']);
    const canonical=value.replace('−','-');if(!values.includes(canonical))values.push(canonical);const index=values.indexOf(canonical);
    picker=scrollPicker(holder,{label:loyalty?'Loyalty ability cost scroller':'Rules mana symbol scroller',rows:values,row:index,
      render:(b,s)=>{if(loyalty)b.textContent=s;else glyph(b,s,symbol(s));},
      change:s=>{const text=loyalty?s:`{${s}}`;input.setRangeText(text,...range,'preserve');range[1]=range[0]+text.length;draw();},
    });
    const done=document.createElement('button');done.type='button';done.textContent='Done';done.onclick=()=>{close();input.focus({preventScroll:true});};holder.append(done);picker.root.focus({preventScroll:true});
  }
  function draw(){
    bar.replaceChildren();
    for(const m of input.value.matchAll(/\{([^}\n]+)\}|^([+−-]?(?:\d+|X))(?=:)/gm)){
      const loyalty=Boolean(m[2]),value=m[2]||m[1];const b=button(loyalty?value:'',()=>open(m.index,m.index+m[0].length,value,loyalty));
      b.setAttribute('aria-label',loyalty?`Edit loyalty cost ${value}`:`Edit rules symbol ${value}`);if(!loyalty)glyph(b,value,symbol(value));
    }
    button('+ Mana',()=>{const start=selection[0],end=selection[1];input.setRangeText('{1}',start,end,'end');draw();open(start,start+3,'1');});
  }
  input.addEventListener('input',()=>{close();draw();});draw();
  return {contains:node=>bar.contains(node)||holder?.contains(node),dispose:close};
}
