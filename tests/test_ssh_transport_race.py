"""force_disconnect() must not tear down a transport mid-command.

Before run_command held _conn_lock for the whole exec, pool eviction could
null self.client between ensure_connected() and exec_command(), surfacing as
'NoneType' object has no attribute 'open_session'. This test blocks the fake
exec until another thread calls force_disconnect(); with the fix the eviction
waits for the in-flight command and the command succeeds.
"""
import threading
import unittest
from unittest import mock

from managers.ssh_manager import SSHManager


try:
    from .ssh_channel_fixture import Channel
except ImportError:
    from ssh_channel_fixture import Channel


class _FakeClient:
    def __init__(self, started, release):
        self._started = started
        self._release = release
        self.closed = False

    def set_missing_host_key_policy(self, policy):
        pass

    def load_system_host_keys(self):
        pass

    def load_host_keys(self, filename):
        pass

    def connect(self, **kwargs):
        pass

    def get_transport(self):
        transport = mock.Mock()
        transport.is_active.return_value = True
        transport.open_session.side_effect = self.open_session
        return transport

    def open_session(self, timeout=60):
        channel = Channel()
        def execute(command):
            self._started.set()
            self.assert_closed_false()
            self._release.wait(5)
            self.assert_closed_false()
        channel.exec_command = execute
        return channel

    def assert_closed_false(self):
        if self.closed:
            raise AttributeError("'NoneType' object has no attribute 'open_session'")

    def close(self):
        self.closed = True


class TransportRaceTest(unittest.TestCase):
    def test_force_disconnect_waits_for_inflight_command(self):
        started = threading.Event()
        release = threading.Event()
        clients = []

        def factory():
            client = _FakeClient(started, release)
            clients.append(client)
            return client

        ssh = SSHManager('203.0.113.1', 22, 'root', password='x')
        with mock.patch('paramiko.SSHClient', side_effect=factory):
            result = {}
            worker = threading.Thread(
                target=lambda: result.setdefault(
                    'res', ssh.run_command('echo ok', timeout=30)))
            worker.start()
            self.assertTrue(started.wait(5))
            evictor = threading.Thread(target=ssh.force_disconnect)
            evictor.start()
            # The eviction must block while the command holds _conn_lock.
            evictor.join(timeout=1)
            self.assertTrue(evictor.is_alive(),
                            'force_disconnect ran during an in-flight command')
            release.set()
            worker.join(timeout=10)
            evictor.join(timeout=10)
            self.assertFalse(worker.is_alive())

        out, err, code = result['res']
        self.assertEqual(code, 0, err)
        self.assertEqual(out, 'ok')
        self.assertTrue(clients[0].closed)  # evicted after the command ended


if __name__ == '__main__':
    unittest.main()
