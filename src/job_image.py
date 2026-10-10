"""Verify the pinned public GHCR image before submitting GPU compute."""
import hashlib
import json
import re
import urllib.request

def verify_job_image(image):
    """Resolve the exact public GHCR manifest before starting billed compute."""
    match = re.fullmatch(r'ghcr\.io/([a-z0-9_./-]+)@(sha256:[a-f0-9]{64})', image)
    if not match:
        raise ValueError('Job image must be a GHCR image pinned by SHA-256 digest')
    repository, digest_value = match.groups()
    with urllib.request.urlopen('https://ghcr.io/token?service=ghcr.io&scope=repository:'+repository+':pull', timeout=30) as response:
        access = json.load(response)['token']
    headers = {'Authorization':'Bearer '+access,
               'Accept':'application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json'}

    def fetch(path, expected):
        request = urllib.request.Request('https://ghcr.io/v2/'+repository+'/'+path, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        if 'sha256:'+hashlib.sha256(raw).hexdigest()!=expected:
            raise ValueError('Registry image content does not match its pinned digest')
        return json.loads(raw)

    index = fetch('manifests/'+digest_value, digest_value)
    platform_digest = digest_value
    if 'manifests' in index:
        platform = next((entry for entry in index['manifests'] if
                         entry.get('platform',{}).get('os')=='linux' and
                         entry['platform'].get('architecture')=='amd64'), None)
        if platform is None:
            raise ValueError('Job image has no Linux AMD64 platform')
        platform_digest = platform['digest']
        index = fetch('manifests/'+platform_digest, platform_digest)
    config_digest = index['config']['digest']
    config = fetch('blobs/'+config_digest, config_digest)
    if config.get('os')!='linux' or config.get('architecture')!='amd64':
        raise ValueError('Job image platform differs from the GPU host')
    return {'image':image, 'platform_digest':platform_digest, 'platform':'linux/amd64'}

