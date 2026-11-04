import subprocess
import unittest
from unittest.mock import Mock

from dashboard_stats import collect_stats
from server_stats import HOST_STATS_COMMANDS
from tests.test_host_stats_batch import batch_output


class DashboardStatsTests(unittest.TestCase):
    def test_one_command_and_field_parity(self):
        ssh = Mock()
        ssh.run_command.side_effect = lambda command, **kw: (batch_output(command), '', 0)
        stats = collect_stats(ssh)
        self.assertEqual(stats['cpu'], 7.3)
        self.assertEqual(stats['ram_percent'], 25.0)
        self.assertEqual(stats['net_rx'], 123456)
        self.assertEqual(stats['ipv6'], ['2001:db8::1', '2001:db8:1::2'])
        self.assertNotIn('errors', stats)
        ssh.run_command.assert_called_once()
        ssh.disconnect.assert_called_once()

    def test_partial_metrics_remain_available(self):
        ssh = Mock()
        ssh.run_command.side_effect = lambda command, **kw: (
            batch_output(command).replace(':end:ram:0', ':end:ram:1'), '', 0)
        stats = collect_stats(ssh)
        self.assertEqual(stats['cpu'], 7.3)
        self.assertEqual(stats['errors'], {'ram': 'probe_failed'})
        ssh.disconnect.assert_called_once()

    def test_transport_error_uses_stale_cache_path_and_disconnects(self):
        ssh = Mock()
        for failure in [('','transport lost', -1), TimeoutError('transport lost')]:
            with self.subTest(failure=failure):
                ssh.reset_mock(side_effect=True)
                if isinstance(failure, Exception):
                    ssh.run_command.side_effect = failure
                else:
                    ssh.run_command.return_value = failure
                with self.assertRaisesRegex(RuntimeError, 'Host statistics unavailable'):
                    collect_stats(ssh)
                ssh.disconnect.assert_called_once()

    def test_shell_ipv6_filter_excludes_ula_and_transit(self):
        script = "ip() { printf '%s\\n' ' inet6 2a01:db8::1/64 scope global' ' inet6 fd00:42::1/64 scope global' ' inet6 2a01:db8::2/126 scope global'; }; "
        script += HOST_STATS_COMMANDS['ipv6']
        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '2a01:db8::1')
