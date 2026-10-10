import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createChunkedFetch, MAX_ASSET_BYTES} from './bonsai-chunks.js';
const root='https://models.example/bonsai/', path=root+'model.safetensors';
const bytes=Uint8Array.from({length:31},(_,i)=>i);
const manifest={version:1,files:[{path:'model.safetensors',bytes:31,chunks:[
  {path:'chunks/a.bin',offset:0,bytes:12},{path:'chunks/b.bin',offset:12,bytes:12},{path:'chunks/c.bin',offset:24,bytes:7},
]}]};
function fixture(change){
  const calls=[];
  const fetcher=async(url,options={})=>{
    calls.push({url:String(url),...options});
    const chunk=manifest.files[0].chunks.find(c=>root+c.path===String(url));
    if(!chunk)return new Response('config');
    const [,first,last]=/bytes=(\d+)-(\d+)/.exec(options.headers.Range).map(Number);
    const response=new Response(bytes.slice(chunk.offset+first,chunk.offset+last+1),{status:206,headers:{
      'Content-Range':`bytes ${first}-${last}/${chunk.bytes}`,'Content-Length':String(last-first+1),
    }});
    return change?change(response):response;
  };
  return {calls,fetch:createChunkedFetch(root,manifest,fetcher)};
}
test('HEAD exposes full logical length without fetching the large original',async()=>{
 const f=fixture(),r=await f.fetch(path,{method:'HEAD'});
 assert.equal(r.headers.get('content-length'),'31');assert.equal(r.headers.get('accept-ranges'),'bytes');assert.equal(f.calls.length,0);
});
test('arbitrary ranges and every boundary reconstruct the original bytes',async()=>{
 const f=fixture();
 for(let start=0;start<31;start++)for(let end=start;end<31;end++){
  const r=await f.fetch(path,{headers:{Range:`bytes=${start}-${end}`}});
  assert.equal(r.status,206);assert.equal(r.headers.get('content-range'),`bytes ${start}-${end}/31`);
  assert.deepEqual(new Uint8Array(await r.arrayBuffer()),bytes.slice(start,end+1));
 }
 assert.ok(f.calls.every(c=>c.url.includes('/chunks/')&&c.credentials==='omit'));
});
test('Request inputs work and non-weight resources pass through',async()=>{
 const f=fixture(),r=await f.fetch(new Request(path,{headers:{Range:'bytes=10-14'}}));
 assert.deepEqual(new Uint8Array(await r.arrayBuffer()),bytes.slice(10,15));
 assert.equal(await(await f.fetch(root+'config.json')).text(),'config');
});
test('invalid or unbounded requests never download oversized originals',async()=>{
 const f=fixture();await assert.rejects(f.fetch(path),/explicit byte range/);
 assert.equal((await f.fetch(path,{headers:{Range:'bytes=30-31'}})).status,416);assert.equal(f.calls.length,0);
});
test('servers ignoring Range or returning incorrect ranges fail closed',async()=>{
 for(const change of [()=>new Response('full file'),r=>{r.headers.set('Content-Range','bytes 0-1/31');return r;},r=>{r.headers.set('Content-Length','99');return r;}]){
  const f=fixture(change),r=await f.fetch(path,{headers:{Range:'bytes=10-20'}});
  await assert.rejects(r.arrayBuffer(),/invalid byte range/);
 }
});
test('truncated range bodies fail instead of corrupting weights',async()=>{
 const f=fixture(r=>new Response(new Uint8Array(1),{status:206,headers:r.headers}));
 const r=await f.fetch(path,{headers:{Range:'bytes=0-10'}});await assert.rejects(r.arrayBuffer(),/truncated/);
});
test('aborted model loads propagate cancellation',async()=>{
 const f=fixture(),abort=new AbortController();abort.abort();
 await assert.rejects(f.fetch(path,{headers:{Range:'bytes=0-5'},signal:abort.signal}),{name:'AbortError'});assert.equal(f.calls.length,0);
});
test('manifest rejects oversized, missing, overlapping and foreign chunks',()=>{
 for(const alter of [m=>m.files[0].chunks[0].bytes=MAX_ASSET_BYTES,m=>m.files[0].chunks.pop(),m=>m.files[0].chunks[1].offset=0,m=>m.files[0].chunks[0].path='https://elsewhere.example/a']){
  const m=structuredClone(manifest);alter(m);assert.throws(()=>createChunkedFetch(root,m));
 }
});
