import {scrollPicker,glyph,numbers} from './scroll-picker.js';
import {holdCardSize,matchCardType} from './editing-geometry.js';
import {textRect,relativeRect,ruleItems} from './direct-geometry.js';
export const rarities=['common','uncommon','rare','mythic','special','bonus'];
export const manaValues=['X','Y','Z',...numbers(0,30),'W','U','B','R','G','C','S','W/U','U/B','B/R','R/G','G/W','W/B','U/R','B/G','R/W','G/U','2/W','2/U','2/B','2/R','2/G','W/P','U/P','B/P','R/P','G/P'];
export const parseMana=text=>[...text.matchAll(/\{([^}]+)\}/g)].map(m=>m[1]);
export function orderedMana(tokens){
  // Collapse numeric additions; variables, generic, then colorless/colored/hybrid.
  const numeric=tokens.filter(s=>/^\d+$/.test(s));tokens=tokens.filter(s=>!/^\d+$/.test(s));if(numeric.length)tokens.unshift(String(numeric.reduce((sum,n)=>sum+Number(n),0)));
  const rank=s=>/^\d+$/.test(s)?1:['X','Y','Z'].includes(s)?0:s==='C'?2:s==='S'?3:4+Math.max(0,manaValues.indexOf(s)-manaValues.indexOf('W'));
  return tokens.map((s,i)=>({s,i})).sort((a,b)=>rank(a.s)-rank(b.s)||a.i-b.i).map(x=>`{${x.s}}`).join('');
}
const types=['Artifact','Battle','Creature','Enchantment','Instant','Kindred','Land','Planeswalker','Sorcery'];
const supers=['Legendary','Basic','Snow','World','Ongoing'];
/** Draft ink sits exactly over the printed item. No detached editing panel. */
export function editWithScrollers(host,part,card,{symbols,sets,save,cancel,tick,detail={}}) {
  const draft={...card},layer=document.createElement('div');layer.className='direct-edit-layer';host.append(layer);host.classList.add('direct-editing');
  const release=holdCardSize(host.closest('.card-stage')),hidden=new Map();
  let closed=false,wheel=null,placeWheel=null,selected=detail.index||0,removeCurrent=null,input=null,removeButton=null;
  const live=host.querySelector('.card-design-preview'),get=s=>live.querySelector(s);
  const rect=node=>relativeRect(node.getBoundingClientRect(),layer);
  function hide(node,inkOnly=false){if(!node||hidden.has(node))return;hidden.set(node,node.getAttribute('style'));node.style[inkOnly?'color':'visibility']=inkOnly?'transparent':'hidden';}
  function restore(node){if(!hidden.has(node))return;const original=hidden.get(node);if(original===null)node.removeAttribute('style');else node.setAttribute('style',original);hidden.delete(node);}
  function position(node,r){Object.assign(node.style,{left:`${r.x}px`,top:`${r.y}px`,width:`${r.width}px`,height:`${r.height}px`});}
  function font(node,source){const s=getComputedStyle(source);Object.assign(node.style,{fontFamily:s.fontFamily,fontWeight:s.fontWeight,fontSize:s.fontSize,fontStyle:s.fontStyle,letterSpacing:s.letterSpacing,color:s.color});return s;}
  function button(label,action,className='direct-peer'){
    const b=document.createElement('button');b.type='button';b.className=className;b.setAttribute('aria-label',label);b.onclick=action;layer.append(b);return b;
  }
  function addNear(r,label,action){const b=button(label,action,'direct-add');b.textContent='+';position(b,{x:r.x+r.width+3,y:r.y+r.height/2-10,width:20,height:20});return b;}
  function clearWheel(){wheel?.dispose();wheel=null;placeWheel=null;input?.remove();input=null;}
  function start(options,measure,source){
    clearWheel();wheel=scrollPicker(layer,{...options,tick,commit:()=>end(true,true)});placeWheel=()=>wheel.place(measure(),getComputedStyle(source));placeWheel();wheel.root.focus({preventScroll:true});
  }
  function end(commit,restoreFocus=false){
    if(closed)return;closed=true;clearWheel();for(const node of [...hidden.keys()])restore(node);layer.remove();host.classList.remove('direct-editing');release();
    document.removeEventListener('pointerdown',outside,true);window.removeEventListener('resize',resize);window.removeEventListener('scroll',resize,true);
    if(commit){if(part==='mana'){draft.mana_cost=orderedMana(parseMana(draft.mana_cost));draft.colors=[...new Set(draft.mana_cost.match(/[WUBRG]/g)||[])];}save(draft,restoreFocus);}else cancel(restoreFocus);
  }
  function outside(e){if(!layer.contains(e.target))end(true);}
  function resize(){placeWheel?.();}
  layer.addEventListener('keydown',e=>{
    if(e.key==='Escape'){e.preventDefault();end(false,true);}
    if(e.key==='Enter'&&e.target===input){e.preventDefault();end(true,true);}
    if(['Delete','Backspace'].includes(e.key)&&e.target!==input&&removeCurrent){e.preventDefault();removeCurrent();}
  });
  for(const event of ['pointerdown','pointermove','pointerup','pointercancel','click'])layer.addEventListener(event,e=>e.stopPropagation());
  const tools=document.createElement('div');tools.className='direct-actions';layer.append(tools);
  for(const [label,text,action] of [['Cancel edit','↶',()=>end(false,true)],['Save edit','✓',()=>end(true,true)]]){const b=button(label,action,'direct-action');b.textContent=text;tools.append(b);}
  if(part==='set'){
    const source=get('.rendered-card-set-mark');hide(source);
    const location=()=>source?rect(source):{x:host.clientWidth*.86,y:host.clientHeight*.57,width:24,height:24};
    fetch(new URL('./sets.json',import.meta.url)).then(r=>{if(!r.ok)throw Error('Could not load sets.');return r.json();}).then(data=>{
      if(closed)return;const rows=data.sets.map(s=>({...s,label:s.name}));if(!rows.some(s=>s.code===(draft.set_code||'FORGE')))rows.unshift({code:draft.set_code||'FORGE',label:draft.set_code||'Forge'});
      const render=(b,s,r)=>{
        const mark=document.createElement('span');mark.className='scroll-set-glyph';glyph(mark,s.code,sets(s.code,r));b.append(mark);b.dataset.rarity=r;
        if(!['FORGE','VIZIER'].includes(s.code.toUpperCase()))b.classList.add('monochrome-set');
        if(Number(b.dataset.dy)&&!Number(b.dataset.dx)){const name=document.createElement('span');name.className='scroll-set-name';name.textContent=s.label;name.title=s.label;b.append(name);}
      };
      start({label:'Set and rarity scroller',rows,columns:rarities,row:Math.max(0,rows.findIndex(s=>s.code===(draft.set_code||'FORGE'))),column:Math.max(0,rarities.indexOf(draft.rarity)),render,change:(s,r)=>{draft.set_code=s.code;draft.rarity=r;}},location,source||get('.rendered-card-type'));
      wheel.root.classList.add('set-wheel');
      // Type to jump without opening another surface; every set remains on the wheel.
      let query='',last=0;wheel.root.addEventListener('keydown',e=>{if(e.key.length!==1||e.ctrlKey||e.metaKey)return;e.preventDefault();query=(Date.now()-last>900?'':query)+e.key.toLowerCase();last=Date.now();const i=rows.findIndex(s=>s.code.toLowerCase().startsWith(query)||s.label.toLowerCase().startsWith(query));if(i>=0){wheel.reset(rows,i);draft.set_code=rows[i].code;tick?.();}});
    }).catch(()=>{if(!closed){restore(source);end(false);}});
  }
  if(part==='mana'){
    const source=get('.rendered-card-mana'),title=get('.rendered-card-title');const original=source?.querySelector('.mana-symbol');
    const size=original?.getBoundingClientRect().width||host.clientWidth*.048,gap=host.clientWidth*.00378;
    const box=source?rect(source):{x:host.clientWidth*.92,y:host.clientHeight*.055,width:0,height:size};hide(source);
    let tokens=parseMana(draft.mana_cost),peers=[],add;
    const sync=()=>{draft.mana_cost=tokens.map(s=>`{${s}}`).join('');};
    const location=i=>({x:box.x+box.width-(tokens.length-i)*size-(tokens.length-1-i)*gap,y:box.y,width:size,height:size});
    const activate=i=>{if(removeButton)removeButton.disabled=!tokens.length;selected=i;peers.forEach((p,j)=>p.style.visibility=i===j?'hidden':'visible');if(!tokens.length){clearWheel();return;}
      const current=tokens[i],values=manaValues.includes(current)?manaValues:[...manaValues,current];
      start({label:'Mana symbol scroller',rows:values,row:values.indexOf(current),render:(b,s)=>glyph(b,s,symbols(s)),change:s=>{tokens[selected]=s;sync();peers[selected].replaceChildren();glyph(peers[selected],s,symbols(s));}},()=>location(selected),source||title);
    };
    function draw(){clearWheel();peers.forEach(b=>b.remove());add?.remove();peers=tokens.map((s,i)=>{const b=button(`Edit mana symbol ${i+1}: ${s}`,()=>activate(i));glyph(b,s,symbols(s));position(b,location(i));return b;});
      // + lives just to the left of the cost, preserving the card's right margin.
      const r=location(0);add=addNear({...r,x:r.x-45,width:20},'Add mana symbol',()=>{tokens.unshift('1');selected=0;sync();draw();});activate(Math.min(selected,Math.max(0,tokens.length-1)));
    }
    removeCurrent=()=>{tokens.splice(selected,1);selected=Math.max(0,selected-1);sync();draw();};draw();
  }
  if(part==='stats'){
    const source=get('.rendered-card-stats');if(source){
      const fields=/Planeswalker/i.test(card.type_line)?['loyalty']:['power','toughness'],style=getComputedStyle(source),r=relativeRect(textRect(source),layer);
      const row=document.createElement('div');row.className='direct-stat-line';position(row,r);font(row,source);layer.append(row);hide(source,true);
      const peers=fields.map((key,i)=>{if(i)row.append(document.createTextNode('/'));const b=button(`Edit ${key}`,()=>activate(i));b.textContent=draft[key]??'0';row.append(b);return b;});
      function activate(i){selected=i;peers.forEach(b=>b.style.visibility='visible');const key=fields[i],peer=peers[i],values=[...numbers(key==='loyalty'?0:-20,99),'*','1+*','X'];if(!values.includes(String(draft[key]??0)))values.push(String(draft[key]));const anchor=()=>rect(peer);peer.style.visibility='hidden';
        start({label:`${key} scroller`,rows:values,row:Math.max(0,values.indexOf(String(draft[key]??0))),render:(b,s)=>{b.textContent=s;},change:s=>{draft[key]=s;peer.textContent=s;}},anchor,row);
      }activate(Math.min(selected,fields.length-1));
    }
  }
  if(part==='type'){
    const source=get('.rendered-card-type > span'),r=relativeRect(textRect(source),layer),style=getComputedStyle(source);
    const [head,tail='']=draft.type_line.split(/\s+[—–]\s+/);let words=head.split(/\s+/),subtypes=tail,peers=[];
    const row=document.createElement('div');row.className='direct-type-line';position(row,{...r,width:host.clientWidth*.77});font(row,source);layer.append(row);hide(source);
    const sync=()=>{draft.type_line=[...new Set(words)].join(' ')+(subtypes.trim()?` — ${subtypes.trim()}`:'');};
    const canRemove=()=>supers.includes(words[selected])||words.filter(word=>!supers.includes(word)).length>1;
    const removeType=button('Remove type',()=>removeCurrent(),'direct-type-remove');
    function updateRemove(at){
      removeType.hidden=false;removeType.textContent=`× Remove ${words[selected]}`;removeType.setAttribute('aria-label',`Remove ${words[selected]}`);
      removeType.disabled=!canRemove();removeType.title=canRemove()?'Remove this type':'Keep at least one card type';
      const width=removeType.offsetWidth,edge=layer.clientWidth-6;
      let x=at.x+at.width+20,y=at.y+at.height+10;
      if(x+width>edge)x=at.x-width-20;
      // Long type names on narrow cards leave no side room: use the space below
      // the fan instead of covering either the choices or the neighboring ink.
      if(x<6){x=Math.max(6,Math.min(edge-width,at.x));y=at.y+at.height+2*parseFloat(wheel.root.style.getPropertyValue('--step-y'))+12;}
      Object.assign(removeType.style,{left:`${x}px`,top:`${y}px`});
    }
    function activate(i){selected=i;peers.forEach(b=>b.style.visibility='visible');const peer=peers[i],current=words[i],values=[...(supers.includes(current)?supers:types)];if(!values.includes(current))values.push(current);const at=rect(peer);peer.style.visibility='hidden';
      start({label:supers.includes(current)?'Supertype scroller':'Card type scroller',rows:values,row:values.indexOf(current),render:(b,s)=>{b.textContent=s;},change:s=>{words[selected]=s;peer.textContent=s;sync();updateRemove(at);}},()=>at,row);updateRemove(at);
    }
    function editSubtypes(peer){clearWheel();removeType.hidden=true;peers.forEach(b=>b.style.visibility='visible');input=document.createElement('input');input.className='direct-subtypes';input.setAttribute('aria-label','Subtypes');input.value=subtypes;matchCardType(input,style);const at=rect(peer);position(input,{...at,width:Math.max(at.width,100)});layer.append(input);peer.style.visibility='hidden';input.oninput=()=>{subtypes=input.value;sync();};input.focus({preventScroll:true});input.select();}
    function draw(){clearWheel();row.replaceChildren();peers=words.map((word,i)=>{if(i)row.append(document.createTextNode(' '));const b=button(`Edit type ${word}`,()=>activate(i));b.textContent=word;row.append(b);return b;});
      row.append(document.createTextNode(' — '));const subtype=button('Edit subtypes',()=>editSubtypes(subtype));subtype.textContent=subtypes||'subtype';row.append(subtype);
      const add=button('Add card type',()=>{words.push(types.find(t=>!words.includes(t))||'Creature');selected=words.length-1;sync();draw();},'direct-add');add.textContent='+';row.append(add);
      const superAdd=button('Add supertype',()=>{words.unshift(supers.find(s=>!words.includes(s))||'Legendary');selected=0;sync();draw();},'direct-add');superAdd.textContent='+';row.prepend(superAdd);
      if(detail.subtypes){detail.subtypes=false;editSubtypes(subtype);}else activate(Math.min(selected,words.length-1));
    }
    removeCurrent=()=>{if(!canRemove())return;words.splice(selected,1);selected=Math.max(0,selected-1);sync();draw();};draw();
  }
  if(part==='rule-symbol'){
    const item=ruleItems(live,card.oracle_text||'')[selected];if(item){const {node,start:from,end:to,value,loyalty}=item;hide(node);
      const rows=loyalty?[...numbers(-20,20).map(n=>Number(n)>0?`+${n}`:n),'-X','+X']:manaValues.concat(['T','Q','E']);if(!rows.includes(value))rows.push(value);
      start({label:loyalty?'Loyalty ability cost scroller':'Rules mana symbol scroller',rows,row:rows.indexOf(value),render:(b,s)=>loyalty?b.textContent=s:glyph(b,s,symbols(s)),change:s=>{draft.oracle_text=card.oracle_text.slice(0,from)+(loyalty?s:`{${s}}`)+card.oracle_text.slice(to);}},()=>rect(node),node);
    }
  }
  if(part==='mana'&&removeCurrent){removeButton=button('Remove symbol',()=>{removeCurrent();removeButton.disabled=!wheel;},'direct-action');removeButton.textContent='−';removeButton.disabled=!wheel;tools.prepend(removeButton);}
  document.addEventListener('pointerdown',outside,true);window.addEventListener('resize',resize);window.addEventListener('scroll',resize,true);
  return {commit:()=>end(true),cancel:()=>end(false)};
}
