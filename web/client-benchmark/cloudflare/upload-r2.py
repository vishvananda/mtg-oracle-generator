"""Upload only audited model assets; never recursively publish a model directory.
Requires requests. Reads a bearer token from the environment or --token-file.
Existing objects are skipped only when size and single-part ETag match local MD5.
"""
import argparse,concurrent.futures,hashlib,json,mimetypes,os,time
from pathlib import Path
from urllib.parse import quote
import requests

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('root',type=Path);p.add_argument('--audit',type=Path,required=True)
p.add_argument('--account',required=True);p.add_argument('--bucket',default='cardforge-models')
p.add_argument('--public-origin',default='https://models.cardforge.fyi')
p.add_argument('--token-file',type=Path);p.add_argument('--workers',type=int,default=3)
p.add_argument('--only-prefix',default='');p.add_argument('--receipt',type=Path,required=True)
a=p.parse_args()
audit=json.loads(a.audit.read_text());assert audit['hashes_verified'], 'Run check-model-assets.py --hashes first'
assets=[x for x in audit['assets'] if x['path'].startswith(a.only_prefix)]
assert assets and all(x['bytes']<300_000_000 for x in assets), 'Split all objects below the REST upload limit'

def upload(item):
 key=item['path'];path=(a.root/key).resolve();assert path.is_file() and path.stat().st_size==item['bytes']
 md5=hashlib.md5();sha=hashlib.sha256()
 with path.open('rb') as f:
  while block:=f.read(8*1024*1024):md5.update(block);sha.update(block)
 if item.get('sha256'):assert sha.hexdigest()==item['sha256'],f'Changed audited file: {key}'
 public=a.public_origin.rstrip('/')+'/'+quote(key,safe='/')
 def exists():
  r=requests.head(public+"?verify="+str(time.time_ns()),headers={"Accept-Encoding":"identity"},timeout=30)
  return r.status_code==200 and int(r.headers.get('Content-Length',0))==item['bytes'] and r.headers.get('ETag','').strip('"')==md5.hexdigest()
 began=time.monotonic()
 if exists():return {**item,'sha256':sha.hexdigest(),'status':'already_uploaded'}
 immutable=key.startswith(('models/','image-models/'))
 content_type=mimetypes.guess_type(key)[0] or 'application/octet-stream'
 if key.endswith(('.bin','.gguf','.safetensors','.model')):content_type='application/octet-stream'
 for attempt in range(4):
  token=os.environ.get('CLOUDFLARE_API_TOKEN') or (a.token_file.read_text().strip() if a.token_file else '')
  if not token:raise RuntimeError('Set CLOUDFLARE_API_TOKEN or --token-file')
  headers={'Authorization':'Bearer '+token,'Content-Type':content_type,'Content-Length':str(item['bytes']),'Cache-Control':'public, max-age=31536000, immutable' if immutable else 'public, max-age=0, must-revalidate'}
  try:
   with path.open('rb') as f:
    r=requests.put(f'https://api.cloudflare.com/client/v4/accounts/{a.account}/r2/buckets/{a.bucket}/objects/{quote(key,safe="/")}',data=f,headers=headers,timeout=(30,600))
   if not r.ok:raise RuntimeError(f'Upload {key}: HTTP {r.status_code}: {r.text[:500]}')
   if not exists():raise RuntimeError(f'Public object size/ETag mismatch: {key}')
   return {**item,'sha256':sha.hexdigest(),'status':'uploaded','seconds':round(time.monotonic()-began,2)}
  except (requests.RequestException,RuntimeError) as error:
   print(f"Retry {key}: {error}",flush=True)
   if attempt==3:raise
   time.sleep(2**attempt)

results=[]
try:
 with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
  futures={pool.submit(upload,x):x for x in assets}
  for future in concurrent.futures.as_completed(futures):
   result=future.result();results.append(result)
   print(f'{len(results)}/{len(assets)} {result["status"]}: {result["path"]} ({result["bytes"]:,} bytes)',flush=True)
finally:
 a.receipt.write_text(json.dumps({'origin':a.public_origin,'bucket':a.bucket,'expected_count':len(assets),'completed_count':len(results),'assets':results},indent=2)+'\n')
