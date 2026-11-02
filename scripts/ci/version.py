"""Read release identity without importing the stateful application."""
import argparse
import ast
from dataclasses import dataclass
from functools import total_ordering
import os
from pathlib import Path
import re


SEMVER = re.compile(
    r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-((?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
)


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple
    metadata: str

    @property
    def precedence(self):
        pre = tuple((0, int(p)) if p.isdecimal() else (1, p)
                    for p in self.prerelease)
        return self.major, self.minor, self.patch, not self.prerelease, pre

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence == other.precedence

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence < other.precedence


def parse_version(value):
    match = SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError(f"Invalid SemVer: {value!r}")
    major, minor, patch, pre, metadata = match.groups()
    return Version(int(major), int(minor), int(patch),
                   tuple(pre.split('.')) if pre else (), metadata or '')


def read_version(source):
    tree = ast.parse(Path(source).read_text(encoding='utf-8-sig'))
    values = [node.value.value for node in tree.body
              if isinstance(node, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'CURRENT_VERSION'
                      for t in node.targets)
              and isinstance(node.value, ast.Constant)
              and isinstance(node.value.value, str)]
    if len(values) != 1:
        raise ValueError('Expected one literal CURRENT_VERSION assignment')
    parse_version(values[0])
    return values[0].removeprefix('v')


def validate_tag(version, tag):
    parse_version(version)
    if parse_version(tag).metadata:
        raise ValueError('Release tags must not contain build metadata: Docker tags forbid +')
    if version.removeprefix('v') != tag.removeprefix('v'):
        raise ValueError(f'Release tag {tag!r} differs from CURRENT_VERSION {version!r}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='app.py')
    args = parser.parse_args()
    version = read_version(args.source)
    ref = os.environ.get('GITHUB_REF', '')
    if ref.startswith('refs/tags/'):
        validate_tag(version, ref.removeprefix('refs/tags/'))
    print(version)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as out:
            out.write(f'version={version}\n')


if __name__ == '__main__':
    main()
