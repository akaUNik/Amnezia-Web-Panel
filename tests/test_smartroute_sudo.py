"""Exercise the real SSH sudo prefix and shell parsing without privileges.

A shell function records argv at the sudo boundary before executing it with
the current UID. This verifies which process receives cp and redirection;
it does not emulate a real sudo policy or change routes on the test host.
"""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import managers.smartroute_manager as smart
from managers.ssh_manager import SSHManager


class LocalShellSSH(SSHManager):
    def __init__(self, directory):
        super().__init__('fixture.invalid', 22, 'operator')
        self.directory = directory
        self.staging = directory / 'upload.cidr'
        self.trace = directory / 'sudo-argv'
        self.sudo_exit = 0

    def upload_file(self, content, path):
        assert path == '/tmp/_smartr_own.cidr'
        self.staging.write_text(content)

    def run_command(self, command, timeout=60, **kwargs):
        command = command.replace('/tmp/_smartr_own.cidr', str(self.staging))
        shim = (
            "sudo() { printf '%s\\0' \"$@\" > " + shlex.quote(str(self.trace)) +
            '; ' + (f'return {self.sudo_exit};' if self.sudo_exit else '"$@";') +
            ' };\n'
        )
        result = subprocess.run(['sh', '-c', shim + command],
                                capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.stderr, result.returncode


class SmartRouteSudoTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='smartroute-sudo-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.host = self.root / 'host'
        self.own = self.host / 'own.cidr'
        self.instances = self.host / 'instances'
        constants = patch.multiple(smart, SMARTR_DIR=str(self.host),
                                   SMARTR_OWN=str(self.own),
                                   SMARTR_INSTANCES=str(self.instances))
        constants.start()
        self.addCleanup(constants.stop)
        self.ssh = LocalShellSSH(self.root)
        self.manager = smart.SmartRouteManager(self.ssh)

    def privileged_script(self):
        argv = self.ssh.trace.read_bytes().decode().rstrip('\0').split('\0')
        self.assertEqual(argv[:2], ['sh', '-c'])
        self.assertEqual(len(argv), 3, 'Compound must be one sudo shell argument')
        return argv[2]

    def test_own_subnets_create_overwrite_and_clear_inside_sudo(self):
        for cidrs in [['192.0.2.0/24'], ['198.51.100.0/24', '203.0.113.0/24'], []]:
            with self.subTest(cidrs=cidrs):
                self.manager.set_own_subnets(cidrs)
                self.assertEqual(self.own.read_text(), ''.join(c + '\n' for c in cidrs))
                self.assertFalse(self.ssh.staging.exists())
                self.assertIn('&& cp ', self.privileged_script())
                self.assertIn(str(self.own), self.privileged_script())

    def test_marker_redirection_is_inside_sudo_before_routes_are_applied(self):
        marker = self.instances / 'amnezia-awg3'

        def check_marker(container):
            self.assertEqual(marker.read_text(), str(smart.SMARTR_TABLE) + '\n')

        with patch.object(self.manager, '_push_merged_list', side_effect=check_marker) as push, \
                patch.object(self.manager, '_container_run', return_value='applied') as apply:
            self.assertEqual(self.manager.enable_instance(
                'amnezia-awg3', '/opt/amnezia/awg/awg0.conf', '10.8.0.0/24'), 'applied')
        push.assert_called_once_with('amnezia-awg3')
        apply.assert_called_once()
        self.assertIn('&& echo ', self.privileged_script())
        self.assertIn('> ' + str(marker), self.privileged_script())

    def test_sudo_failure_preserves_own_subnets_and_removes_upload(self):
        self.host.mkdir()
        self.own.write_text('192.0.2.0/24\n')
        self.ssh.sudo_exit = 73
        with self.assertRaises(smart.SmartRouteError):
            self.manager.set_own_subnets(['198.51.100.0/24'])
        self.assertEqual(self.own.read_text(), '192.0.2.0/24\n')
        self.assertFalse(self.ssh.staging.exists())

    def test_marker_failure_does_not_apply_routes(self):
        self.ssh.sudo_exit = 73
        with patch.object(self.manager, '_push_merged_list') as push, \
                patch.object(self.manager, '_container_run') as apply:
            with self.assertRaises(smart.SmartRouteError):
                self.manager.enable_instance(
                    'amnezia-awg3', '/opt/amnezia/awg/awg0.conf', '10.8.0.0/24')
        push.assert_not_called()
        apply.assert_not_called()
        self.assertFalse((self.instances / 'amnezia-awg3').exists())

    def test_root_session_keeps_file_operations_working(self):
        self.ssh._is_root = True
        self.manager.set_own_subnets(['192.0.2.0/24'])
        self.assertEqual(self.own.read_text(), '192.0.2.0/24\n')
        self.assertFalse(self.ssh.trace.exists())
