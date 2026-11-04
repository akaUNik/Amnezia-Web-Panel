"""The sudo password must never reach the log or the remote command line.

Previously run_sudo_command/run_sudo_script wrapped every command as
`echo '<password>' | sudo -S -p '' <cmd>`, and run_command logged the first
100 chars of that string on every call -- so the panel journal held the sudo
password in clear text, and `ps` on the managed host showed it for the life
of the command. The password now travels through the channel's stdin only.
"""

import logging
import threading
import unittest
from unittest import mock

from managers.ssh_manager import SSHManager

PASSWORD = "S3cr3t Pa'ss w0rd"


try:
    from .ssh_channel_fixture import Channel
except ImportError:
    from ssh_channel_fixture import Channel


def make_ssh():
    ssh = SSHManager.__new__(SSHManager)
    ssh._exec_lock = threading.RLock()
    ssh._is_root = False
    ssh.password = PASSWORD
    ssh.ensure_connected = lambda: None
    ssh.upload_file = lambda content, path: None
    ssh.client = mock.Mock()
    return ssh


class SudoPasswordLeakTests(unittest.TestCase):
    def run_and_capture(self, fn):
        ssh = make_ssh()
        channels = []
        def open_session(**kwargs):
            channel = Channel(output=b'')
            def execute(command):
                channel.commands.append(command)
                if command.startswith('mktemp -d '):
                    channel.output.extend(b'/tmp/amnezia-private.ABC123def0\n')
            channel.exec_command = execute
            channels.append(channel)
            return channel
        ssh.client.get_transport.return_value.open_session.side_effect = open_session

        records = []

        class Handler(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Handler()
        logger = logging.getLogger('managers.ssh_manager')
        logger.addHandler(handler)
        try:
            fn(ssh)
        finally:
            logger.removeHandler(handler)
        return ssh, channels, records

    def assert_password_hidden(self, channels, records):
        commands = [command for channel in channels for command in channel.commands]
        self.assertTrue(all(PASSWORD not in command for command in commands))
        self.assertTrue(all(PASSWORD not in record for record in records))
        self.assertEqual([bytes(channel.input) for channel in channels if channel.input],
                         [(PASSWORD + '\n').encode()])
        self.assertTrue(all(channel.input_closed for channel in channels if channel.input))

    def test_sudo_command(self):
        ssh, channels, records = self.run_and_capture(lambda s: s.run_sudo_command('docker ps'))
        self.assert_password_hidden(channels, records)
        self.assertEqual(channels[0].commands, ["sudo -S -p '' docker ps"])

    def test_sudo_script(self):
        ssh, channels, records = self.run_and_capture(lambda s: s.run_sudo_script('echo hi'))
        self.assert_password_hidden(channels, records)
        commands = [command for channel in channels for command in channel.commands]
        self.assertTrue(any(cmd.startswith("sudo -S -p '' bash /tmp/") for cmd in commands))
        self.assertTrue(commands[-1].startswith('rm -rf -- /tmp/amnezia-private.'))

    def test_root_login_needs_no_stdin(self):
        ssh = make_ssh()
        ssh._is_root = True
        channel = Channel()
        ssh.client.get_transport.return_value.open_session.return_value = channel
        self.assertEqual(ssh.run_sudo_command('docker ps'), ('ok', '', 0))
        self.assertEqual(channel.commands, ['docker ps'])
        self.assertEqual(channel.input, b'')


if __name__ == '__main__':
    unittest.main()
