"""Source identity, immutable activation and fail-closed binary verification."""
import json
import unittest
from unittest.mock import patch, MagicMock

from managers.awg_plugin_updater import (
    AwgPluginUpdater, PluginUpdateError, USERSPACE, GO_REPO,
    SOURCE_LABEL, HASH_LABEL, _github_commit, _release_dockerfile,
)
from tests.awg_plugin_fixtures import SOURCE_COMMIT, SOURCE_IMAGE, DERIVED_IMAGE, BINARY_HASH, GO_NEW, USERSPACE_TMP
from tests.test_awg_plugin_userspace_update import userspace_handler


class IdentitySSH:
    _is_root = True

    def __init__(self, *, derived_hash=BINARY_HASH, runtime_hash=BINARY_HASH,
                 fail_source=False, fail_derived=False, dockerfile=None):
        self.commands = []
        self.delegate = userspace_handler(exec_version_output='amneziawg-go 0.0.20250522')
        self.derived_hash = derived_hash
        self.runtime_hash = runtime_hash
        self.fail_source = fail_source
        self.fail_derived = fail_derived
        self.dockerfile = dockerfile or 'FROM amneziavpn/amneziawg-go:latest\nRUN echo setup\n'

    def run_command(self, command, timeout=60, **kwargs):
        self.commands.append(command)
        if command == 'umask 077; mktemp -d /tmp/awg-plugin-userspace.XXXXXXXXXX':
            return USERSPACE_TMP, '', 0
        if "--format '{{json .Config.Labels}}'" in command:
            return '{}', '', 0
        if command.startswith('cat /opt/amnezia/'):
            return self.dockerfile, '', 0
        if command.endswith('userspace-source.iid') and command.startswith('cat '):
            return SOURCE_IMAGE, '', 0
        if command.endswith('userspace-derived.iid') and command.startswith('cat '):
            return DERIVED_IMAGE, '', 0
        if command.startswith('docker build'):
            source = GO_REPO + '.git#' in command
            if (source and self.fail_source) or (not source and self.fail_derived):
                return '', 'failed', 1
        if 'sha256sum' in command:
            digest = self.runtime_hash if command.startswith('docker exec ') else (
                BINARY_HASH if SOURCE_IMAGE in command else self.derived_hash)
            return digest + '  /usr/bin/amneziawg-go\n', '', 0
        return self.delegate(command)

    run_sudo_script = run_command


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.resolve = patch('managers.awg_plugin_updater._github_commit', return_value=SOURCE_COMMIT).start()
        self.addCleanup(patch.stopall)

    def update(self, ssh, **kwargs):
        return AwgPluginUpdater(ssh).update(USERSPACE, GO_NEW, allow_interrupt=True, **kwargs)

    def test_source_commit_and_binary_hash_identify_the_release(self):
        ssh = IdentitySSH()
        result = self.update(ssh)
        self.assertEqual(result['installed'], GO_NEW)
        self.assertEqual(result['source_revision'], SOURCE_COMMIT)
        self.resolve.assert_called_once_with(GO_NEW)
        source = next(c for c in ssh.commands if GO_REPO + '.git#' in c)
        self.assertIn(GO_REPO + '.git#' + SOURCE_COMMIT, source)
        derived = next(c for c in ssh.commands if c.startswith('docker build') and '--label' in c)
        self.assertIn(HASH_LABEL + '=' + BINARY_HASH, derived)
        self.assertIn('org.opencontainers.image.revision=' + SOURCE_COMMIT, derived)
        run = next(c for c in ssh.commands if c.startswith('docker run -d'))
        self.assertTrue(run.endswith(DERIVED_IMAGE), 'activation must use the checked image ID')
        self.assertTrue(any(c.startswith('docker exec ') and 'sha256sum' in c for c in ssh.commands))

    def test_missing_commit_never_builds_or_stops(self):
        self.resolve.side_effect = OSError('unavailable')
        ssh = IdentitySSH()
        with self.assertRaises(PluginUpdateError) as error:
            self.update(ssh)
        self.assertEqual(error.exception.code, 'version_check_failed')
        self.assertFalse(any(c.startswith(('docker build', 'docker stop')) for c in ssh.commands))

    def test_both_build_failures_leave_original_container_running(self):
        for field in ['fail_source', 'fail_derived']:
            with self.subTest(field=field):
                ssh = IdentitySSH(**{field: True})
                with self.assertRaises(PluginUpdateError):
                    self.update(ssh)
                self.assertFalse(any(c.startswith('docker stop') for c in ssh.commands))

    def test_modified_derived_binary_is_rejected_before_stop(self):
        ssh = IdentitySSH(derived_hash='d' * 64)
        with self.assertRaises(PluginUpdateError) as error:
            self.update(ssh)
        self.assertIn('differs', error.exception.message)
        self.assertFalse(any(c.startswith('docker stop') for c in ssh.commands))

    def test_runtime_binary_mismatch_rolls_back_and_keeps_old_until_restored(self):
        ssh = IdentitySSH(runtime_hash='d' * 64)
        with self.assertRaises(PluginUpdateError):
            self.update(ssh)
        self.assertIn('docker rm -f amnezia-awg3', ssh.commands)
        self.assertIn('docker start amnezia-awg3', ssh.commands)
        self.assertFalse(any(c.startswith('docker rm -f amnezia-awg3.old-') for c in ssh.commands))

    def test_dockerfile_contents_are_not_journaled(self):
        secret = 'DO_NOT_LOG_THIS_FIXTURE'
        ssh = IdentitySSH(dockerfile='FROM amneziavpn/amneziawg-go:latest\nRUN echo ' + secret + '\n')
        journal = []
        self.update(ssh, log=lambda stage, text: journal.append(text))
        self.assertNotIn(secret, '\n'.join(journal))
        self.assertTrue(any(secret in c for c in ssh.commands))

    def test_custom_or_multiple_bases_fail_before_source_build(self):
        for text in ['FROM alpine\n', 'FROM amneziavpn/amneziawg-go:latest\nFROM alpine\n']:
            with self.subTest(text=text), self.assertRaises(PluginUpdateError):
                self.update(IdentitySSH(dockerfile=text))
        self.resolve.assert_not_called()

    def test_installed_release_requires_matching_receipt_and_binary(self):
        labels = {SOURCE_LABEL: 'source-v1', HASH_LABEL: BINARY_HASH,
                  'org.opencontainers.image.version': GO_NEW,
                  'org.opencontainers.image.revision': SOURCE_COMMIT,
                  'org.opencontainers.image.source': GO_REPO}
        ssh = MagicMock()
        ssh._is_root = True
        updater = AwgPluginUpdater(ssh)
        for change, digest, expected in [({}, BINARY_HASH, GO_NEW), ({}, 'd' * 64, None),
                ({SOURCE_LABEL: 'unknown'}, BINARY_HASH, None),
                ({'org.opencontainers.image.revision': 'abc'}, BINARY_HASH, None),
                ({'org.opencontainers.image.source': 'https://example.com'}, BINARY_HASH, None)]:
            with self.subTest(change=change, digest=digest):
                ssh.run_command.side_effect = [(json.dumps(dict(labels, **change)), '', 0),
                                               (digest + '  /usr/bin/amneziawg-go\n', '', 0)]
                self.assertEqual(updater._verified_userspace_version('amnezia-awg3'), expected)

    def test_invalid_receipt_cannot_fall_back_to_version_label(self):
        ssh = MagicMock()
        ssh._is_root = True
        labels = {SOURCE_LABEL: 'source-v1', HASH_LABEL: BINARY_HASH,
                  'org.opencontainers.image.version': GO_NEW,
                  'org.opencontainers.image.revision': SOURCE_COMMIT,
                  'org.opencontainers.image.source': GO_REPO}
        ssh.run_command.side_effect = [
            (json.dumps(labels), '', 0),
            ('d' * 64 + '  /usr/bin/amneziawg-go\n', '', 0),
        ]
        updater = AwgPluginUpdater(ssh)
        self.assertIsNone(updater._userspace_version([('amnezia-awg3', 'image:' + GO_NEW)]))
        self.assertEqual(ssh.run_command.call_count, 2)

    def test_mixed_containers_report_oldest_and_unknown_is_not_masked(self):
        updater = AwgPluginUpdater(MagicMock())
        containers = [('amnezia-awg', 'legacy:latest'), ('amnezia-awg3', 'current:latest')]
        with patch.object(updater, '_userspace_labels', return_value={}), \
                patch.object(updater, '_verified_userspace_version', side_effect=['0.0.20250522', GO_NEW]):
            self.assertEqual(updater._userspace_version(containers), '0.0.20250522')
        with patch.object(updater, '_userspace_labels', return_value={}), \
                patch.object(updater, '_verified_userspace_version', side_effect=[GO_NEW, None]), \
                patch.object(updater, '_exec', return_value=('', '', 1)):
            self.assertIsNone(updater._userspace_version(containers))

    def test_invalid_image_id_prevents_activation(self):
        ssh = IdentitySSH()
        updater = AwgPluginUpdater(ssh)
        original = ssh.run_command

        def corrupt_id(command, **kwargs):
            if command.startswith('cat ') and command.endswith('userspace-derived.iid'):
                return 'mutable:latest', '', 0
            return original(command, **kwargs)

        ssh.run_command = corrupt_id
        with self.assertRaises(PluginUpdateError):
            updater.update(USERSPACE, GO_NEW, allow_interrupt=True)
        self.assertFalse(any(c.startswith('docker stop') for c in ssh.commands))


class ResolveCommitTests(unittest.TestCase):
    def test_resolver_uses_tag_and_validates_full_commit(self):
        for value in [SOURCE_COMMIT, 'abc', 'a' * 39 + ';', None]:
            with self.subTest(value=value), patch('urllib.request.urlopen') as fetch:
                fetch.return_value.__enter__.return_value.read.return_value = json.dumps({'sha': value}).encode()
                if value == SOURCE_COMMIT:
                    self.assertEqual(_github_commit(GO_NEW), SOURCE_COMMIT)
                else:
                    with self.assertRaises(ValueError):
                        _github_commit(GO_NEW)
                self.assertTrue(fetch.call_args.args[0].full_url.endswith('/commits/v' + GO_NEW))

    def test_invalid_version_does_not_make_network_request(self):
        with patch('urllib.request.urlopen') as fetch, self.assertRaises(ValueError):
            _github_commit('latest; echo invalid')
        fetch.assert_not_called()

    def test_original_dockerfile_is_preserved_apart_from_base(self):
        original = 'FROM amneziavpn/amneziawg-go@sha256:' + 'a' * 64 + '\nRUN echo hello\n'
        self.assertEqual(_release_dockerfile(original, 'local-source'), 'FROM local-source\nRUN echo hello\n')
