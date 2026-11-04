import unittest
from unittest.mock import Mock

from managers.awg_manager import AWGManager


class CounterSnapshotTests(unittest.TestCase):
    def test_empty_and_valid_snapshots_never_scan_conntrack(self):
        for payload, expected in [('{}', 0), ('{"10.8.1.2": 7}', 7)]:
            with self.subTest(payload=payload):
                manager = self.manager(payload)
                clients = manager.get_clients('awg2')
                self.assertEqual(clients[0]['userData']['connCount'], expected)
                manager._count_connections_by_ip.assert_not_called()

    def test_missing_and_invalid_snapshots_use_fallback(self):
        for payload, code in [('', 1), ('', 0), ('{', 0), ('[]', 0),
                              ('{"ip": -1}', 0), ('{"ip": null}', 0),
                              ('{"ip": true}', 0), ('{"ip": 1.2}', 0)]:
            with self.subTest(payload=payload):
                manager = self.manager(payload, code)
                self.assertEqual(manager.get_clients('awg2')[0]['userData']['connCount'], 3)
                manager._count_connections_by_ip.assert_called_once_with('awg2')

    def manager(self, payload, code=0):
        ssh = Mock()
        ssh.run_sudo_command.return_value = payload, '', code
        manager = AWGManager(ssh)
        manager._get_clients_table = Mock(return_value=[{'clientId': 'peer', 'userData': {'clientIp': '10.8.1.2'}}])
        manager._wg_show = Mock(return_value={})
        manager._parse_peers_from_config = Mock(return_value={})
        manager._load_conn_warnings = Mock(return_value={})
        manager._count_connections_by_ip = Mock(return_value={'10.8.1.2': 3})
        return manager
