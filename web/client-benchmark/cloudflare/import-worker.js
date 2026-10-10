// Temporary authenticated importer for GGUF tensors too large for REST uploads.
// Only these five audited objects can be fetched; delete the Worker after use.
import files from './gguf-import.json';
export default {
 async fetch(request,env){
  if(request.method!=='POST'||!env.IMPORT_TOKEN||request.headers.get('Authorization')!==`Bearer ${env.IMPORT_TOKEN}`)return new Response('Not found',{status:404});
  try {
  const {path}=await request.json(),file=files.find(f=>f.path===path);
  if(!file)return new Response('Unknown audited object',{status:400});
  const existing=await env.MODELS.head(path);
  if(existing?.size===file.bytes&&existing.customMetadata?.sha256===file.sha256)return Response.json({path,bytes:existing.size,status:'already_uploaded',sha256:file.sha256});
  const source=await fetch(new URL(path,env.SOURCE_ROOT),{redirect:'manual'});
  if(!source.ok||Number(source.headers.get('Content-Length'))!==file.bytes)return new Response('Source length mismatch',{status:502});
  const result=await env.MODELS.put(path,source.body,{sha256:file.sha256,customMetadata:{sha256:file.sha256},httpMetadata:{contentType:'application/octet-stream',cacheControl:'public, max-age=31536000, immutable'}});
  return Response.json({path,bytes:result.size,etag:result.etag,sha256:file.sha256,status:'uploaded'});
  } catch(error) { return Response.json({error:error.message},{status:500}); }
 }
};
