import threading
import time
import unittest
from unittest.mock import Mock, patch

from managers.ssh_manager import SSHManager
try:
    from .ssh_channel_fixture import Channel
except ImportError:
    from ssh_channel_fixture import Channel


def manager(channel):
    ssh = SSHManager('192.0.2.1', 22, 'root')
    ssh.client = Mock()
    ssh.client.get_transport.return_value.open_session.return_value = channel
    return ssh


class CommandBudgetTests(unittest.TestCase):
    def test_drains_both_windows_before_status_and_stdin(self):
        channel = Channel(b'a' * 150000, b'b' * 150000, stdin=True)
        ssh = manager(channel)
        out, err, code = ssh.run_command('fake write', timeout=1, stdin_input='secret\n' * 100)
        self.assertEqual((len(out), len(err), code), (150000, 150000, 0))
        self.assertEqual(channel.input, ('secret\n' * 100).encode())
        self.assertTrue(channel.input_closed)
        self.assertTrue(channel.closed)

    def test_ack_status_and_stdin_timeouts_release_locks(self):
        for mode in ('ack', 'status', 'stdin'):
            with self.subTest(mode=mode):
                channel = Channel(ack=mode != 'ack', status=mode != 'status', stdin=mode == 'stdin')
                if mode == 'stdin':
                    channel.send_ready = lambda: False
                ssh = manager(channel)
                start = time.monotonic()
                out, err, code = ssh.run_command('fake mutation', timeout=.05, stdin_input='input')
                self.assertEqual((out, code), ('', -1))
                self.assertTrue(err)
                self.assertLess(time.monotonic() - start, .5)
                self.assertTrue(channel.closed)
                self.assertEqual(len(channel.commands), 1)
                self.assertFalse(any(t.name == 'ssh-command-deadline' for t in threading.enumerate()))
                # A different thread can acquire both locks and complete.
                ssh.client.get_transport.return_value.open_session.return_value = Channel()
                result = []
                worker = threading.Thread(target=lambda: result.append(ssh.run_command('next', timeout=.1)))
                worker.start()
                worker.join(.5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result[0], ('ok', '', 0))

    def test_open_session_timeout_and_retry_share_budget(self):
        ssh = manager(Channel())
        budgets = []
        def open_session(timeout):
            budgets.append(timeout)
            time.sleep(.02)
            if len(budgets) == 1:
                raise EOFError('before exec')
            return Channel()
        transport = ssh.client.get_transport.return_value
        transport.open_session.side_effect = open_session
        with patch.object(ssh, '_disconnect_locked'), patch.object(ssh, 'connect') as connect:
            self.assertEqual(ssh.run_command('safe retry', timeout=.2), ('ok', '', 0))
        self.assertEqual(connect.call_count, 1)
        self.assertLess(budgets[1], budgets[0])

    def test_exec_failure_is_never_replayed(self):
        channel = Channel()
        channel.exec_command = Mock(side_effect=EOFError('reply lost'))
        ssh = manager(channel)
        with patch.object(ssh, 'connect') as connect:
            self.assertEqual(ssh.run_command('remote mutation')[2], -1)
        channel.exec_command.assert_called_once()
        connect.assert_not_called()

    def test_deadline_also_bounds_channel_close_during_rekey(self):
        channel = Channel()
        ssh = manager(channel)
        retired = threading.Event()
        ssh.client.get_transport.return_value.close.side_effect = retired.set
        def close():
            if not retired.wait(.5):
                raise AssertionError('channel cleanup was not interrupted')
            channel.closed = True
        channel.close = close
        start = time.monotonic()
        self.assertEqual(ssh.run_command('true', timeout=.05), ('ok', '', 0))
        self.assertLess(time.monotonic() - start, .5)
        self.assertTrue(retired.is_set())
