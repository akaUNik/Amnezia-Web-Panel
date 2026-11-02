"""Real shell staging and changing peer state at the userspace build boundary."""
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from managers.awg_plugin_updater import AwgPluginUpdater, PluginUpdateError, USERSPACE, GO_REPO
from tests.awg_plugin_fixtures import GO_NEW, SOURCE_COMMIT, USERSPACE_TMP
from tests.test_awg_plugin_userspace_identity import IdentitySSH


class ShellStagingSSH(IdentitySSH):
    """Execute only staging shell commands; Docker/network remain simulated."""
    def __init__(self, sandbox, **kwargs):
        super().__init__(**kwargs)
        self.sandbox = sandbox
        self.owned = []
        self.staging = []
        self.elevated = []

    def run_command(self, command, timeout=60, **kwargs):
        if command.startswith('umask 077; mktemp -d /tmp/awg-plugin-userspace.'):
            p = subprocess.run(['sh', '-c', command], text=True, capture_output=True, check=True)
            self.owned.append(Path(p.stdout.strip()))
            self.commands.append(command)
            return p.stdout, p.stderr, p.returncode
        if command.startswith(("umask 077; printf '%s' ", 'mkdir -p /tmp/awg-plugin-update')):
            local = command.replace('/tmp/awg-plugin-update', str(self.sandbox / 'precreated'))
            p = subprocess.run(['sh', '-c', local], text=True, capture_output=True)
            self.commands.append(command)
            return p.stdout, p.stderr, p.returncode
        if command.startswith('docker build') and ' -f ' in command:
            args = shlex.split(command)
            path = Path(args[args.index('-f') + 1])
            self.staging.append((path, path.parent.stat().st_mode & 0o777, path.read_text()))
        if command.startswith('rm -rf -- '):
            path = Path(shlex.split(command)[-1])
            assert path in self.owned, 'cleanup must target an allocated workspace'
            shutil.rmtree(path)
            self.commands.append(command)
            return '', '', 0
        return super().run_command(command, timeout=timeout, **kwargs)

    run_sudo_script = run_command

    def run_sudo_command(self, command, timeout=60):
        self.elevated.append(command)
        args = shlex.split(command)
        assert args[:2] == ['bash', '-c']
        return self.run_command(args[2], timeout=timeout)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        patcher = patch('managers.awg_plugin_updater._github_commit', return_value=SOURCE_COMMIT)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_update(self, ssh):
        # Always remove private test directories, even on an assertion failure.
        self.addCleanup(lambda: [shutil.rmtree(p, ignore_errors=True) for p in ssh.owned])
        return AwgPluginUpdater(ssh).update(USERSPACE, GO_NEW, allow_interrupt=True)

    def test_precreated_directory_and_nested_symlink_are_never_used_or_cleaned(self):
        legacy = self.root / 'precreated'
        legacy.mkdir()
        victim = self.root / 'unrelated-config'
        victim.write_text('untouched')
        (legacy / 'Dockerfile.amnezia-awg3').symlink_to(victim)
        for nonroot in (False, True):
            with self.subTest(nonroot=nonroot):
                ssh = ShellStagingSSH(self.root)
                ssh._is_root = not nonroot
                result = self.run_update(ssh)
                self.assertEqual(result['state'], 'updated')
                self.assertEqual(victim.read_text(), 'untouched')
                self.assertTrue(legacy.exists())
                self.assertEqual(ssh.staging[0][1], 0o700)
                self.assertIn('FROM amnezia-awg-build-', ssh.staging[0][2])
                self.assertFalse(ssh.owned[0].exists())
                backups = [c for c in ssh.commands if c.startswith('docker cp amnezia-awg3:')]
                self.assertIn(str(ssh.owned[0]) + '/backup-amnezia-awg3', backups[0])
                if nonroot:
                    self.assertTrue(any('mktemp -d' in c for c in ssh.elevated))

    def test_top_level_symlink_and_other_operation_workspace_are_preserved(self):
        victim_dir = self.root / 'other-operation'
        victim_dir.mkdir()
        sentinel = victim_dir / 'keep'
        sentinel.write_text('recovery data')
        (self.root / 'precreated').symlink_to(victim_dir, target_is_directory=True)
        first = ShellStagingSSH(self.root)
        second = ShellStagingSSH(self.root)
        self.run_update(first)
        self.run_update(second)
        self.assertNotEqual(first.owned, second.owned)
        self.assertEqual(sentinel.read_text(), 'recovery data')
        self.assertTrue((self.root / 'precreated').is_symlink())

    def test_failed_or_malformed_allocation_never_builds_writes_or_removes(self):
        for output, code in [('', 1), ('/tmp/awg-plugin-update', 0),
                             ('/tmp/awg-plugin-userspace.0123456789/..', 0),
                             (USERSPACE_TMP + '\n/tmp/another', 0)]:
            with self.subTest(output=output, code=code):
                ssh = IdentitySSH()
                original = ssh.run_command
                def reply(command, **kwargs):
                    if 'mktemp -d' in command:
                        return output, '', code
                    return original(command, **kwargs)
                ssh.run_command = reply
                with self.assertRaises(PluginUpdateError):
                    AwgPluginUpdater(ssh).update(USERSPACE, GO_NEW, allow_interrupt=True)
                self.assertFalse(any(c.startswith(('docker build', 'docker stop', 'rm ', 'umask'))
                                     for c in ssh.commands))


