"""Promote verified container digests without rebuilding or downgrading aliases."""
import json
import os
from pathlib import Path
import re
import subprocess

from project_info import GITHUB_PROJECT
from .artifacts import PLATFORMS, validate_digests, verify_release
from .github import GitHub
from .version import parse_version, read_version, validate_tag

IMAGE = 'ghcr.io/akaunik/amnezia-panel'


def summary(message):
    print(message, flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as out:
            out.write(message + '\n')


class Registry:
    def inspect(self, reference):
        result = subprocess.run(['docker','buildx','imagetools','inspect',reference,
                                 '--format','{{json .Manifest}}'], capture_output=True,text=True,timeout=180)
        if result.returncode:
            if any(s in result.stderr.lower() for s in ('manifest unknown','not found','404 not found')):
                return None
            raise RuntimeError('Registry inspection failed: ' + result.stderr.strip())
        manifest = json.loads(result.stdout)
        digest = manifest.get('digest','')
        if not re.fullmatch(r'sha256:[0-9a-f]{64}',digest):
            raise ValueError('Registry did not return a valid digest')
        children = [m for m in manifest.get('manifests',[])
                    if m.get('platform',{}).get('os') == 'linux']
        configs = []
        for child in children or [{'digest':digest}]:
            config = subprocess.run(['docker','buildx','imagetools','inspect',
                                     IMAGE+'@'+child['digest'],'--format','{{json .Image}}'],
                                    capture_output=True,text=True,check=True,timeout=180)
            configs.append(json.loads(config.stdout))
        labels = [c.get('config',{}).get('Labels',{}) for c in configs]
        if any(l.get('org.opencontainers.image.source') != 'https://github.com/'+GITHUB_PROJECT for l in labels):
            raise ValueError('Container source label mismatch')
        versions = {l.get('org.opencontainers.image.version') for l in labels}
        commits = {l.get('org.opencontainers.image.revision') for l in labels}
        if len(versions)!=1 or len(commits)!=1:
            raise ValueError('Mixed container platform identities')
        version, sha = versions.pop(), commits.pop()
        parse_version(version)
        if not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{40}',sha):
            raise ValueError('Invalid container source SHA')
        return {'digest':digest,'version':version,'sha':sha,
                'platforms':sorted(c['os']+'/'+c['architecture'] for c in configs)}

    def promote(self, tag, digest):
        subprocess.run(['docker','buildx','imagetools','create','--prefer-index=false',
                        '--tag',IMAGE+':'+tag,IMAGE+'@'+digest],check=True,timeout=300)
        current = self.inspect(IMAGE+':'+tag)
        if not current or current['digest'] != digest:
            raise ValueError('Promoted digest verification failed: '+tag)


def promote(digests, version, sha, ref, main_sha, registry, emit=summary):
    # Preflight BOTH variants and ALL target names before the first mutation.
    for variant, data in digests.items():
        current=registry.inspect(IMAGE+'@'+data['digest'])
        expected={'digest':data['digest'],'sha':sha,'version':version,
                  'platforms':sorted(PLATFORMS[variant])}
        if current!=expected:
            raise ValueError('Staging image identity/platform mismatch: '+variant)
    if set(digests)!=set(PLATFORMS):
        raise ValueError('Both container variants are required')
    v=parse_version(version)
    if ref=='refs/heads/main':
        exact='sha-'+sha
        aliases=['main','latest'] if sha==main_sha else []
        if sha!=main_sha:
            emit('Stale main SHA: skipping main/latest aliases')
    elif ref.startswith('refs/tags/'):
        validate_tag(version,ref.removeprefix('refs/tags/'))
        exact=version
        aliases=[] if v.prerelease else [str(v.major),f'{v.major}.{v.minor}','latest']
    else:
        raise ValueError('Ref cannot publish containers')
    previous, planned = {}, {}
    for base in [exact,*aliases]:
        pair={variant:registry.inspect(IMAGE+':'+base+('-warp' if variant=='warp' else '')) for variant in PLATFORMS}
        for variant, old in pair.items():
            tag=base+('-warp' if variant=='warp' else '')
            previous[tag]=old
        if base==exact:
            if any(old and old['digest']!=digests[variant]['digest'] for variant,old in pair.items()):
                raise ValueError('Immutable container tag conflict: '+base)
        elif ref!='refs/heads/main':
            existing=[old for old in pair.values() if old]
            if any(parse_version(old['version'])>v or
                   (parse_version(old['version'])==v and old['sha']!=sha) for old in existing):
                emit('Preserving newer/equal-version alias: '+base)
                continue
        for variant in PLATFORMS:
            tag=base+('-warp' if variant=='warp' else '')
            planned[tag]=digests[variant]['digest']
    emit('## Container promotion: previous digests (recorded before writes)')
    for tag, old in previous.items():
        emit(f'- `{tag}`: `{old["digest"] if old else "absent"}`')
    for tag,digest in planned.items():
        if previous[tag] and previous[tag]['digest']==digest:
            emit(f'- Unchanged `{tag}` = `{digest}`')
            continue
        emit(f'- Starting `{tag}` -> `{digest}`')
        registry.promote(tag,digest)
        emit(f'- Completed `{tag}` -> `{digest}`')
    return planned


if __name__=='__main__':
    sha,ref=os.environ['GITHUB_SHA'],os.environ['GITHUB_REF']
    version=read_version('app.py')
    verify_release('release-assets',sha,version)
    digests=validate_digests('digests',sha,version)
    github=GitHub()
    main_sha=github.repo('git/ref/heads/main')['object']['sha'] if ref=='refs/heads/main' else None
    promote(digests,version,sha,ref,main_sha,Registry())
