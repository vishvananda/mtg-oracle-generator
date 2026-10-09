// Snapshot set names at build time; the user-facing picker needs no API requests.
import {readFile,writeFile} from 'node:fs/promises';
const source=process.argv[2];
if(!source)throw Error('Usage: node update-forge-sets.mjs /path/to/vizier-frontend');
const owner=await readFile(`${source}/src/shared/cards/presentation/set-symbols.ts`,'utf8');
const codes=new Set(owner.match(/const KEYRUNE_CODES = new Set\(`([\s\S]+?)`\.trim/)[1].trim().split(/\s+/));codes.add('con');
const response=await fetch('https://api.scryfall.com/sets',{headers:{Accept:'application/json','User-Agent':'MTGForge/0.1 (set picker build)'}});
if(!response.ok)throw Error(`Set catalog returned ${response.status}`);
const data=await response.json();
const sets=data.data.filter(s=>codes.has(s.code)).map(s=>({code:s.code.toUpperCase(),name:s.name,released:s.released_at||''})).sort((a,b)=>b.released.localeCompare(a.released)||a.name.localeCompare(b.name));
sets.unshift({code:'FORGE',name:'MTG CardForge',released:''},{code:'VIZIER',name:'Vizier · Original designs',released:''});
await writeFile('forge/sets.json',JSON.stringify({source:'https://api.scryfall.com/sets',updated:new Date().toISOString().slice(0,10),sets},null,2)+'\n');
console.log(`Saved ${sets.length} supported sets.`);
