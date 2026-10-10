import {designOnly,parserHints} from './revision-prompt.js';
// The status is descriptive, not an interactive checkbox or a legality claim.
export function oracleStatus({card,canRepair,onRepair}){
 const root=document.getElementById('oracle-status'),label=document.getElementById('oracle-label'),detail=document.getElementById('oracle-detail'),fix=document.getElementById('oracle-fix'),info=document.getElementById('oracle-info');
 let validator,result=null,key='',serial=0,timer;
 const errorLabel=(error,rejected=true)=>{const message=String(error||'No parser match').replace(/\s+/g,' ').trim();label.textContent=`${rejected?'☒':'□'} Oracle: ${message.length>90?message.slice(0,87)+'…':message}`;label.title=message;};
 const refresh=()=>{fix.hidden=!(result?.status==='rejected'&&canRepair());fix.disabled=!canRepair();};
 info.onclick=()=>{detail.hidden=!detail.hidden;info.setAttribute('aria-expanded',String(!detail.hidden));};
 fix.onclick=()=>{if(result?.status==='rejected'&&canRepair())onRepair(result);};
 async function check(value,id){
  try{
   if(!validator){const {OracleValidator}=await import('./oracle-validator.js');validator=new OracleValidator();}
   const next=await validator.check(value);if(id!==serial)return;result=next;
   const parsed=next.parse_complete===true,rewrites=parsed&&next.applied_rewrites?.length;
   root.dataset.state=parsed?'passed':next.status==='rejected'?'rejected':'unavailable';
   if(parsed)label.textContent=rewrites?'☑ Oracle text parses after corrections':'☑ Oracle text parses';else errorLabel(next.error||'Oracle check unavailable',next.status==='rejected');
   detail.textContent=parsed?'Mtgish parsed the complete card. This checks supported syntax, not balance or every game rule.':`${next.error||'Unsupported syntax.'} This may be a parser limitation, rather than an invalid card.`;
   for(const hint of parserHints(value,next))detail.textContent+='\n'+hint;
   if(next.parse_diagnostic?.context)detail.textContent+=`\nFarthest match (a hint, not a guaranteed error location):\n${next.parse_diagnostic.context}`;
   if(rewrites)detail.textContent+='\nMtgish applied its card-specific corrections. Normalized input:\n'+next.normalized_input;
   info.hidden=false;refresh();
  }catch(error){if(id!==serial)return;errorLabel(error.message||'Oracle check unavailable',false);detail.textContent=error.message;root.dataset.state='unavailable';info.hidden=false;}
 }
 return {
  update(){refresh();const value=designOnly(card()),next=JSON.stringify(value);if(next===key)return;key=next;const id=++serial;clearTimeout(timer);result=null;fix.hidden=true;info.hidden=true;info.setAttribute('aria-expanded','false');detail.hidden=true;root.dataset.state='checking';label.removeAttribute('title');label.textContent='□ Checking Oracle text…';timer=setTimeout(()=>void check(value,id),350);},
  async check(value){if(!validator){const {OracleValidator}=await import('./oracle-validator.js');validator=new OracleValidator();}return validator.check(value);},
  dispose(){serial++;clearTimeout(timer);validator?.dispose();}
 };
}
