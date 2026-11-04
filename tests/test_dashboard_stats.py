import subprocess
import unittest
from unittest.mock import Mock

from dashboard_stats import STATS_COMMAND, collect_stats, parse_stats


class DashboardStatsTests(unittest.TestCase):
    def test_one_command_and_field_parity(self):
        ssh = Mock()
        ssh.run_command.return_value = ('__AWP_CPU__\n12.56\n__AWP_RAM__\n25 100\n'
            '__AWP_DISK__\n50 200\n__AWP_NET__\n123 456\n__AWP_UPTIME__\nup 2 days\n'
            '__AWP_IPV6__\n2001:db8::1\n', '', 0)
        self.assertEqual(collect_stats(ssh), {'cpu': 12.6, 'ram_used': 25, 'ram_total': 100,
            'ram_percent': 25.0, 'disk_used': 50, 'disk_total': 200, 'disk_percent': 25.0,
            'net_rx': 123, 'net_tx': 456, 'uptime': 'up 2 days', 'ipv6': ['2001:db8::1']})
        ssh.run_command.assert_called_once_with(STATS_COMMAND)
        ssh.disconnect.assert_called_once()

    def test_partial_metrics_and_transport_error(self):
        stats = parse_stats('__AWP_CPU__\ninvalid\n__AWP_RAM__\nmissing\n__AWP_NET__\n1 2\n')
        self.assertEqual((stats['cpu'], stats['ram_total'], stats['disk_total']), (0, 0, 0))
        self.assertEqual((stats['net_rx'], stats['net_tx'], stats['uptime'], stats['ipv6']), (1, 2, '', []))
        ssh = Mock()
        ssh.run_command.return_value = '', 'transport lost', -1
        with self.assertRaisesRegex(RuntimeError, 'transport lost'):
            collect_stats(ssh)
        ssh.disconnect.assert_called_once()

    def test_shell_ipv6_filter_excludes_ula_and_transit(self):
        # Exercise the actual awk expression with synthetic ip output.
        script = "ip() { printf '%s\\n' ' inet6 2a01:db8::1/64 scope global' ' inet6 fd00:42::1/64 scope global' ' inet6 2a01:db8::2/126 scope global'; }; "
        script += STATS_COMMAND.split("printf '\\n__AWP_IPV6__\\n'", 1)[1]
        result = subprocess.run(['sh', '-c', script], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), '2a01:db8::1')
