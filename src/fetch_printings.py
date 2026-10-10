"""Download one checksum-verified MTGJSON AllPrintings snapshot."""
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

from paths import ARTIFACTS


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'VizierCardIntentResearch/0.2'}),timeout=60)


def main():
    base='https://mtgjson.com/api/v5/'
    with request(base+'Meta.json') as stream: meta=json.load(stream)['data']
    with request(base+'AllPrintings.json.xz.sha256') as stream: expected=stream.read().decode().strip()
    if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected): raise ValueError('Invalid upstream checksum')
    root=ARTIFACTS/'mtgjson-printings'/meta['date']; root.mkdir(parents=True,exist_ok=True)
    target=root/'AllPrintings.json.xz'
    if not target.exists():
        temporary=target.with_suffix('.download')
        with request(base+target.name) as src,temporary.open('wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
        temporary.replace(target)
    with target.open('rb') as stream: actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=expected: raise ValueError('Snapshot hash mismatch; retain file for diagnosis and retry with a new snapshot')
    manifest={'source':'MTGJSON AllPrintings','url':base+target.name,'meta':meta,
              'source_file':str(target),'sha256':actual,'bytes':target.stat().st_size,
              'checksum_url':base+target.name+'.sha256','checksum_verified':True}
    (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
