import {createServer} from 'node:http';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,extname} from 'node:path';
import {chromium} from 'playwright';
const root=resolve(import.meta.dirname,'..'),out=resolve(process.env.LOGO_FRAMES||'/tmp/cardforge-jewel-frames');
const frames=Number(process.env.LOGO_FRAME_COUNT||360),size=Number(process.env.LOGO_SIZE||384);
const server=createServer(async(req,res)=>{
  const path=resolve(root,'.'+new URL(req.url,'http://localhost').pathname);
  if(!path.startsWith(root+'/')){res.writeHead(403).end();return;}
  try{const bytes=await readFile(path);res.setHeader('Content-Type',extname(path)==='.js'?'text/javascript':'text/html');res.end(bytes);}catch{res.writeHead(404).end();}
});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const browser=await chromium.launch({executablePath:process.env.CHROME_BIN,args:['--no-sandbox','--enable-unsafe-swiftshader']});
try{
  const page=await browser.newPage({viewport:{width:size,height:size},deviceScaleFactor:1});
  page.on('pageerror',e=>{console.error(e);process.exitCode=1;});page.on('console',m=>{if(m.type()==='error')console.error(m.text());});
  await page.goto(`http://127.0.0.1:${server.address().port}/logo-studio/index.html`);
  await page.waitForFunction(()=>window.logoStudio,{},{timeout:60000});
  await mkdir(out,{recursive:true});await writeFile(`${out}/designs.json`,JSON.stringify(await page.evaluate(()=>window.logoStudio.designs),null,2));
  for(const kind of ['dual','cube']){
    await mkdir(`${out}/${kind}`,{recursive:true});const began=Date.now();
    for(let i=0;i<frames;i++){
      const base64=await page.evaluate(({kind,i,frames,size})=>window.logoStudio.render(kind,i/frames,size),{kind,i,frames,size});
      await writeFile(`${out}/${kind}/${String(i).padStart(4,'0')}.png`,Buffer.from(base64,'base64'));
      if(i%60===0||i===frames-1)console.log(`${kind}: ${i+1}/${frames} frames, ${((Date.now()-began)/1000).toFixed(1)}s`);
    }
  }
}finally{await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
