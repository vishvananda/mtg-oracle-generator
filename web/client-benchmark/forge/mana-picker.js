// Visual cost editing, using the production renderer's mana glyphs.
export function mountManaPicker(host,input,symbolAsset){
  const names={W:'White',U:'Blue',B:'Black',R:'Red',G:'Green',C:'Colorless',S:'Snow',X:'X'};
  const palette=document.createElement('div');palette.className='mana-palette';palette.setAttribute('aria-label','Mana symbols');
  const tokens=document.createElement('div');tokens.className='mana-tokens';tokens.setAttribute('aria-label','Current mana cost');
  function button(symbol,action,label){const b=document.createElement('button');b.type='button';b.setAttribute('aria-label',label);b.title=label;const url=symbolAsset(symbol);if(url){const img=document.createElement('img');img.src=url;img.alt='';b.append(img);}else b.textContent=symbol;b.onclick=action;return b;}
  const parse=()=>[...input.value.matchAll(/\{([^}]+)\}/g)].map(match=>match[1]);
  function change(value){input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));}
  function add(symbol){change(`${input.value}{${symbol}}`);}
  function draw(){tokens.replaceChildren();parse().forEach((symbol,i)=>tokens.append(button(symbol,()=>change(parse().filter((_,at)=>at!==i).map(s=>`{${s}}`).join('')),`Remove ${symbol} at position ${i+1}`)));}
  for(const symbol of ['W','U','B','R','G','C','X','S'])palette.append(button(symbol,()=>add(symbol),`Add ${names[symbol]} mana`));
  const row=document.createElement('div');row.className='generic-mana';
  const genericLabel=document.createElement('label');genericLabel.textContent='Generic';const generic=document.createElement('input');generic.type='number';generic.value='1';generic.min='0';generic.max='999';generic.step='1';genericLabel.append(generic);
  const addGeneric=document.createElement('button');addGeneric.type='button';addGeneric.className='quiet-button';addGeneric.textContent='Add generic';addGeneric.onclick=()=>{const n=Number(generic.value);if(generic.value!==''&&Number.isInteger(n)&&n>=0&&n<=999)add(String(n));};
  const clear=document.createElement('button');clear.type='button';clear.className='quiet-button';clear.textContent='Clear cost';clear.onclick=()=>change('');row.append(genericLabel,addGeneric,clear);
  const more=document.createElement('details'),summary=document.createElement('summary');summary.textContent='Hybrid & Phyrexian';more.className='mana-more';more.append(summary);
  for(const [name,values]of [['Hybrid',['W/U','U/B','B/R','R/G','G/W','W/B','U/R','B/G','R/W','G/U']],['Two or color',['2/W','2/U','2/B','2/R','2/G']],['Phyrexian',['W/P','U/P','B/P','R/P','G/P']]]){
    const label=document.createElement('small');label.textContent=name;const choices=document.createElement('div');choices.className='mana-palette';for(const symbol of values)choices.append(button(symbol,()=>add(symbol),`Add ${symbol} mana`));more.append(label,choices);
  }
  const note=document.createElement('small');note.textContent='Click a symbol in the current cost to remove it. Empty means no mana cost; {0} means zero.';
  host.append(palette,row,tokens,note,more);input.addEventListener('input',draw);draw();
}
