"""Strict build identity, binary artifacts, and release asset preparation."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil

from .version import parse_version, read_version


BINARIES = {
    'Linux': ('Amnezia-Web-Panel-Linux', 'x86_64'),
    'Windows': ('Amnezia-Web-Panel-Windows.exe', 'x86_64'),
}
PLATFORMS = {'default': ['linux/amd64', 'linux/arm64'], 'warp': ['linux/amd64']}


def checksum(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def identity(sha, version):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('Expected full commit SHA')
    parse_version(version)
    return {'schema': 1, 'sha': sha, 'version': version.removeprefix('v')}


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def regular_file(path):
    if path.is_symlink() or not path.is_file() or not path.stat().st_size:
        raise ValueError(f'Missing, empty, or non-regular file: {path.name}')


def binary_metadata(directory, system, sha, version, check_host=True):
    filename, arch = BINARIES[system]
    if check_host:
        host_os = platform.system()
        host_arch = {'AMD64': 'x86_64', 'aarch64': 'arm64'}.get(platform.machine(), platform.machine())
        if (host_os, host_arch) != (system, arch):
            raise ValueError('Build runner OS/architecture does not match the matrix')
    path = Path(directory) / filename
    regular_file(path)
    data = {**identity(sha, version), 'os': system, 'arch': arch,
            'filename': filename, 'sha256': checksum(path)}
    write_json(Path(directory) / f'metadata-{system}.json', data)
    return data


def validate_binaries(directory, sha, version):
    directory = Path(directory)
    expected = {name for system, (filename, _) in BINARIES.items()
                for name in (filename, f'metadata-{system}.json')}
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('Expected exactly two binaries and their two metadata files')
    result = []
    for system, (filename, arch) in BINARIES.items():
        path = directory / filename
        meta = directory / f'metadata-{system}.json'
        regular_file(path)
        regular_file(meta)
        data = json.loads(meta.read_text(encoding='utf-8'))
        expected_data = {**identity(sha, version), 'os': system, 'arch': arch,
                         'filename': filename, 'sha256': checksum(path)}
        if data != expected_data:
            raise ValueError(f'Binary identity/checksum mismatch: {filename}')
        result.append(data)
    return result


def digest_metadata(variant, digest, sha, version):
    if variant not in PLATFORMS or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('Invalid container variant or digest')
    return {**identity(sha, version), 'variant': variant,
            'digest': digest, 'platforms': PLATFORMS[variant]}


def validate_digests(directory, sha, version):
    directory = Path(directory)
    if {p.name for p in directory.iterdir()} != {'digest-default.json', 'digest-warp.json'}:
        raise ValueError('Expected exactly two container digest files')
    result = {}
    for variant in PLATFORMS:
        path = directory / f'digest-{variant}.json'
        regular_file(path)
        data = json.loads(path.read_text(encoding='utf-8'))
        expected = digest_metadata(variant, data.get('digest', ''), sha, version)
        if data != expected:
            raise ValueError(f'Container identity/platform mismatch: {variant}')
        result[variant] = data
    return result


def prepare(binaries, digests, output, sha, version, ref, run_url):
    metadata = validate_binaries(binaries, sha, version)
    containers = validate_digests(digests, sha, version)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    info = {**identity(sha, version), 'ref': ref, 'run_url': run_url,
            'binaries': metadata, 'containers': containers}
    write_json(output / 'build-info.json', info)
    for system, (filename, _) in BINARIES.items():
        target = output / filename
        shutil.copyfile(Path(binaries) / filename, target)
        if system != 'Windows':
            target.chmod(0o755)
    files = sorted([filename for filename, _ in BINARIES.values()] + ['build-info.json'])
    (output / 'SHA256SUMS').write_text(''.join(
        f'{checksum(output / filename)}  {filename}\n' for filename in files), encoding='utf-8')
    verify_release(output, sha, version)
    return info


def verify_release(directory, sha, version):
    directory = Path(directory)
    names = {filename for filename, _ in BINARIES.values()} | {'build-info.json', 'SHA256SUMS'}
    if {p.name for p in directory.iterdir()} != names:
        raise ValueError('Release must contain exactly four assets')
    for name in names:
        regular_file(directory / name)
    info = json.loads((directory / 'build-info.json').read_text(encoding='utf-8'))
    if any(info.get(k) != v for k, v in identity(sha, version).items()):
        raise ValueError('Release identity mismatch')
    if {b.get('os') for b in info.get('binaries', [])} != set(BINARIES) or len(info['binaries']) != len(BINARIES):
        raise ValueError('Release binary metadata is incomplete')
    for b in info['binaries']:
        system = b['os']
        filename, arch = BINARIES[system]
        expected = {**identity(sha, version), 'os': system, 'arch': arch,
                    'filename': filename, 'sha256': checksum(directory / filename)}
        if b != expected:
            raise ValueError('Release binary metadata mismatch')
    if set(info.get('containers', {})) != set(PLATFORMS):
        raise ValueError('Release container metadata is incomplete')
    for variant, c in info['containers'].items():
        if c != digest_metadata(variant, c.get('digest', ''), sha, version):
            raise ValueError('Release container identity mismatch')
    expected_sums = ''.join(f'{checksum(directory / name)}  {name}\n'
                            for name in sorted(names - {'SHA256SUMS'}))
    if (directory / 'SHA256SUMS').read_text(encoding='utf-8') != expected_sums:
        raise ValueError('Release checksum manifest mismatch')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('binary', 'digest', 'prepare'))
    parser.add_argument('--directory', default='dist')
    parser.add_argument('--system', choices=BINARIES)
    parser.add_argument('--variant', choices=PLATFORMS)
    parser.add_argument('--digest')
    parser.add_argument('--sha', required=True)
    parser.add_argument('--digests', default='digests')
    parser.add_argument('--output', default='release-assets')
    parser.add_argument('--ref', default='')
    parser.add_argument('--run-url', default='')
    args = parser.parse_args()
    version = read_version('app.py')
    if args.operation == 'binary':
        binary_metadata(args.directory, args.system, args.sha, version)
    elif args.operation == 'digest':
        Path(args.directory).mkdir(parents=True, exist_ok=True)
        write_json(Path(args.directory) / f'digest-{args.variant}.json',
                   digest_metadata(args.variant, args.digest, args.sha, version))
    else:
        prepare(args.directory, args.digests, args.output, args.sha, version, args.ref, args.run_url)


if __name__ == '__main__':
    main()
