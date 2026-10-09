// Stage only a verified standalone card-preview build, never the editor or engine.
import {readFile,copyFile,mkdir} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {resolve,basename} from 'node:path';
const source=process.argv[2];
if(!source)throw Error('Usage: node prepare-forge.mjs /path/to/vizier-frontend/dist/card-preview');
const manifest=JSON.parse(await readFile(resolve(source,'manifest.json'),'utf8'));
if(manifest.capability!=='card-design-preview-v1')throw Error('Not a standalone card-preview build');
await mkdir('dist/forge/renderer',{recursive:true});
for(const [name,entry]of Object.entries(manifest.files)){
  if(basename(name)!==name)throw Error('Unexpected nested renderer asset');
  const bytes=await readFile(resolve(source,name));
  if(bytes.length!==entry.bytes||createHash('sha256').update(bytes).digest('hex')!==entry.sha256)throw Error(`Renderer integrity mismatch: ${name}`);
  await copyFile(resolve(source,name),resolve('dist/forge/renderer',name));
}
await copyFile(resolve(source,'manifest.json'),'dist/forge/renderer/manifest.json');
console.log('Staged standalone renderer:',manifest.files);
