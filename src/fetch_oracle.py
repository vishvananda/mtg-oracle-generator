#!/usr/bin/env python3
"""Snapshot Scryfall's complete Oracle bulk file; no per-card API loop."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
from paths import ARTIFACTS


def main():
    root=ARTIFACTS/'full-oracle'
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'bulk-metadata.json').exists():
        request=urllib.request.Request('https://api.scryfall.com/bulk-data/oracle-cards',
            headers={'User-Agent':'MTGOracleGenerator/0.1','Accept':'application/json'})
        with urllib.request.urlopen(request,timeout=60) as stream: metadata=json.load(stream)
        (root/'bulk-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
    meta=json.loads((root/'bulk-metadata.json').read_text())
    url=meta.get('jsonl_download_uri') or meta['download_uri']
    target=root/Path(url).name
    if not target.exists():
        request=urllib.request.Request(url,headers={'User-Agent':'VizierCardIntentResearch/0.2','Accept':'application/json'})
        with urllib.request.urlopen(request,timeout=60) as src, target.with_suffix('.download').open('wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
        target.with_suffix('.download').replace(target)
    with target.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    if url.endswith('.jsonl.gz'):
        with gzip.open(target,'rt') as src: cards=[json.loads(line) for line in src if line.strip()]
    else:
        cards=json.loads(target.read_text())
    ids=[c.get('oracle_id') for c in cards]
    if not all(ids) or len(set(ids))!=len(ids): raise ValueError('Expected one card per Oracle ID')
    from collections import Counter
    # Normalize either upstream format into the JSONL gzip used by projection.
    download_digest=digest
    if not url.endswith('.jsonl.gz'):
        normalized=root/'oracle-cards.jsonl.gz'
        with normalized.open('wb') as file:
            with gzip.GzipFile(filename='',mode='wb',fileobj=file,mtime=0) as zipped:
                for card in cards: zipped.write((json.dumps(card,ensure_ascii=False)+'\n').encode())
        target=normalized
        with target.open('rb') as stream: digest=hashlib.file_digest(stream,'sha256').hexdigest()
    report={'source':'Scryfall Oracle Cards bulk data','url':url,'updated_at':meta['updated_at'],
            'upstream_download_sha256':download_digest,
            'download_sha256':digest,'download_bytes':target.stat().st_size,'oracle_cards':len(cards),
            'layouts':dict(Counter(c['layout'] for c in cards)),
            'source_file':str(target),'selection':'Complete Oracle bulk snapshot, independent of engine support.'}
    (root/'source-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
