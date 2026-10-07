"""Explicit Hub upload/download; only manifest-listed dataset files are transferred."""
import argparse
import json
from pathlib import Path
from data_utils import digest


def verify(root):
    manifest=json.loads((root/'manifest.json').read_text())
    for name,pin in manifest['files'].items():
        if Path(name).name!=name or (root/name).is_symlink(): raise ValueError('Unsafe file name')
        if digest((root/name).read_bytes())!=pin['sha256']: raise ValueError('Hash mismatch: '+name)
    if any(manifest['checks'][key] for key in ('group_overlap','card_overlap','description_overlap')):
        raise ValueError('Dataset reports split leakage')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['download','upload','verify'])
    p.add_argument('--repo-id')
    p.add_argument('--revision',help='Pin downloads to the release commit SHA')
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--private',action='store_true',help='Create a private repo instead of public')
    a=p.parse_args()
    if a.action!='verify' and not a.repo_id: p.error('--repo-id is required')
    if a.action=='download':
        if not a.revision: p.error('--revision is required for reproducible downloads')
        from huggingface_hub import snapshot_download
        snapshot_download(a.repo_id,repo_type='dataset',revision=a.revision,local_dir=a.directory,
                          allow_patterns=['manifest.json','README.md','DATA_LICENSE.md','*.jsonl','*.json','system-prompt.txt'])
        verify(a.directory)
    elif a.action=='upload':
        from huggingface_hub import HfApi
        manifest=verify(a.directory)
        api=HfApi()
        api.create_repo(a.repo_id,repo_type='dataset',private=a.private,exist_ok=True)
        receipt=api.upload_folder(repo_id=a.repo_id,repo_type='dataset',folder_path=a.directory,
            allow_patterns=['manifest.json','README.md',*manifest['files']],
            commit_message='Publish '+manifest['release'])
        print(json.dumps({'repo_id':a.repo_id,'commit':receipt.oid,'url':receipt.commit_url},indent=2))
    else:
        result=verify(a.directory)
        print(json.dumps({'verified':True,'counts':result['counts']},indent=2))
