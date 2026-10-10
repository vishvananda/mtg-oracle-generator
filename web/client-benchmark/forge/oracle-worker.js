// Classic worker: Go's WASM shim exposes callbacks on this worker, never on UI.
importScripts(new URL('./mtgish/wasm_exec.js'+self.location.search,self.location.href).href);
let wasmMemory;const started=performance.now();
self.mtgishReady=()=>postMessage({type:'ready',load_ms:performance.now()-started,memory_bytes:wasmMemory?.buffer.byteLength});
self.onmessage=({data})=>{
 if(data.type==='check'){
  const started=performance.now();
  try{const result=JSON.parse(self.mtgishCheck(JSON.stringify(data.input)));postMessage({type:'result',id:data.id,result,elapsed_ms:performance.now()-started,memory_bytes:wasmMemory?.buffer.byteLength});}
  catch(error){postMessage({type:'result',id:data.id,result:{status:'parser_error',parse_complete:false,error:error.message},elapsed_ms:performance.now()-started});}
 }
};
(async()=>{
 try{
  const go=new Go();const response=await fetch(new URL('./mtgish/parser.wasm'+self.location.search,self.location.href));
  if(!response.ok)throw Error('Oracle checker could not download.');
  const {instance}=await WebAssembly.instantiate(await response.arrayBuffer(),go.importObject);
  wasmMemory=instance.exports.mem;await go.run(instance);
 }catch(error){postMessage({type:'error',error:error.message});}
})();
