import {demos,parseDraft,artPrompt} from './cards.js';
import {mountRenderer} from './renderer.js';
import {editOnCard} from './inline-editor.js';
import {savedCards,saveCards} from './storage.js';
import {Sound} from './sound.js';
import {editWithScrollers} from './card-scrollers.js';
import {isMobileDevice} from '/model-bench/device-support.js';
const $=id=>document.getElementById(id), sound=new Sound();
let models={ready:false,cancel(){},dispose(){}};
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
let generated=[],cards=demos.map(c=>({...c})),index=0,preview,busy=false,loading=false,editor=null,paused=reduced.matches,lastChange=Date.now(),timer,hovering=false;
let pendingArt=null;
const active=()=>cards[index];
let previewError=false,pendingEditor=null;
$('card-preview').addEventListener('preview-error',()=>{previewError=true;message('The card could not finish loading. Reload the page to retry.',true);});
$('card-preview').addEventListener('preview-pending',()=>controls());
$('card-preview').addEventListener('preview-ready',()=>{controls();if(previewError){previewError=false;message('');}if(pendingEditor){const next=pendingEditor;pendingEditor=null;if(next.id===active().id)openEditor(next.part,next.detail);}});
function message(text,error=false){for(const id of ['message','mobile-message']){$(id).textContent=text;$(id).classList.toggle('error',error);}}
function controls(){
  const previewPending=$('card-preview').getAttribute('aria-busy')==='true'&&!$('card-preview').dataset.previewAvailable;
  $('load-panel').hidden=models.ready;
  $('create-form').hidden=!models.ready;
  $('load-button').disabled=busy||loading;
  $('load-label').textContent=loading?'Opening the forge…':'Open the forge';
  $('generate-button').disabled=busy||loading;
  $('description').disabled=busy||loading;
  $('stop-button').hidden=!busy||loading;
  $('previous').disabled=busy||cards.length<2;$('next').disabled=busy||cards.length<2;
  $('browse-examples').hidden=!generated.length;$('browse-examples').disabled=busy||loading;$('browse-examples').textContent=active().demo?'Back to your cards':'Browse examples';
  $('finish').disabled=busy;$('card-details').disabled=busy||previewPending;$('export-card').disabled=busy||previewPending;
  $('edit-own-card').disabled=busy||loading||previewPending;$('edit-own-card').querySelector('span').textContent=active().demo?'Edit your own card':'Edit this card';
  $('pause-carousel').setAttribute('aria-pressed',String(paused));$('pause-carousel').setAttribute('aria-label',paused?'Play carousel':'Pause carousel');$('pause-carousel').textContent=paused?'▷':'Ⅱ';
  document.body.classList.toggle('studio-ready',models.ready);
  preview?.setBusy(busy);
  for(const b of $('dots').children)b.disabled=busy;
}
function draw(reveal=false){
  $('finish').value=active().finish_id||'vizier_etched_v1';
  preview?.update(active(),busy?'ordinary':$('finish').value,editor?.part);
  $('card-caption').textContent=active().name;
  $('card-counter').textContent=`${String(index+1).padStart(2,'0')} / ${String(cards.length).padStart(2,'0')}`;
  $('gallery-kind').textContent=active().demo?'Examples':'Your cards';
  $('dots').replaceChildren();
  cards.forEach((card,i)=>{const b=document.createElement('button');b.type='button';b.setAttribute('aria-label',`Show ${card.name}`);b.setAttribute('aria-current',String(i===index));b.onclick=()=>show(i);$('dots').append(b);});
  if(reveal&&!reduced.matches){$('card-holder').classList.remove('reveal');void $('card-holder').offsetWidth;$('card-holder').classList.add('reveal');}
  controls();
}
function show(i){if(busy||editor)return;index=(i+cards.length)%cards.length;lastChange=Date.now();draw(true);}
async function persist(){try{await saveCards(generated);}catch{message('This browser could not save the card. Use Save card to keep a copy.',true);}}
function adoptDemo(){
  if(!active().demo)return;
  const copy={...active(),id:crypto.randomUUID(),demo:false,created_at:new Date().toISOString()};
  // Static sample artwork retains its own attribution and survives reloads.
  copy.demo_image=active().image; generated.push(copy);cards=generated;index=cards.length-1;draw();
}
function appendCard(card){
  generated.push(card);
  if(generated.length>20){const old=generated.shift();if(old.illustration?.startsWith('blob:'))URL.revokeObjectURL(old.illustration);}
  cards=generated;index=cards.length-1;lastChange=Date.now();draw(true);
}
function start(phase){busy=true;message('');closeEditor(false);$('generation-progress').hidden=false;$('card-busy').hidden=false;$('generation-stage').textContent=phase;const began=performance.now();timer=setInterval(()=>{$('generation-time').textContent=`${((performance.now()-began)/1000).toFixed(1)}s`;},100);sound.play('start');draw();}
function finish(){busy=false;clearInterval(timer);$('generation-progress').hidden=true;$('card-busy').hidden=true;lastChange=Date.now();draw();}
function stage(text){$('generation-stage').textContent=text;}
async function paint(card,prompt){
  card.art_prompt=prompt;const result=await models.art(prompt,stage);
  if(card.illustration?.startsWith('blob:'))URL.revokeObjectURL(card.illustration);
  card.art_blob=result.blob;card.illustration=URL.createObjectURL(result.blob);card.artist??='MTG CardForge';delete card.demo_image;
  card.art_settings={seed:result.seed,width:result.width,height:result.height,steps:result.steps,generation_ms:result.total_ms};
}
async function generate(){
  if(busy||loading||!models.ready)return;
  const description=$('description').value.trim();if(!description)return;
  start('Imagining your card…');let card;
  try {
    const result=await models.card(description,stage),draft=parseDraft(result.text);
    card={...draft,id:crypto.randomUUID(),demo:false,created_at:new Date().toISOString(),description,text_seed:result.seed,artist:'MTG CardForge',art_prompt:artPrompt(description,draft)};
    appendCard(card);await persist();stage('Imagining the scene…');
    await paint(card,card.art_prompt);await persist();sound.play('complete');message('Card ready.');$('generate-label').textContent='Create another';
  }catch(error){message(error.name==='AbortError'?'Stopped. Any completed card text has been kept.':`${error.message}${card?' Your card text has been kept. Click its artwork to retry.':''}`,error.name!=='AbortError');}
  finally{finish();}
}
async function regenerateArt(card,prompt){
  if(busy||loading)return;
  if(!models.ready){pendingArt={id:card.id,prompt};closeEditor();message('Load the models on the left, then the new artwork will be generated.');$('load-button').focus();return;}
  adoptDemo();card=active();start('Imagining the scene…');
  try{await paint(card,prompt);await persist();sound.play('complete');message('Artwork updated.');}
  catch(error){message(error.name==='AbortError'?'Artwork generation stopped.':error.message,error.name!=='AbortError');}
  finally{finish();}
}
$('load-button').onclick=async()=>{
  if(isMobileDevice)return;
  if(busy||loading)return;loading=true;message('');controls();sound.play('load');$('load-progress').hidden=false;
  try{
    if(!models.load){const {LocalModels}=await import('./local-models.js');models=new LocalModels();}
    await models.load((text,fraction)=>{$('load-status').textContent=text;$('model-progress').value=fraction;});
    sound.play('complete');
  }catch(error){message(error.message,true);}
  finally{loading=false;controls();if(models.ready)$('description').focus();}
  if(models.ready&&pendingArt){const request=pendingArt;pendingArt=null;const at=cards.findIndex(c=>c.id===request.id);if(at>=0){index=at;draw();await regenerateArt(active(),request.prompt);}}
};
$('create-form').onsubmit=e=>{e.preventDefault();void generate();};
$('stop-button').onclick=()=>{models.cancel();stage('Stopping…');};
$('previous').onclick=()=>show(index-1);$('next').onclick=()=>show(index+1);
$('pause-carousel').onclick=()=>{paused=!paused;lastChange=Date.now();controls();};
$('gallery').onpointerenter=()=>{hovering=true;};$('gallery').onpointerleave=()=>{hovering=false;lastChange=Date.now();};
$('gallery').addEventListener('keydown',e=>{if(editor||busy||e.target.matches('input,textarea,select'))return;if(e.key==='ArrowRight'){e.preventDefault();show(index+1);}if(e.key==='ArrowLeft'){e.preventDefault();show(index-1);}});
setInterval(()=>{if(!paused&&!reduced.matches&&!busy&&!loading&&!editor&&!hovering&&!document.hidden&&!$('gallery').contains(document.activeElement)&&Date.now()-lastChange>=10000)show(index+1);},500);
document.addEventListener('visibilitychange',()=>{lastChange=Date.now();});
$('finish').onchange=()=>{const finish=$('finish').value;closeEditor(false);adoptDemo();active().finish_id=finish;draw();void persist();};
function field(name,label,value,{textarea=false,type='text',options}={}){
  const container=document.createElement('label');container.textContent=label;
  const input=document.createElement(options?'select':textarea?'textarea':'input');input.name=name;input.id=`edit-${name}`;
  if(options)for(const value of options){const option=document.createElement('option');option.value=value;option.textContent=value[0].toUpperCase()+value.slice(1);input.append(option);}
  else if(!textarea)input.type=type;
  input.value=value??'';if(textarea){input.rows=name==='art_prompt'?7:5;input.maxLength=name==='art_prompt'?4000:5000;}else if(!options)input.maxLength=500;
  container.append(input);$('edit-fields').append(container);return input;
}
function artFields(card,mode) {
  $('edit-fields').replaceChildren();
  if(!isMobileDevice&&models.ready){
    const select=field('art_mode','Artwork source',mode,{options:['generate','url']});
    select.options[0].textContent='Generate from a prompt';select.options[1].textContent='Image URL or file';
    select.onchange=()=>artFields(card,select.value);
  }else{const hidden=document.createElement('input');hidden.type='hidden';hidden.name='art_mode';hidden.value='url';$('edit-fields').append(hidden);mode='url';}
  $('edit-save').textContent=mode==='generate'?'Regenerate art':'Use artwork';
  if(mode==='generate')field('art_prompt','Describe the scene',card.art_prompt||artPrompt(card.description||card.name,card),{textarea:true}).required=true;
  else{field('art_url','Image URL',card.art_source_url||'',{type:'url'}).placeholder='https://example.com/illustration.jpg';field('art_file','Or choose an image','',{type:'file'}).accept='image/png,image/jpeg,image/webp,image/avif';field('artist','Artist credit',card.artist);}
  const note=document.createElement('small');note.textContent=mode==='generate'?'768 × 512 landscape · 4 steps. Only the artwork changes.':'Your image fills the art window automatically. URLs need browser access; a file works with any image host. Up to 12 MB.';$('edit-fields').append(note);
}
async function importArtwork(data){
  const button=$('edit-save');button.disabled=true;button.textContent='Importing…';$('edit-error').textContent='';
  const target=editor;
  try{
    const {readArtwork}=await import('./import-art.js');const {blob,source}=await readArtwork(data);
    if(editor!==target)return;
    adoptDemo();const card=active();if(card.illustration?.startsWith('blob:'))URL.revokeObjectURL(card.illustration);
    card.art_blob=blob;card.illustration=URL.createObjectURL(blob);card.art_source_url=source;card.artist=String(data.artist||'');delete card.demo_image;delete card.art_settings;
    closeEditor();draw(true);await persist();sound.play('complete');message('Artwork updated.');
  }catch(error){if(editor===target)$('edit-error').textContent=error.message;}
  finally{button.disabled=false;button.textContent='Use artwork';}
}
function openEditor(part,detail={}){
  if(busy||loading)return;
  if($('card-preview').getAttribute('aria-busy')==='true'){pendingEditor={part,detail,id:active().id};return;}
  if(editor?.inline){if(editor.part===part)return;editor.inline.commit();}
  else if(editor)closeEditor(false);
  if(part==='name'||part==='rules'){
    const card=active(),key=part==='name'?'name':'oracle_text';
    const original=part==='rules'?(card[key]||'').replaceAll('CARDNAME',card.name):card.name;
    const session={part,id:card.id};editor=session;lastChange=Date.now();sound.play('edit');
    function end(value,restore){
      editor=null;
      const next=part==='name'?(value.trim()||original):value.trim();
      if(next!==original){
        adoptDemo();const card=active();card[key]=next;
        if(part==='name'&&card.oracle_text)card.oracle_text=card.oracle_text.replaceAll(original,'CARDNAME');
        card.updated_at=new Date().toISOString();void persist();message('Saved on this device.');
      }
      draw();lastChange=Date.now();if(restore)preview.focus(part);
    }
    session.inline=editOnCard($('card-preview'),part,original,{save:end,cancel:restore=>end(original,restore),symbol:preview.manaSymbol});
    return;
  }
  if(['set','mana','stats','type','rule-symbol'].includes(part)){
    const session={part,id:active().id};editor=session;lastChange=Date.now();sound.play('edit');
    session.inline=editWithScrollers($('card-preview'),part,active(),{
      symbols:preview.manaSymbol,sets:preview.setSymbol,detail,tick:()=>sound.play('tick'),
      save:(draft,restore)=>{editor=null;const changed=JSON.stringify(draft)!==JSON.stringify(active());if(changed){adoptDemo();Object.assign(active(),draft,{id:active().id,demo:false,updated_at:new Date().toISOString()});void persist();}draw();lastChange=Date.now();if(restore)preview.focus(part);},
      cancel:restore=>{editor=null;draw();lastChange=Date.now();if(restore)preview.focus(part);},
    });return;
  }
  const c=active();editor={part,id:c.id};sound.play('edit');lastChange=Date.now();
  $('edit-fields').replaceChildren();$('edit-error').textContent='';$('edit-panel').hidden=false;$('gallery').classList.add('editing');
  $('edit-heading').textContent=({name:'Give it a name',mana:'Mana cost',type:'Type & rarity',rules:'Shape its abilities',stats:'Strength & resilience',art:'Imagine the artwork',details:'The finishing touches',set:'Choose a set',artist:'Artist credit',border:'Choose the border',frame:'Choose the frame'})[part];
  $('edit-save').textContent='Save changes';
  $('edit-panel').classList.toggle('quick-options',['frame','border'].includes(part));
  if(part==='frame'||part==='border'){
    const key=part==='frame'?'frame_style':'border_color',options=part==='frame'?[['auto','Match set'],['modern','Modern'],['retro','Old frame']]:[['auto','Match set'],['black','Black'],['white','White']];
    for(const [value,label] of options){const button=document.createElement('button');button.type='button';button.className='direct-option';button.textContent=label;button.setAttribute('aria-pressed',String((c[key]||'auto')===value));button.onclick=()=>{adoptDemo();active()[key]=value;active().updated_at=new Date().toISOString();closeEditor(false);draw();void persist();sound.play('click');};$('edit-fields').append(button);}
  }
  if(part==='artist')field('artist','Artist credit',c.artist);
  if(part==='details'){field('artist','Artist',c.artist);field('rarity','Rarity',c.rarity,{options:['common','uncommon','rare','mythic','special','bonus']});}
  if(part==='art'){
    artFields(c,models.ready&&!isMobileDevice?'generate':'url');
  }
  const anchor={name:'5%',mana:'5%',type:'46%',rules:'54%',stats:'58%',details:'58%',art:'17%',set:'30%',artist:'58%',border:'30%',frame:'30%'}[part];$('edit-panel').style.top=matchMedia('(max-width:760px)').matches?'auto':anchor;
  draw();$('edit-fields').querySelector('input:not([type="hidden"]),textarea,select,button')?.focus();
}
function closeEditor(restore=true){if(editor?.inline){editor.inline.commit();return;}const part=editor?.part;editor=null;$('edit-panel').hidden=true;$('gallery').classList.remove('editing');preview?.update(active(),$('finish').value,null);lastChange=Date.now();if(restore&&part)preview?.focus(part);}
$('edit-close').onclick=()=>closeEditor();$('edit-cancel').onclick=()=>closeEditor();$('card-details').onclick=()=>openEditor('details');
$('edit-own-card').onclick=()=>openEditor('name');
$('browse-examples').onclick=()=>{if(busy||loading)return;closeEditor(false);cards=active().demo?generated:demos.map(c=>({...c}));index=0;pendingEditor=null;lastChange=Date.now();draw(true);};
$('edit-panel').addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();closeEditor();}
  if(e.key==='Tab'){const items=[...$('edit-panel').querySelectorAll('button,input,textarea,select')].filter(e=>!e.disabled&&e.type!=='hidden');const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}
});
$('edit-form').onsubmit=async e=>{
  e.preventDefault();if(!editor||busy)return;const data=Object.fromEntries(new FormData(e.currentTarget)),part=editor.part;
  if(part==='art'){
    if(data.art_mode==='generate'){const prompt=data.art_prompt.trim();if(prompt)await regenerateArt(active(),prompt);}
    else await importArtwork(data);
    return;
  }
  if('mana_cost' in data&&!/^(?:\{[0-9WUBRGCSXYP/∞]+\}\s*)*$/i.test(data.mana_cost.trim())){$('edit-error').textContent='Use mana symbols in braces, such as {2}{U}{U}. Leave this empty for a land.';return;}
  if('colors' in data&&!/^[WUBRG\s,]*$/i.test(data.colors)){$('edit-error').textContent='Use W, U, B, R, or G, separated by spaces.';return;}
  adoptDemo();const c=active(),oldName=c.name;
  for(const [key,value]of Object.entries(data))c[key]=key==='colors'?[...new Set(value.toUpperCase().match(/[WUBRG]/g)||[])]:value.trim();
  if('mana_cost'in data)c.colors=[...new Set(c.mana_cost.toUpperCase().match(/[WUBRG]/g)||[])];
  if('name'in data&&oldName!==c.name&&c.oracle_text)c.oracle_text=c.oracle_text.replaceAll(oldName,'CARDNAME');
  c.updated_at=new Date().toISOString();closeEditor();draw();await persist();sound.play('click');
};
function download(blob,name){const href=URL.createObjectURL(blob),a=document.createElement('a');a.href=href;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(href),1000);}
$('export-card').onclick=async()=>{
  try {const c=active(),{illustration,art_blob,...record}=c;const blob=art_blob||await fetch(illustration).then(r=>{if(!r.ok)throw Error('Artwork could not be downloaded.');return r.blob();});
    const art=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(blob);});
    download(new Blob([JSON.stringify({format:'tetrarchs-forge-card-v1',...record,art_data_url:art},null,2)],{type:'application/json'}),`${c.name.replace(/[^a-z0-9]+/gi,'-').toLowerCase()}.json`);
  }catch(error){message(error.message||'The card could not be exported.',true);}
};
function soundControl(){$('sound-toggle').textContent=sound.enabled?'Sound on':'Sound off';$('sound-toggle').setAttribute('aria-pressed',String(sound.enabled));}
$('sound-toggle').onclick=()=>{sound.set(!sound.enabled);soundControl();if(sound.enabled)sound.play('edit');};soundControl();
if(isMobileDevice){document.body.classList.add('mobile-mode');$('mobile-message').hidden=false;}
try {
  try {generated=await savedCards();for(const c of generated)c.illustration=c.art_blob?URL.createObjectURL(c.art_blob):c.demo_image?new URL(`./assets/${c.demo_image}`,import.meta.url).href:null;if(generated.length){cards=generated;index=cards.length-1;}}catch{message('Local history is unavailable. You can still create and export cards.');}
  preview=await mountRenderer($('card-preview'),active(),openEditor);$('finish').replaceChildren(...preview.finishes.map(({id,label})=>{const option=document.createElement('option');option.value=id;option.textContent=label;return option;}));$('finish').value='vizier_etched_v1';draw();
}catch(error){message(`The card preview could not load: ${error.message}`,true);}
window.addEventListener('pagehide',e=>{editor?.inline?.commit();if(!e.persisted)models.dispose();});
