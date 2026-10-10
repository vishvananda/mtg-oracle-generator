import {oracleInput} from './oracle-input.js';
export class OracleValidator {
 worker=null;ready=null;pending=null;cache=new Map();queue=Promise.resolve();nextId=0;
 reset(){this.worker?.terminate();this.worker=null;this.ready=null;}
 load(){
  if(this.ready)return this.ready;
  this.ready=new Promise((resolve,reject)=>{
   const worker=this.worker=new Worker(new URL('./oracle-worker.js',import.meta.url));
   const fail=error=>{clearTimeout(timeout);reject(error);this.pending?.reject(error);this.pending=null;this.reset();};
   const timeout=setTimeout(()=>fail(Error('Oracle checker took too long to load.')),20000);
   worker.onerror=e=>{e.preventDefault();fail(Error(e.message||'Oracle checker stopped.'));};
   worker.onmessage=({data})=>{
    if(data.type==='ready'){this.metrics={load_ms:data.load_ms,memory_bytes:data.memory_bytes};clearTimeout(timeout);resolve();}
    else if(data.type==='error')fail(Error(data.error));
    else if(data.type==='result'&&this.pending?.id===data.id){this.metrics.peak_memory_bytes=Math.max(this.metrics.peak_memory_bytes||this.metrics.memory_bytes,data.memory_bytes||0);const p=this.pending;this.pending=null;p.resolve({...data.result,elapsed_ms:data.elapsed_ms});}
   };
  });return this.ready;
 }
 check(card){
  let input;try{input=oracleInput(card);}catch(error){return Promise.resolve({status:'invalid_input',parse_complete:false,error:error.message});}
  const key=JSON.stringify(input);if(this.cache.has(key))return Promise.resolve(this.cache.get(key));
  const task=async()=>{
   if(this.cache.has(key))return this.cache.get(key);
   await this.load();const id=++this.nextId;
   const result=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{this.pending=null;this.reset();reject(Error('Oracle check timed out. This wording may be too complex for the parser.'));},8000);
    this.pending={id,resolve:value=>{clearTimeout(timer);resolve(value);},reject:error=>{clearTimeout(timer);reject(error);}};
    this.worker.postMessage({type:'check',id,input});
   });
   if(!['parser_error','invalid_input'].includes(result.status)){this.cache.set(key,result);if(this.cache.size>64)this.cache.delete(this.cache.keys().next().value);}
   return result;
  };
  const result=this.queue.then(task).catch(error=>({status:'parser_error',parse_complete:false,error:error.message}));this.queue=result;return result;
 }
 dispose(){this.pending?.reject(Error('Oracle checker closed.'));this.pending=null;this.reset();}
}