class ChangingPeersSSH(IdentitySSH):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.peers = ['unchanged', 'revoked-during-build']
        self.snapshot = None

    def run_command(self, command, timeout=60, **kwargs):
        if command.startswith('docker build') and GO_REPO + '.git#' in command:
            self.peers = ['unchanged', 'created-during-build']
        if command.startswith('docker cp amnezia-awg3:/opt/amnezia '):
            self.snapshot = self.peers.copy()
        if command.startswith('docker cp /tmp/') and '/. amnezia-awg3:/opt/amnezia/' in command:
            self.peers = self.snapshot.copy()
        if command.startswith('docker exec') and 'grep -c' in command:
            self.commands.append(command)
            return str(len(self.peers)), '', 0
        return super().run_command(command, timeout=timeout, **kwargs)

    run_sudo_script = run_command


class SnapshotTests(unittest.TestCase):
    @patch('managers.awg_plugin_updater._github_commit', return_value=SOURCE_COMMIT)
    def test_build_time_peer_changes_survive_replacement(self, resolve):
        ssh = ChangingPeersSSH()
        result = AwgPluginUpdater(ssh).update(USERSPACE, GO_NEW, allow_interrupt=True)
        self.assertEqual(result['state'], 'updated')
        self.assertEqual(ssh.peers, ['unchanged', 'created-during-build'])
        builds = [i for i, c in enumerate(ssh.commands) if c.startswith('docker build')]
        snapshot = next(i for i, c in enumerate(ssh.commands) if c.startswith('docker cp amnezia-awg3:'))
        self.assertLess(max(builds), snapshot)

    @patch('managers.awg_plugin_updater._github_commit', return_value=SOURCE_COMMIT)
    def test_container_replacement_during_build_aborts_before_stop(self, resolve):
        ssh = IdentitySSH()
        original = ssh.run_command
        built = False
        def reply(command, **kwargs):
            nonlocal built
            if command.startswith('docker build'):
                built = True
            out, err, code = original(command, **kwargs)
            if command == 'docker inspect amnezia-awg3 2>/dev/null' and built:
                info = json.loads(out)
                info[0]['Id'] = 'different-container'
                out = json.dumps(info)
            return out, err, code
        ssh.run_command = reply
        ssh.run_sudo_script = reply
        with self.assertRaisesRegex(PluginUpdateError, 'changed during the build'):
            AwgPluginUpdater(ssh).update(USERSPACE, GO_NEW, allow_interrupt=True)
        self.assertFalse(any(c.startswith('docker stop') for c in ssh.commands))
