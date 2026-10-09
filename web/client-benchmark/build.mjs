import { cp, mkdir, copyFile } from 'node:fs/promises';
await mkdir('dist/vendor', { recursive: true });
for (const file of ['index.html', 'style.css', 'app.js']) await copyFile(file, `dist/${file}`);
await cp('node_modules/@wllama/wllama/esm', 'dist/vendor/wllama', { recursive: true });
await cp('node_modules/@wllama/wllama-compat/wasm', 'dist/vendor/compat', { recursive: true });
await cp('notices', 'dist/notices', { recursive: true });
await copyFile('node_modules/@wllama/wllama/LICENCE', 'dist/notices/wllama-LICENSE.txt');
await copyFile('../../LICENSE', 'dist/notices/app-LICENSE.txt');
console.log('Built dist/. Add model.json, system-prompt.txt and model shards before serving.');
