"""A failed dial must not permanently block unrelated peer operations."""
import asyncio
import pathlib
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import peer_lifecycle_lock as lifecycle
from managers.ssh_errors import SSHConnectionUnavailable
from managers.ssh_manager import SSHManager


class LifecycleSSHConnectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        old_path, old_fenced = lifecycle._fence_path, lifecycle._fenced
        self.addCleanup(setattr, lifecycle, '_fence_path', old_path)
        self.addCleanup(setattr, lifecycle, '_fenced', old_fenced)
        self.data_file = pathlib.Path(self.tmp.name) / 'data.json'
        lifecycle.configure(self.data_file)
        self.ssh = SSHManager('example.test', 22, 'root')

    async def assert_next_operation_allowed(self):
        @lifecycle.serialize_peer_lifecycle
        async def next_operation():
            return 'success'
        self.assertFalse(lifecycle.diagnostics()['blocked'])
        self.assertFalse(lifecycle._fence_path.exists())
        self.assertEqual(await next_operation(), 'success')

    async def test_connect_timeout_allows_other_operations_and_later_retry(self):
        @lifecycle.serialize_peer_lifecycle
        async def connect():
            return await asyncio.to_thread(self.ssh.connect)
        with patch.object(self.ssh, '_connect_once', side_effect=TimeoutError('dial timeout')) as dial:
            with self.assertRaises(SSHConnectionUnavailable):
                await connect()
            self.assertEqual(dial.call_count, 2)
        await self.assert_next_operation_allowed()
        with patch.object(self.ssh, '_connect_once'):
            self.assertTrue(await connect())
        self.assertEqual(self.ssh._connect_fail_count, 0)

    async def test_cooldown_before_command_does_not_fence(self):
        self.ssh._last_connect_fail = time.time()
        @lifecycle.serialize_peer_lifecycle
        async def command():
            return await asyncio.to_thread(self.ssh.run_command, 'true')
        with patch.object(self.ssh, 'connect') as dial:
            with self.assertRaises(SSHConnectionUnavailable):
                await command()
            dial.assert_not_called()
        await self.assert_next_operation_allowed()

    async def test_lost_command_reply_still_fences_across_restart(self):
        self.ssh.client = Mock()
        stdout = Mock()
        stdout.channel.recv_exit_status.side_effect = TimeoutError('reply lost')
        self.ssh.client.exec_command.return_value = (Mock(), stdout, Mock())
        @lifecycle.serialize_peer_lifecycle
        async def command():
            return await asyncio.to_thread(self.ssh.run_command, 'true')
        with patch.object(self.ssh, 'ensure_connected', return_value=True):
            with self.assertRaises(lifecycle.PeerLifecycleUncertain):
                await command()
        self.assertTrue(lifecycle._fence_path.exists())
        lifecycle.configure(self.data_file)
        with self.assertRaises(lifecycle.PeerLifecycleUncertain):
            await command()
        self.ssh.client.exec_command.assert_called_once()

    async def test_failed_reconnect_after_exec_failure_keeps_fence(self):
        self.ssh.client = Mock()
        self.ssh.client.exec_command.side_effect = EOFError('exec reply lost')
        @lifecycle.serialize_peer_lifecycle
        async def command():
            return await asyncio.to_thread(self.ssh.run_command, 'true')
        with patch.object(self.ssh, 'ensure_connected', return_value=True), \
             patch.object(self.ssh, '_connect_once', side_effect=TimeoutError('dial timeout')):
            with self.assertRaises(lifecycle.PeerLifecycleUncertain):
                await command()
        self.assertTrue(lifecycle.diagnostics()['blocked'])

    async def test_connect_error_cannot_mask_preceding_uncertainty(self):
        @lifecycle.serialize_peer_lifecycle
        async def operation():
            lifecycle.note_ssh_uncertainty()
            raise SSHConnectionUnavailable('dial failed after uncertain work')
        with self.assertRaises(lifecycle.PeerLifecycleUncertain):
            await operation()
        self.assertTrue(lifecycle._fence_path.exists())

    async def test_unclassified_timeout_still_fences(self):
        @lifecycle.serialize_peer_lifecycle
        async def operation():
            raise TimeoutError('unknown outcome')
        with self.assertRaises(lifecycle.PeerLifecycleUncertain):
            await operation()
        self.assertTrue(lifecycle._fence_path.exists())
