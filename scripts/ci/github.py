"""Small GitHub CLI adapter; credentials remain in the runner environment."""
import json
import subprocess
from urllib.parse import quote

from project_info import GITHUB_PROJECT


class GitHub:
    def api(self, path, method='GET', data=None, missing=False):
        command = ['gh', 'api', path, '--method', method]
        if data is not None:
            command += ['--input', '-']
        result = subprocess.run(command, input=json.dumps(data) if data is not None else None,
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            if missing and 'HTTP 404' in result.stderr:
                return None
            raise RuntimeError(f'GitHub {method} {path} failed: {result.stderr.strip()}')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def repo(self, path, **kwargs):
        return self.api(f'repos/{GITHUB_PROJECT}/{path}', **kwargs)

    def release(self, tag):
        published = self.repo('releases/tags/' + quote(tag, safe=''), missing=True)
        if published is not None:
            return published
        # The tag endpoint describes published releases. Authenticated list
        # pagination also finds a draft left by an interrupted publication.
        matches = []
        page = 1
        while True:
            releases = self.repo(f'releases?per_page=100&page={page}')
            matches.extend(r for r in releases if r.get('tag_name') == tag)
            if len(releases) < 100:
                break
            page += 1
        if len(matches) > 1:
            raise ValueError('Multiple releases claim the same tag')
        return matches[0] if matches else None

    def asset(self, asset_id):
        result = subprocess.run(['gh','api',f'repos/{GITHUB_PROJECT}/releases/assets/{asset_id}',
                                 '-H','Accept: application/octet-stream'],
                                capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError('Could not download release asset')
        return result.stdout

    def upload(self, tag, path):
        subprocess.run(['gh','release','upload',tag,str(path),'--repo',GITHUB_PROJECT,'--clobber'],
                       check=True, timeout=300)

    def tag_sha(self, tag):
        obj = self.repo('git/ref/tags/' + quote(tag, safe=''))['object']
        for _ in range(10):
            if obj['type'] == 'commit':
                return obj['sha']
            if obj['type'] != 'tag':
                break
            obj = self.repo('git/tags/' + obj['sha'])['object']
        raise ValueError('Tag does not resolve to a commit')
