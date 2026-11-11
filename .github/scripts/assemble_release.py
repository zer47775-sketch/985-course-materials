"""Reassemble this task's approved GitHub release using only Python stdlib.

This program never extracts an archive or executes course-material contents.
It refuses to clobber a concurrent/incomplete upload under a final ZIP name.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = 'zer47775-sketch/985-course-materials'
REPOSITORY_ID = 1454032300
RELEASE_ID = 441750661
TAG = 'v2026.11.11'
EXPECTED = {
    '01-calculus-985.zip': (1688996057, '4c1ae5f42214810c24a5efd6b465930d4f5dc8270d5c2e0a54bfc1e96d780dd7'),
    '02-linear-algebra-985.zip': (800797070, '6b3fc2031995c58439c3dc2d4e0bb9962d5fcfa0533c301762edcff83bd41e58'),
    '03-university-physics-985.zip': (1050989168, '1e34d584f3c1f94d5bb4114d15ad079179bb0e9e8a68adc4ba9e1e17a1963fb7'),
}
BASE = '/repos/' + REPOSITORY
HEX = re.compile(r'^[0-9a-f]{64}$')


class Stop(RuntimeError):
    pass


def integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def chunk_name(set_id, name, index, count):
    return f'tmp985-{set_id}-{name}.part{index + 1:04d}-of-{count:04d}'


def validate_manifest(m):
    if m.get('schema_version') != 1:
        raise Stop('Unsupported chunk manifest version')
    if m.get('repository') != {'full_name': REPOSITORY, 'id': REPOSITORY_ID}:
        raise Stop('Chunk manifest repository proof differs')
    if m.get('release') != {'id': RELEASE_ID, 'tag_name': TAG}:
        raise Stop('Chunk manifest release proof differs')
    set_id = m.get('upload_set_id', '')
    if not isinstance(set_id, str) or not re.fullmatch('[a-z0-9]{8,24}', set_id):
        raise Stop('Invalid upload-set identifier')
    block = m.get('chunk_size_bytes')
    if not integer(block) or block not in (16 * 1024**2, 32 * 1024**2):
        raise Stop('Only 16 or 32 MiB chunks are accepted')
    assets = m.get('assets')
    if not isinstance(assets, list) or len(assets) != len(EXPECTED):
        raise Stop('Manifest must describe exactly the three approved ZIPs')
    names, part_ids, part_names = set(), set(), set()
    for a in assets:
        name = a.get('name')
        if name not in EXPECTED or name in names:
            raise Stop('Unknown or repeated final ZIP name')
        names.add(name)
        size, sha = EXPECTED[name]
        if a.get('size_bytes') != size or a.get('sha256') != sha:
            raise Stop('Final ZIP size or SHA256 differs from the approved package')
        parts = a.get('parts')
        if not isinstance(parts, list):
            raise Stop('Chunk parts must be a list')
        # Empty parts are only usable when this exact final ZIP already exists
        # remotely and passes verification before any write operation.
        if not parts:
            continue
        count = math.ceil(size / block)
        if len(parts) != count:
            raise Stop('Unexpected number of chunks')
        for index, p in enumerate(parts):
            remote_id = p.get('asset_id')
            if not integer(remote_id) or remote_id <= 0 or remote_id in part_ids:
                raise Stop('Every uploaded chunk needs a unique positive asset_id')
            part_ids.add(remote_id)
            expected_name = chunk_name(set_id, name, index, count)
            if p.get('index') != index or p.get('asset_name') != expected_name:
                raise Stop('Chunk order or bound name differs')
            if expected_name in part_names:
                raise Stop('Repeated chunk asset name')
            part_names.add(expected_name)
            if p.get('size_bytes') != min(block, size - index * block):
                raise Stop('Chunk size differs from its position')
            if not isinstance(p.get('sha256'), str) or not HEX.fullmatch(p['sha256']):
                raise Stop('Invalid chunk SHA256')
    if names != set(EXPECTED):
        raise Stop('An approved final ZIP is missing')
    return m


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class GitHub:
    def __init__(self, token):
        if not token:
            raise Stop('GITHUB_TOKEN is required')
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def headers(self, accept='application/vnd.github+json'):
        return {'Authorization': 'Bearer ' + self.token, 'Accept': accept,
                'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': '985-release-reassembly'}

    def request(self, method, path, body=None, missing=False):
        if not path.startswith(BASE):
            raise Stop('API operation outside the approved repository')
        raw = None if body is None else json.dumps(body).encode('utf-8')
        headers = self.headers()
        if raw is not None:
            headers['Content-Type'] = 'application/json'
        attempts = 4 if method in ('GET', 'DELETE') else 1
        for attempt in range(attempts):
            request = urllib.request.Request('https://api.github.com' + path, data=raw,
                                             method=method, headers=headers)
            try:
                with self.opener.open(request, timeout=90) as response:
                    data = response.read()
                    return json.loads(data) if data else None
            except urllib.error.HTTPError as error:
                if error.code == 404 and (missing or method == 'DELETE'):
                    return None
                if error.code in (429, 500, 502, 503, 504) and attempt + 1 < attempts:
                    time.sleep(min(30, 2 ** (attempt + 1)))
                    continue
                raise Stop(f'GitHub {method} failed with HTTP {error.code}') from None
            except (OSError, http.client.HTTPException):
                if attempt + 1 < attempts:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise Stop(f'GitHub {method} transport failed; assets were preserved') from None
        raise Stop('Unreachable GitHub request state')

    def bind(self):
        repo = self.request('GET', BASE)
        if repo.get('id') != REPOSITORY_ID or repo.get('full_name') != REPOSITORY:
            raise Stop('Live GitHub repository proof differs')
        release = self.request('GET', BASE + f'/releases/{RELEASE_ID}')
        if release.get('id') != RELEASE_ID or release.get('tag_name') != TAG:
            raise Stop('Live GitHub release proof differs')
        return release

    def assets(self):
        results = []
        for page in range(1, 101):
            batch = self.request('GET', BASE + f'/releases/{RELEASE_ID}/assets?per_page=100&page={page}')
            if not isinstance(batch, list):
                raise Stop('Invalid release-asset list')
            results.extend(batch)
            if len(batch) < 100:
                return results
        raise Stop('Release contains an unexpectedly large number of assets')

    def open_bytes(self, asset_id):
        # The credential goes only to api.github.com. Signed CDN redirects are
        # followed separately, using an entirely new request with no credential.
        path = BASE + f'/releases/assets/{asset_id}'
        req = urllib.request.Request('https://api.github.com' + path,
                                     headers=self.headers('application/octet-stream'))
        try:
            return self.opener.open(req, timeout=90)
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                raise Stop(f'Asset download API failed with HTTP {error.code}') from None
            location = error.headers.get('Location', '')
        target = urllib.parse.urlsplit(location)
        host = (target.hostname or '').lower()
        if target.scheme != 'https' or not (host == 'githubusercontent.com' or host.endswith('.githubusercontent.com')):
            raise Stop('Asset download redirect was outside the approved GitHub CDN')
        return self.opener.open(urllib.request.Request(location, headers={'User-Agent': '985-release-reassembly'}), timeout=90)

    def upload(self, name, file_path, size):
        # This route and every destination field are fixed by the approved IDs.
        route = BASE + f'/releases/{RELEASE_ID}/assets?' + urllib.parse.urlencode({'name': name})
        conn = http.client.HTTPSConnection('uploads.github.com', timeout=180)
        try:
            conn.putrequest('POST', route)
            for key, value in self.headers().items():
                conn.putheader(key, value)
            conn.putheader('Content-Type', 'application/zip')
            conn.putheader('Content-Length', str(size))
            conn.endheaders()
            with file_path.open('rb') as source:
                while True:
                    block = source.read(1024**2)
                    if not block:
                        break
                    conn.send(block)
            response = conn.getresponse()
            raw = response.read(1024**2)
            if response.status not in (200, 201):
                raise Stop(f'Final ZIP upload failed with HTTP {response.status}; no asset was clobbered')
            return json.loads(raw)
        except (OSError, http.client.HTTPException):
            raise Stop('Final ZIP upload was interrupted. Preserve chunks and inspect any starter asset before rerunning.') from None
        finally:
            conn.close()


def local_valid(path, size, sha):
    if not path.is_file() or path.stat().st_size != size:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024**2), b''):
            digest.update(block)
    return digest.hexdigest() == sha


def assert_metadata(remote, name, size, sha=None):
    if remote.get('name') != name or remote.get('size') != size or remote.get('state') != 'uploaded':
        raise Stop('Conflicting or incomplete asset: ' + name + '. Preserve it; do not run alongside another uploader.')
    if sha is not None and remote.get('digest') not in (None, '', 'sha256:' + sha):
        raise Stop('Remote SHA256 differs: ' + name)


def download_verified(api, remote, path, size, sha):
    if local_valid(path, size, sha):
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + '.partial')
    last = None
    for attempt in range(4):
        digest, received = hashlib.sha256(), 0
        try:
            with api.open_bytes(remote['id']) as response, partial.open('wb') as sink:
                while True:
                    block = response.read(1024**2)
                    if not block:
                        break
                    received += len(block)
                    if received > size:
                        raise Stop('Asset exceeded its manifest size')
                    digest.update(block)
                    sink.write(block)
            if received != size or digest.hexdigest() != sha:
                raise Stop('Downloaded bytes failed SHA256 or size validation: ' + remote['name'])
            partial.replace(path)
            return path
        except (Stop, OSError, http.client.HTTPException):
            last = 'Could not verify downloaded asset: ' + remote['name']
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise Stop(last)


def verify_original(api, remote, name, work):
    size, sha = EXPECTED[name]
    assert_metadata(remote, name, size, sha)
    if remote.get('digest') != 'sha256:' + sha:
        # Older API deployments may omit digest. In that case check actual bytes,
        # rather than silently replacing a checksum check with a size check.
        download_verified(api, remote, work / 'verified-remote' / name, size, sha)
    return remote['id']


def assemble_one(api, a, by_id, work):
    name, size, sha = a['name'], a['size_bytes'], a['sha256']
    destination = work / 'assembled' / name
    if local_valid(destination, size, sha):
        return destination
    if not a['parts']:
        raise Stop('No chunks supplied for a final ZIP that is not verified remotely: ' + name)

    def fetch(part):
        remote = by_id.get(part['asset_id'])
        if remote is None:
            raise Stop('A required chunk asset is missing: ' + part['asset_name'])
        assert_metadata(remote, part['asset_name'], part['size_bytes'], part['sha256'])
        path = work / 'parts' / part['asset_name']
        return download_verified(api, remote, path, part['size_bytes'], part['sha256'])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(fetch, a['parts']))
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.partial')
    digest, count = hashlib.sha256(), 0
    with partial.open('wb') as sink:
        for path in paths:
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024**2), b''):
                    digest.update(block)
                    count += len(block)
                    sink.write(block)
    if count != size or digest.hexdigest() != sha:
        raise Stop('Reassembled original ZIP failed SHA256: ' + name)
    partial.replace(destination)
    return destination


def run(api, manifest, work):
    validate_manifest(manifest)
    work.mkdir(parents=True, exist_ok=True)
    release = api.bind()
    remote_assets = api.assets()
    by_id = {a['id']: a for a in remote_assets}
    by_name = {a['name']: a for a in remote_assets}
    if len(by_name) != len(remote_assets):
        raise Stop('Duplicate remote asset names are ambiguous')
    ready, already_verified = {}, {}
    # Refuse conflicting existing final names before writing anything. This is
    # what protects the still-running local large-file upload from this plan.
    for a in manifest['assets']:
        existing = by_name.get(a['name'])
        if existing is not None:
            already_verified[a['name']] = verify_original(api, existing, a['name'], work)
    if not release.get('draft') and len(already_verified) != len(EXPECTED):
        raise Stop('Release is already public but its approved final ZIP set is incomplete; no writes were made')

    # Validate all sources first; course contents are concatenated as opaque bytes.
    for a in manifest['assets']:
        if a['name'] not in already_verified:
            ready[a['name']] = assemble_one(api, a, by_id, work)
    for name, path in ready.items():
        current = {a['name']: a for a in api.assets()}
        if name in current:
            already_verified[name] = verify_original(api, current[name], name, work)
            continue
        print('Uploading reconstructed ZIP: ' + name, flush=True)
        uploaded = api.upload(name, path, EXPECTED[name][0])
        already_verified[name] = verify_original(api, uploaded, name, work)

    # Full remote proof of all three originals precedes every destructive stage.
    current = {a['name']: a for a in api.assets()}
    for name in EXPECTED:
        if name not in current:
            raise Stop('A final ZIP is missing; chunks and draft were preserved')
        already_verified[name] = verify_original(api, current[name], name, work)
    deleted = 0
    for a in manifest['assets']:
        for part in a['parts']:
            path = BASE + f"/releases/assets/{part['asset_id']}"
            remote = api.request('GET', path, missing=True)
            if remote is None:
                continue  # Safe recovery after originals exist and cleanup was partial.
            assert_metadata(remote, part['asset_name'], part['size_bytes'], part['sha256'])
            api.request('DELETE', path)
            deleted += 1
    release = api.bind()
    current = {a['name']: a for a in api.assets()}
    for name in EXPECTED:
        if name not in current:
            raise Stop('A final ZIP disappeared before publication')
        verify_original(api, current[name], name, work)
    if release.get('draft'):
        api.request('PATCH', BASE + f'/releases/{RELEASE_ID}', {'draft': False, 'make_latest': 'true'})
    published = api.bind()
    if published.get('draft'):
        raise Stop('Release remains a draft')
    result = {'repository_id': REPOSITORY_ID, 'release_id': RELEASE_ID,
              'published': True, 'release_url': published['html_url'],
              'verified_originals': list(EXPECTED), 'temporary_chunks_deleted': deleted}
    (work / 'assembly-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--work-dir', type=Path, default=Path(os.environ.get('RUNNER_TEMP', '.')) / '985-release-assembly')
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(args.manifest.read_text('utf-8'))
    api = GitHub(os.environ.get('GITHUB_TOKEN', ''))
    print(json.dumps(run(api, manifest, args.work_dir), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
