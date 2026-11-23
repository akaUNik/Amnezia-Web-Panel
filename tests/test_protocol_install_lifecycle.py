"""Exercise protocol installation over ASGI with gated remote work."""
import asyncio
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

import app as panel
import peer_lifecycle_lock as lifecycle
from managers.ssh_manager import SSHManager
from ssh_channel_fixture import Channel


class ProtocolInstallLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stack = self.enterContext(ExitStack())
        self.path = Path(stack.enter_context(tempfile.TemporaryDirectory())) / 'data.json'
        self.path.write_text(json.dumps({'servers': [
            {'uid': 'install-fixture', 'host': '192.0.2.1', 'protocols': {}}
        ]}))
        self.fence = self.path.with_suffix('.json.lifecycle-uncertain.json')
        stack.enter_context(patch.multiple(lifecycle, _fence_path=self.fence, _fenced=False,
                                           RESPONSE_WAIT_SECONDS=0.02, QUEUE_WAIT_SECONDS=0.02))
        self.ssh = Mock()
        self.manager = Mock()
        admin = {'id': 'fixture', 'role': 'admin'}
        stack.enter_context(patch.multiple(
            panel, DATA_FILE=str(self.path), get_current_user=Mock(return_value=admin),
            _check_admin=Mock(return_value=admin), get_ssh=Mock(return_value=self.ssh),
            get_protocol_manager=Mock(return_value=self.manager),
            ensure_docker_installed=Mock(return_value=''), get_used_ports=Mock(return_value={}),
        ))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=panel.app), base_url='http://panel.test',
        ))

    async def install(self, protocol='awg3', **options):
        return await self.client.post('/api/servers/0/install', json={
            'protocol': protocol, 'port': '55424', **options,
        })

    def protocols(self):
        return json.loads(self.path.read_text())['servers'][0]['protocols']

    async def test_busy_lock_rejects_install_before_start(self):
        async with lifecycle.snapshot_interlock():
            response = await self.install()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['code'], 'peer_lifecycle_busy')
        self.assertEqual(response.headers['Retry-After'], '10')
        self.manager.install_protocol.assert_not_called()

    async def test_persisted_fence_rejects_install_before_start(self):
        self.fence.write_text('{"reason": "ssh_result_unknown"}')
        response = await self.install()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()['code'], 'peer_lifecycle_uncertain')
        self.manager.install_protocol.assert_not_called()

    async def test_cancelled_request_keeps_interlock_until_installer_finishes(self):
        entered, release = self.gate_installer({'status': 'success'})
        request = asyncio.create_task(self.install())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            response = await self.install(install_another=True)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()['code'], 'peer_lifecycle_busy')
            self.assertEqual(self.protocols(), {})
            self.manager.install_protocol.assert_called_once()
        finally:
            release.set()
            with patch.object(lifecycle, 'QUEUE_WAIT_SECONDS', 2):
                async with lifecycle.snapshot_interlock():
                    pass
        self.assertTrue(self.protocols()['awg3']['installed'])
        response = await self.install(install_another=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['protocol'], 'awg3__2')

    async def test_lost_ssh_reply_returns_recovery_required_and_persists_fence(self):
        ssh = SSHManager('192.0.2.1', 22, 'root')
        ssh.client = Mock()
        channel = Channel()
        channel.exec_command = Mock(side_effect=EOFError('reply lost'))
        ssh.client.get_transport.return_value.open_session.return_value = channel

        def install(*args, **kwargs):
            _, error, code = ssh.run_command('docker build .')
            self.assertEqual(code, -1)
            return {'status': 'error', 'message': error}

        self.manager.install_protocol.side_effect = install
        with patch.object(panel, 'get_ssh', return_value=ssh), patch.object(ssh, 'connect'):
            response = await self.install()
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()['code'], 'peer_lifecycle_uncertain')
            self.assertEqual(json.loads(self.fence.read_text())['reason'], 'ssh_result_unknown')
            self.assertEqual(self.protocols(), {})
            with patch.object(lifecycle, '_fenced', False):
                retry = await self.install()
            self.assertEqual(retry.status_code, 503, retry.text)
            self.assertEqual(retry.json()['code'], 'peer_lifecycle_uncertain')
        channel.exec_command.assert_called_once_with('docker build .')
        self.manager.install_protocol.assert_called_once()

    def gate_installer(self, result):
        entered, release = threading.Event(), threading.Event()

        def install(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Installer was not released')
            return result

        self.manager.install_protocol.side_effect = install
        return entered, release

    async def test_slow_install_returns_terminal_result(self):
        response = await self.finish_slow_install({'status': 'success', 'log': ['Interface verified']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['protocol'], 'awg3')
        self.assertEqual(response.json()['log'], ['Interface verified'])
        self.assertTrue(self.protocols()['awg3']['installed'])

    async def test_slow_build_failure_returns_installer_error_without_saving(self):
        error = 'Docker build failed\nStep 4: compiler exited with status 1'
        response = await self.finish_slow_install({'status': 'error', 'message': error})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json(), {'error': error})
        self.assertEqual(self.protocols(), {})

    async def finish_slow_install(self, result):
        entered, release = self.gate_installer(result)
        request = asyncio.create_task(self.install())
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            completed, _ = await asyncio.wait({request}, timeout=lifecycle.RESPONSE_WAIT_SECONDS * 3)
            self.assertFalse(completed, request.result().text if completed else '')
            self.assertEqual(self.protocols(), {})
        finally:
            release.set()
            async with asyncio.timeout(2):
                await request
        self.manager.install_protocol.assert_called_once()
        return request.result()
