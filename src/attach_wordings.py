"""Attach a hash-pinned historical-wording export to its source corpus."""
import argparse
import json
from pathlib import Path
from data_utils import digest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True)
    p.add_argument('--wordings',type=Path,required=True);a=p.parse_args()
    source=a.root/'manifest.json';manifest=a.wordings/'manifest.json'
    if json.loads(manifest.read_text())['source_manifest_sha256']!=digest(source.read_bytes()):
        p.error('Wordings belong to a different source corpus')
    path=a.root/'augmentations.json'
    if path.exists():p.error('Augmentations already attached; inspect instead of overwriting')
    path.write_text(json.dumps([{'path':str(a.wordings.resolve()),'manifest_sha256':digest(manifest.read_bytes())}],indent=2)+'\n')
