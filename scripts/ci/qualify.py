"""Qualify public images with disposable Linux Compose projects."""
import argparse
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from .artifacts import PLATFORMS
from .promote import IMAGE, Registry, summary

ROOT = Path(__file__).resolve().parents[2]
STATE_FILES = ('/app/data/data.json', '/app/data/known_hosts.fixture',
               '/app/data/tunnels.fixture.json')


def validate_inputs(tag, sha):
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', tag):
        raise ValueError('Expected a container tag, not a registry or image URL')
    if sha and not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Expected a full lowercase source commit SHA')


def validate_pair(images, sha):
    if set(images) != set(PLATFORMS) or any(not image for image in images.values()):
        raise ValueError('Both public image variants are required')
    if len({(image['sha'], image['version']) for image in images.values()}) != 1:
        raise ValueError('Published image variants have different source identities')
    for variant, image in images.items():
        if image['platforms'] != sorted(PLATFORMS[variant]):
            raise ValueError('Published image platform mismatch: ' + variant)
        if sha and image['sha'] != sha:
            raise ValueError('Published image does not match the requested source SHA')


def run(command, env, timeout=180):
    result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                            capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(command) + '\n' +
                           result.stdout[-8000:] + result.stderr[-8000:])
    return result.stdout


def ready(port, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for path in ('/login', '/static/js/updates.js'):
                with urllib.request.urlopen(f'http://127.0.0.1:{port}' + path, timeout=2) as response:
                    if response.status != 200 or len(response.read()) < 100:
                        raise RuntimeError('Published image is missing web resources')
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(.5)
    raise RuntimeError('Published Compose readiness timed out')


def exercise(variant, images, directory, env):
    project = 'panel-qualification-' + variant + '-' + uuid.uuid4().hex[:10]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    override = directory / (variant + '.yml')
    override.write_text('services:\n  amnezia_panel:\n'
                        f'    container_name: {project}\n'
                        '    platform: linux/amd64\n    restart: "no"\n'
                        '    environment:\n      SECRET_KEY: isolated-compose-qualification\n')
    empty_env = directory / 'empty.env'
    empty_env.touch()
    env = {**env, 'APP_PORT': '127.0.0.1:' + str(port),
           'PANEL_IMAGE': IMAGE + '@' + images['default']['digest'],
           'PANEL_WARP_IMAGE': IMAGE + '@' + images['warp']['digest']}
    command = ['docker', 'compose', '--env-file', str(empty_env),
               '--project-name', project, '-f', 'docker-compose.yml']
    if variant == 'warp':
        command += ['-f', 'docker-compose.warp.yml']
    command += ['-f', str(override)]

    def compose(*args):
        return run(command + list(args), env)

    def execute(code):
        return compose('exec', '-T', 'amnezia_panel', 'python3', '-c', code)

    hashes = ('import hashlib,json; from pathlib import Path; '
              'print(json.dumps({p:hashlib.sha256(Path(p).read_bytes()).hexdigest() '
              'for p in ' + repr(STATE_FILES) + '}))')
    try:
        compose('up', '-d', '--pull', 'never')
        ready(port)
        # Only synthetic state inside this newly created, uniquely named volume.
        execute("import json; from pathlib import Path; p=Path('/app/data/data.json'); "
                "data=json.loads(p.read_text()); data['qualification_marker']='" + project + "'; "
                "p.write_text(json.dumps(data)); "
                "Path('/app/data/known_hosts.fixture').write_text('synthetic-host-key'); "
                "Path('/app/data/tunnels.fixture.json').write_text('{}')")
        if variant == 'warp':
            execute("from pathlib import Path; "
                    "Path('/var/lib/cloudflare-warp/qualification.fixture').write_text('synthetic-warp-state')")
        before = json.loads(execute(hashes))
        previous = compose('ps', '-q', 'amnezia_panel').strip()
        compose('up', '-d', '--force-recreate', '--pull', 'never')
        ready(port)
        current = compose('ps', '-q', 'amnezia_panel').strip()
        if current == previous:
            raise ValueError('Compose did not recreate the container')
        after = json.loads(execute(hashes))
        if before != after:
            raise ValueError('Persistent panel state changed during recreation')
        if variant == 'warp':
            execute("import os,stat; from pathlib import Path; "
                    "assert stat.S_ISCHR(os.stat('/dev/net/tun').st_mode); "
                    "assert Path('/var/lib/cloudflare-warp/qualification.fixture').read_text()=='synthetic-warp-state'")
            compose('exec', '-T', 'amnezia_panel', 'warp-cli', '--version')
        container = json.loads(run(['docker', 'inspect', current], env))[0]
        image = json.loads(run(['docker', 'image', 'inspect', container['Image']], env))[0]
        expected = images[variant]
        labels = image['Config']['Labels']
        if (labels['org.opencontainers.image.revision'] != expected['sha'] or
                labels['org.opencontainers.image.version'] != expected['version'] or
                image['Os'] != 'linux' or image['Architecture'] != 'amd64'):
            raise ValueError('Running container image identity mismatch')
        return {'variant': variant, 'image': IMAGE + '@' + expected['digest'],
                'source_sha': expected['sha'], 'version': expected['version'],
                'platforms': expected['platforms'], 'container_recreated': True,
                'state_sha256': after,
                'mounts': [{'destination': mount['Destination'], 'name': mount.get('Name')}
                           for mount in container['Mounts']],
                'devices': container['HostConfig']['Devices'],
                'cap_add': container['HostConfig']['CapAdd']}
    except Exception:
        try:
            print(compose('logs', '--no-color', '--tail', '80'))
        except RuntimeError:
            pass
        raise
    finally:
        # These project/volume names were generated above, never supplied by input.
        compose('down', '--volumes')


def qualify(tag, sha, output):
    validate_inputs(tag, sha)
    if not os.sys.platform.startswith('linux'):
        raise ValueError('Published Compose qualification requires a Linux runner')
    original_config = Path(os.environ.get('DOCKER_CONFIG', Path.home() / '.docker'))
    with tempfile.TemporaryDirectory(prefix='panel-public-qualification-') as tmp:
        directory = Path(tmp)
        config = directory / 'docker'
        config.mkdir()
        (config / 'config.json').write_text('{"auths":{}}\n')
        # Reuse CLI plugins only; no auths, credential store, or helper is inherited.
        if (original_config / 'cli-plugins').is_dir():
            (config / 'cli-plugins').symlink_to(original_config / 'cli-plugins', target_is_directory=True)
        env = {**os.environ, 'DOCKER_CONFIG': str(config)}
        previous = os.environ.get('DOCKER_CONFIG')
        os.environ['DOCKER_CONFIG'] = str(config)
        try:
            images = {variant: Registry().inspect(IMAGE + ':' + tag + ('-warp' if variant == 'warp' else ''))
                      for variant in PLATFORMS}
        finally:
            if previous is None:
                os.environ.pop('DOCKER_CONFIG', None)
            else:
                os.environ['DOCKER_CONFIG'] = previous
        validate_pair(images, sha)
        results = []
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        for variant in PLATFORMS:
            reference = IMAGE + '@' + images[variant]['digest']
            run(['docker', 'pull', '--platform', 'linux/amd64', reference], env, timeout=600)
            result = exercise(variant, images, directory, env)
            results.append(result)
            output.write_text(json.dumps({'tag': tag, 'anonymous': True, 'results': results}, indent=2) + '\n')
            summary(f'- Passed `{variant}`: anonymous pull, login/static, recreation, persistent state; `{reference}`')
        return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', default='latest')
    parser.add_argument('--sha', default='')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    qualify(args.tag, args.sha, args.output)
