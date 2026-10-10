import {scrollPicker,glyph,numbers} from './scroll-picker.js';
import {holdCardSize} from './editing-geometry.js';
export const rarities=['common','uncommon','rare','mythic','special','bonus'];
export const manaValues=['X','Y','Z',...numbers(0,30),'W','U','B','R','G','C','S','W/U','U/B','B/R','R/G','G/W','W/B','U/R','B/G','R/W','G/U','2/W','2/U','2/B','2/R','2/G','W/P','U/P','B/P','R/P','G/P'];
export const parseMana=text=>[...text.matchAll(/\{([^}]+)\}/g)].map(m=>m[1]);
export function orderedMana(tokens){
  // Collapse numeric additions; variables, generic, then colorless/colored/hybrid.
  const numeric=tokens.filter(s=>/^\d+$/.test(s));tokens=tokens.filter(s=>!/^\d+$/.test(s));if(numeric.length)tokens.unshift(String(numeric.reduce((sum,n)=>sum+Number(n),0)));
  const rank=s=>/^\d+$/.test(s)?1:['X','Y','Z'].includes(s)?0:s==='C'?2:s==='S'?3:4+Math.max(0,manaValues.indexOf(s)-manaValues.indexOf('W'));
  return tokens.map((s,i)=>({s,i})).sort((a,b)=>rank(a.s)-rank(b.s)||a.i-b.i).map(x=>`{${x.s}}`).join('');
}
function button(host,label,click){const b=document.createElement('button');b.type='button';b.textContent=label;b.onclick=click;host.append(b);return b;}
function input(host,label,value,change){const l=document.createElement('label');l.textContent=label;const i=document.createElement('input');i.value=value??'';i.setAttribute('aria-label',label);i.oninput=()=>change(i.value);l.append(i);host.append(l);return i;}
/** Anchored editing session. Drafts paint immediately, but only Done persists. */
export function editWithScrollers(host,part,card,{symbols,sets,preview,save,cancel}) {
  const draft={...card},panel=document.createElement('section');panel.className='card-scroll-editor';panel.setAttribute('role','dialog');panel.setAttribute('aria-label',`${part} editor`);
  const heading=document.createElement('header'),title=document.createElement('strong');title.textContent={set:'Set & rarity',mana:'Mana cost',stats:/Planeswalker/.test(card.type_line)?'Starting loyalty':'Power & toughness',type:'Card type'}[part];heading.append(title);panel.append(heading);
  const content=document.createElement('div');content.className='card-scroll-content';panel.append(content);
  const hint=document.createElement('small');hint.className='scroll-help';hint.textContent=part==='set'?'↑↓ Set · ←→ Rarity · scroll, drag, or hover near an edge':'Scroll, drag, or use arrow keys · hover near an edge to keep scrolling';panel.append(hint);
  const actions=document.createElement('footer');panel.append(actions);document.body.append(panel);host.classList.add('scroll-editing');
  const release=holdCardSize(host.closest('.card-stage'));let pickers=[],closed=false,scheduled;
  const update=()=>{clearTimeout(scheduled);scheduled=setTimeout(()=>preview({...draft}),100);};
  const clear=()=>{pickers.forEach(p=>p.dispose());pickers=[];content.replaceChildren();};
  function picker(options){const p=scrollPicker(content,options);pickers.push(p);return p;}
  function end(commit,restore=true){if(closed)return;closed=true;clearTimeout(scheduled);clear();panel.remove();host.classList.remove('scroll-editing');release();document.removeEventListener('pointerdown',outside,true);window.removeEventListener('resize',position);if(commit){if(part==='mana'){draft.mana_cost=orderedMana(parseMana(draft.mana_cost));draft.colors=[...new Set(draft.mana_cost.match(/[WUBRG]/g)||[])];}save(draft,restore);}else cancel(restore);}
  button(actions,'Cancel',()=>end(false));button(actions,'Done',()=>end(true));
  function outside(e){if(!panel.contains(e.target)&&!host.contains(e.target))end(true,false);}
  // Stop card tilt/click targets capturing drag gestures.
  for(const event of ['pointerdown','pointermove','pointerup','click'])panel.addEventListener(event,e=>e.stopPropagation());
  panel.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();end(false);}if(e.key==='Enter'&&!e.target.matches('button')){e.preventDefault();end(true);}});
  function position(){const cardBox=host.getBoundingClientRect(),touch=matchMedia('(pointer:coarse)').matches;panel.style.width=`${Math.min(touch?330:310,innerWidth-24)}px`;panel.style.left=`${Math.max(12,Math.min(innerWidth-panel.offsetWidth-12,cardBox.right+12))}px`;panel.style.top=`${Math.max(10,Math.min(innerHeight-panel.offsetHeight-12,cardBox.top+cardBox.height*({set:.5,mana:.03,type:.48,stats:.66}[part]||0)))}px`;if(innerWidth<900)panel.style.left=`${Math.max(12,Math.min(innerWidth-panel.offsetWidth-12,cardBox.left+(cardBox.width-panel.offsetWidth)/2))}px`;}
  if(part==='set'){
    const search=input(content,'Find a set','',value=>{const filtered=all.filter(s=>`${s.label} ${s.code}`.toLowerCase().includes(value.toLowerCase()));if(filtered.length)wheel.reset(filtered,Math.max(0,filtered.findIndex(s=>s.code===draft.set_code)));note.textContent=filtered.length?'':'No matching sets';});search.type='search';
    let all=[],wheel;const note=document.createElement('small');note.textContent='Loading sets…';content.append(note);
    fetch(new URL('./sets.json',import.meta.url)).then(r=>{if(!r.ok)throw Error('Could not load sets.');return r.json();}).then(data=>{
      if(closed)return;all=data.sets.map(s=>({...s,label:s.name}));
      if(!all.some(s=>s.code===(draft.set_code||'FORGE')))all.unshift({code:draft.set_code||'FORGE',label:draft.set_code||'Forge'});
      wheel=picker({label:'Set and rarity scroller',rows:all,columns:rarities,row:Math.max(0,all.findIndex(s=>s.code===(draft.set_code||'FORGE'))),column:Math.max(0,rarities.indexOf(draft.rarity)),render:(b,s,r)=>{glyph(b,s.code,sets(s.code,r));b.dataset.rarity=r;if(!['FORGE','VIZIER'].includes(s.code.toUpperCase()))b.classList.add('monochrome-set');},change:(s,r)=>{draft.set_code=s.code;draft.rarity=r;update();}});note.textContent='';position();wheel.root.focus({preventScroll:true});
    }).catch(error=>{if(!closed)note.textContent=error.message;});
  }
  if(part==='mana'){
    let tokens=parseMana(draft.mana_cost),selected=0;
    const draw=()=>{
      clear();const bar=document.createElement('div');bar.className='symbol-strip';content.append(bar);
      tokens.forEach((s,i)=>{const b=button(bar,'',()=>{selected=i;draw();});glyph(b,s,symbols(s));b.setAttribute('aria-label',`Edit mana symbol ${i+1}: ${s}`);b.setAttribute('aria-pressed',String(i===selected));});
      button(bar,'+',()=>{tokens.push('1');selected=tokens.length-1;sync();draw();}).setAttribute('aria-label','Add mana symbol');
      if(tokens.length){const current=tokens[selected],values=manaValues.includes(current)?manaValues:[...manaValues,current];picker({label:'Mana symbol scroller',rows:values,row:values.indexOf(current),render:(b,s)=>glyph(b,s,symbols(s)),change:s=>{tokens[selected]=s;sync();const b=bar.children[selected];b.replaceChildren();glyph(b,s,symbols(s));b.setAttribute('aria-label',`Edit mana symbol ${selected+1}: ${s}`);}});button(content,'Remove symbol',()=>{tokens.splice(selected,1);selected=Math.max(0,selected-1);sync();draw();});}
      else{const note=document.createElement('p');note.textContent='No mana cost. Tap + to add a symbol.';content.append(note);}
      position();
    };
    function sync(){draft.mana_cost=tokens.map(s=>`{${s}}`).join('');draft.colors=[...new Set(draft.mana_cost.match(/[WUBRG]/g)||[])];update();}draw();
  }
  if(part==='stats'){
    const fields=/Planeswalker/i.test(card.type_line)?['loyalty']:['power','toughness'];let key=fields[0];
    const draw=()=>{clear();const tabs=document.createElement('div');tabs.className='symbol-strip';content.append(tabs);for(const k of fields){const b=button(tabs,`${k}: ${draft[k]??'0'}`,()=>{key=k;draw();});b.setAttribute('aria-pressed',String(key===k));}
      const values=[...numbers(key==='loyalty'?0:-20,99),'*','1+*','X'];if(draft[key]!=null&&!values.includes(String(draft[key])))values.push(String(draft[key]));
      picker({label:`${key} scroller`,rows:values,row:Math.max(0,values.indexOf(String(draft[key]??0))),render:(b,s)=>{b.textContent=s;},change:s=>{draft[key]=s;tabs.children[fields.indexOf(key)].textContent=`${key}: ${s}`;update();}});position();};draw();
  }
  if(part==='type'){
    const supertypes=['Legendary','Basic','Snow','World','Ongoing'];const types=['Artifact','Battle','Creature','Enchantment','Instant','Kindred','Land','Planeswalker','Sorcery'];
    const [head,tail='']=draft.type_line.split(/\s+[—–]\s+/);let supers=head.split(' ').filter(s=>supertypes.includes(s)),chosen=head.split(' ').filter(s=>!supertypes.includes(s)),subtypes=tail,selected=0;
    function sync(){draft.type_line=[...supers,...new Set(chosen)].join(' ')+(subtypes.trim()?` — ${subtypes.trim()}`:'');update();}
    const draw=()=>{clear();const superbar=document.createElement('div');superbar.className='type-toggles';content.append(superbar);for(const s of supertypes){const b=button(superbar,s,()=>{supers=supers.includes(s)?supers.filter(x=>x!==s):[...supers,s];b.setAttribute('aria-pressed',String(supers.includes(s)));sync();});b.setAttribute('aria-pressed',String(supers.includes(s)));}
      const bar=document.createElement('div');bar.className='symbol-strip';content.append(bar);chosen.forEach((s,i)=>{const b=button(bar,s,()=>{selected=i;draw();});b.setAttribute('aria-pressed',String(i===selected));});button(bar,'+',()=>{chosen.push(types.find(t=>!chosen.includes(t))||'Creature');selected=chosen.length-1;sync();draw();}).setAttribute('aria-label','Add card type');
      const values=types.includes(chosen[selected])?types:[...types,chosen[selected]];picker({label:'Card type scroller',rows:values,row:Math.max(0,values.indexOf(chosen[selected])),render:(b,s)=>{b.textContent=s;},change:s=>{chosen[selected]=s;bar.children[selected].textContent=s;sync();}});
      if(chosen.length>1)button(content,'Remove type',()=>{chosen.splice(selected,1);selected=Math.max(0,selected-1);sync();draw();});
      input(content,'Subtypes',subtypes,value=>{subtypes=value;sync();}).placeholder='Elk Spirit';position();};draw();
  }
  window.addEventListener('resize',position);document.addEventListener('pointerdown',outside,true);position();panel.querySelector('.scroll-picker')?.focus({preventScroll:true});
  return {commit:()=>end(true,false),cancel:()=>end(false,false)};
}
