// Run npm run build first: its dist includes the separately packaged renderer/parser.
import {cp,mkdir,readFile,readdir,writeFile,stat,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {parseArgs} from 'node:util';
const {values}=parseArgs({options:{dist:{type:'string',default:'dist'},out:{type:'string',default:'pages-dist'},'model-origin':{type:'string',default:'https://models.cardforge.fyi'}}});
const source=resolve(values.dist),out=resolve(values.out),origin=new URL(values['model-origin']);
if(origin.protocol!=='https:'||origin.pathname!=='/'||origin.search||origin.hash)throw Error('Use an HTTPS model origin without a path.');
if(out===source||!out.endsWith('/pages-dist'))throw Error('Output must be a separate pages-dist directory.');
await rm(out,{recursive:true,force:true});await mkdir(out,{recursive:true});
await cp(join(source,'forge'),out,{recursive:true,filter:path=>!path.endsWith('/README.md')});
const bench=join(out,'model-bench');await mkdir(bench);
for(const name of ['vendor','notices','device-support.js','bonsai-config.js','bonsai-loader.js','bonsai-worker.js','bonsai-chunks.js','model.json','system-prompt.txt'])await cp(join(source,name),join(bench,name),{recursive:true});
const manifest=JSON.parse(await readFile(join(bench,'model.json'),'utf8'));
manifest.first_shard=new URL(manifest.first_shard,origin).href;
await writeFile(join(bench,'model.json'),JSON.stringify(manifest,null,2)+'\n');
const config=join(bench,'bonsai-config.js');
const before=await readFile(config,'utf8');
const after=before.replace("model_path: './image-models/bonsai-ternary-2c24c81/'",`model_path: '${new URL('image-models/bonsai-ternary-2c24c81/',origin).href}'`);
if(after===before)throw Error('Bonsai model path not found; update the Pages builder.');
await writeFile(config,after);
await cp(new URL('./pages-headers.txt',import.meta.url),join(out,'_headers'));
// Pages _redirects does not support host matching. Normalize before the app
// starts so www visits use the same IndexedDB/model cache origin.
const html=join(out,'index.html');
await writeFile(html,(await readFile(html,'utf8')).replace('<head>',`<head>
  <link rel="canonical" href="https://cardforge.fyi/">
  <script>if(location.hostname==='www.cardforge.fyi')location.replace('https://cardforge.fyi'+location.pathname+location.search+location.hash);</script>`));
await writeFile(join(out,'404.html'),'<!doctype html><html lang="en"><meta charset="utf-8"><title>Not found · MTG CardForge</title><p>This page was not found.</p></html>\n');
await writeFile(join(out,'robots.txt'),'User-agent: *\nAllow: /\n');
const objects=[];
async function inventory(directory){for(const entry of await readdir(directory,{withFileTypes:true})){const path=join(directory,entry.name);if(entry.isDirectory())await inventory(path);else{const bytes=(await stat(path)).size;if(bytes>25*1024*1024)throw Error(`Pages file too large: ${path}`);objects.push({path:path.slice(out.length+1),bytes});}}}
await inventory(out);
if(objects.length>20000)throw Error('Too many Pages files.');
console.log(JSON.stringify({out,model_origin:origin.origin,file_count:objects.length,bytes:objects.reduce((s,f)=>s+f.bytes,0),largest:objects.sort((a,b)=>b.bytes-a.bytes).slice(0,3)},null,2));
