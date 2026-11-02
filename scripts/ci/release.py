"""Publish complete releases through an owned draft, readback, and verification."""
import json
import os
from pathlib import Path
import re
import tempfile

from project_info import GITHUB_PROJECT
from .artifacts import checksum, verify_release
from .github import GitHub
from .promote import summary
from .version import parse_version, read_version, validate_tag


def release_notes(tag, sha, github, changelog=Path('CHANGELOG.md')):
    if changelog.is_file():
        text=changelog.read_text(encoding='utf-8')
        section=re.search(r'^## \['+re.escape(tag.removeprefix('v'))+r'\][^\n]*\n(.*?)(?=^## \[|\Z)',text,re.M|re.S)
        if section:
            previous=re.search(r'^## \[([0-9]+\.[0-9]+\.[0-9]+[^\]]*)\]',text[section.end():],re.M)
            base='https://github.com/'+GITHUB_PROJECT
            link=(base+'/compare/v'+previous[1]+'...'+tag if previous else base+'/commits/'+tag)
            return section[1].strip()+'\n\n**Full Changelog**: '+link
    return github.repo('releases/generate-notes',method='POST',
                       data={'tag_name':tag,'target_commitish':sha})['body']


def readback(github, release, directory, names):
    assets = release.get('assets',[])
    if len({a['name'] for a in assets}) != len(assets):
        raise ValueError('Duplicate remote asset names')
    if {a['name'] for a in assets} != names:
        raise ValueError('Remote release asset set is incomplete or unexpected')
    for asset in assets:
        (directory / asset['name']).write_bytes(github.asset(asset['id']))


def same_assets(local, remote):
    if any(checksum(path)!=checksum(remote/path.name) for path in local.iterdir()):
        raise ValueError('Published release content/checksum conflict; refusing overwrite')


def publish(directory, tag, sha, version, github, emit=summary):
    directory = Path(directory)
    validate_tag(version, tag)
    verify_release(directory, sha, version)
    if github.tag_sha(tag)!=sha:
        raise ValueError('Remote tag does not resolve to the build SHA')
    marker=f'<!-- amnezia-ci:{sha} -->'
    names={path.name for path in directory.iterdir()}
    current=github.release(tag)
    prerelease=bool(parse_version(version).prerelease)
    if current is None:
        notes=release_notes(tag,sha,github)
        current=github.repo('releases',method='POST',data={
            'tag_name':tag,'target_commitish':sha,'name':tag,'draft':True,
            'prerelease':prerelease,'body':marker+'\n\n'+notes})
        emit(f'Created draft release `{tag}` for `{sha}`')
    with tempfile.TemporaryDirectory(prefix='release-readback-') as tmp:
        downloaded=Path(tmp)
        if not current['draft']:
            readback(github,current,downloaded,names)
            verify_release(downloaded,sha,version)
            same_assets(directory,downloaded)
            if current.get('prerelease')!=prerelease:
                raise ValueError('Published release channel conflict')
            emit(f'Published release `{tag}` already matches; no changes')
            return current
        def own_draft(release):
            if (not release or not release.get('draft') or release.get('tag_name')!=tag or
                release.get('target_commitish')!=sha or
                not release.get('body','').startswith(marker)):
                raise ValueError('Refusing to modify an unrelated or published draft')
        own_draft(current)
        if any(a['name'] not in names for a in current.get('assets',[])):
            raise ValueError('Draft contains unrelated assets')
        for asset in current.get('assets',[]):
            if asset['name']=='build-info.json':
                try:
                    info=json.loads(github.asset(asset['id']))
                except (ValueError,UnicodeDecodeError):
                    # A truncated draft upload can be replaced; marker/target
                    # identity was verified before any upload is attempted.
                    continue
                if info.get('sha')!=sha or info.get('version')!=version:
                    raise ValueError('Draft build-info SHA/version conflict')
        # Upload one at a time. A failed upload leaves the owned draft intact.
        for path in sorted(directory.iterdir()):
            current=github.release(tag)
            own_draft(current)
            existing=next((a for a in current.get('assets',[]) if a['name']==path.name),None)
            if existing and github.asset(existing['id'])==path.read_bytes():
                continue
            github.upload(tag,path)
            emit(f'Uploaded draft asset `{path.name}`')
        current=github.release(tag)
        own_draft(current)
        readback(github,current,downloaded,names)
        verify_release(downloaded,sha,version)
        same_assets(directory,downloaded)
        # Recheck tag identity immediately before final publication.
        if github.tag_sha(tag)!=sha:
            raise ValueError('Tag changed during draft upload')
        latest=github.repo('releases/latest',missing=True)
        make_latest=not prerelease
        if latest:
            if latest.get('draft') or latest.get('prerelease'):
                raise ValueError('Unexpected stable latest release response')
            make_latest=make_latest and parse_version(version)>parse_version(latest['tag_name'])
        published=github.repo('releases/'+str(current['id']),method='PATCH',data={
            'draft':False,'prerelease':prerelease,
            'make_latest':'true' if make_latest else 'false'})
        if published.get('draft') or published.get('tag_name')!=tag or published.get('prerelease')!=prerelease:
            raise ValueError('Release publication response does not match the verified draft')
        emit(f'Published verified release `{tag}`; make_latest={make_latest}')
        return published


if __name__=='__main__':
    ref=os.environ['GITHUB_REF']
    if not ref.startswith('refs/tags/'):
        raise ValueError('Only tag pushes can publish releases')
    publish('release-assets',ref.removeprefix('refs/tags/'),os.environ['GITHUB_SHA'],
            read_version('app.py'),GitHub())
