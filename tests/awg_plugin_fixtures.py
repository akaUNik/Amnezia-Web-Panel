"""Shared test doubles for the AmneziaWG plugin updater tests.

Kept in one place so the kernel and userspace suites can be written and
maintained independently without duplicating the SSH fake.
"""

import json

OLD = '3.1.20260812'
NEW = '3.1.20260906'
GO_NEW = '3.1.20260828'
KVER = '6.12.113+deb13-amd64'


class FakeSSH:
    """Records every command and answers through a handler."""

    def __init__(self, handler=None):
        self.commands = []
        self.handler = handler or (lambda command: ('', '', 0))

    def run_command(self, command, timeout=60, _retried=False, stdin_input=None):
        self.commands.append(command)
        return source_build_response(command) or self.handler(command)

    def run_sudo_script(self, script, timeout=120):
        self.commands.append(script)
        return source_build_response(script) or self.handler(script)

    def find(self, needle):
        return [command for command in self.commands if needle in command]


def container_inspect(shape='ok', networks=None, version_label=OLD, cap_add=None):
    """One entry of ``docker inspect <name>`` for a panel-created container."""
    payload = {
        'Image': 'sha256:oldimage',
        'Config': {
            'Image': 'amnezia-awg3',
            'Labels': {'org.opencontainers.image.version': version_label},
        },
        'HostConfig': {
            'Privileged': True,
            'CapAdd': list(cap_add) if cap_add is not None else ['NET_ADMIN', 'SYS_MODULE'],
            'Binds': ['/lib/modules:/lib/modules:rw'],
            'PortBindings': {'55424/udp': [{'HostPort': '55424'}]},
            'Sysctls': {'net.ipv4.conf.all.src_valid_mark': '1'},
            'RestartPolicy': {'Name': 'always'},
            'NetworkMode': 'default',
            'IpcMode': 'private',
            'Ulimits': [{'Name': 'nofile', 'Soft': 51200, 'Hard': 51200}],
        },
        'NetworkSettings': {'Networks': {'bridge': {}, **(networks or {})}},
    }
    if shape == 'bad':
        payload['HostConfig']['NetworkMode'] = 'host'
    return payload


def inspect_json(**kwargs):
    return json.dumps([container_inspect(**kwargs)])


DOCKER_PS = 'amnezia-awg3\tamneziavpn/amneziawg-go:latest'


# Source-build transport replies shared by the lifecycle suites. Identity and
# failure checks have separate tests that do not install these defaults.
SOURCE_COMMIT = 'a' * 40
SOURCE_IMAGE = 'sha256:' + 'a' * 64
DERIVED_IMAGE = 'sha256:' + 'b' * 64
BINARY_HASH = 'c' * 64

def source_build_response(command):
    if "--format '{{json .Config.Labels}}'" in command:
        return ('{}', '', 0)
    if command.startswith('cat /opt/amnezia/') and command.endswith('/Dockerfile'):
        return ('FROM amneziavpn/amneziawg-go:latest\n', '', 0)
    if command == 'cat /tmp/awg-plugin-update/userspace-source.iid':
        return (SOURCE_IMAGE, '', 0)
    if command == 'cat /tmp/awg-plugin-update/userspace-derived.iid':
        return (DERIVED_IMAGE, '', 0)
    if 'sha256sum' in command and '/usr/bin/amneziawg-go' in command:
        return (BINARY_HASH + '  /usr/bin/amneziawg-go\n', '', 0)
    return None
