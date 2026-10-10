import { cp, mkdir, copyFile, readdir, readFile, writeFile } from 'node:fs/promises';
import {createHash} from 'node:crypto';
await mkdir('dist/vendor', { recursive: true });
for (const file of ['index.html', 'style.css', 'app.js', 'device-support.js', 'bonsai.js', 'bonsai-config.js', 'bonsai-loader.js', 'bonsai-worker.js', 'bonsai-chunks.js']) await copyFile(file, `dist/${file}`);
await cp('forge', 'dist/forge', { recursive: true });
await cp('node_modules/@wllama/wllama/esm', 'dist/vendor/wllama', { recursive: true });
await cp('node_modules/@wllama/wllama-compat/wasm', 'dist/vendor/compat', { recursive: true });
await cp('notices', 'dist/notices', { recursive: true });
await copyFile('node_modules/@wllama/wllama/LICENCE', 'dist/notices/wllama-LICENSE.txt');
await copyFile('../../LICENSE', 'dist/notices/app-LICENSE.txt');
console.log('Built dist/. Add model.json, system-prompt.txt and model shards before serving.');

// Version the small app dependency graph, including the renderer import. Existing
// model/art URLs stay stable; a browser cannot retain yesterday's renderer CSS.
const appFiles=(await readdir('dist/forge')).filter(f=>/\.(js|css|html)$/.test(f)).sort();
const texts=await Promise.all(appFiles.map(f=>readFile(`dist/forge/${f}`,'utf8')));
const rendererManifest=await readFile('dist/forge/renderer/manifest.json','utf8').catch(()=> '');
const parserManifest=await readFile('dist/forge/mtgish/manifest.json','utf8').catch(()=> '');
const version=createHash('sha256').update(texts.join('\n')+rendererManifest+parserManifest).digest('hex').slice(0,12);
for(let i=0;i<appFiles.length;i++){
  const text=texts[i].replace(/(["'])(\.\/[^"'\s?]+\.(?:js|css|json))\1/g,(_,quote,path)=>`${quote}${path}?v=${version}${quote}`);
  await writeFile(`dist/forge/${appFiles[i]}`,text);
}
await writeFile('dist/forge/build.json',JSON.stringify({version,built_at:new Date().toISOString()})+'\n');
console.log(`Forge app version ${version}; model URLs unchanged.`);
