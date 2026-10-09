let catalog;
const load=()=>catalog??=fetch(new URL('./sets.json',import.meta.url)).then(r=>{if(!r.ok)throw Error('Set list could not load. Please try again.');return r.json();}).then(data=>data.sets).catch(error=>{catalog=null;throw error;});
export async function mountSetPicker(host,card,symbol){
  const hidden=document.createElement('input');hidden.type='hidden';hidden.name='set_code';hidden.value=card.set_code||'FORGE';
  const label=document.createElement('label');label.textContent='Find a set';
  const search=document.createElement('input');search.type='search';search.placeholder='Search by set name or code';label.append(search);
  const list=document.createElement('div');list.className='set-list';list.setAttribute('aria-label','Set symbols');
  const note=document.createElement('small');note.textContent='Loading sets…';
  host.append(hidden,label,list,note);
  const sets=await load();let limit=40;
  function draw(){
    list.replaceChildren();const query=search.value.trim().toLowerCase();
    const matches=sets.filter(set=>`${set.name} ${set.code}`.toLowerCase().includes(query));
    for(const set of matches.slice(0,limit)){
      const button=document.createElement('button');button.type='button';button.className='set-option';button.setAttribute('aria-pressed',String(set.code===hidden.value));
      const img=document.createElement('img');img.alt='';img.loading='lazy';img.crossOrigin='anonymous';img.src=symbol(set.code,card.rarity);img.onerror=()=>{img.hidden=true;};
      const title=document.createElement('span');title.textContent=set.name;const code=document.createElement('small');code.textContent=set.code;title.append(code);button.append(img,title);
      button.onclick=()=>{hidden.value=set.code;for(const b of list.querySelectorAll('.set-option'))b.setAttribute('aria-pressed',String(b===button));note.textContent=`Selected: ${set.name}. Save changes to apply.`;};list.append(button);
    }
    if(matches.length>limit){const more=document.createElement('button');more.type='button';more.className='quiet-button';more.textContent='Show more sets';more.onclick=()=>{limit+=40;draw();};list.append(more);}
    note.textContent=matches.length?`${matches.length} sets · selected ${hidden.value}`:'No matching sets.';
  }
  search.oninput=()=>{limit=40;draw();};draw();
}
