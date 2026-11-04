from contextlib import ExitStack
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import app as panel
from tests.test_host_stats_batch import batch_output, VALUES
from dashboard_cache import DisplayCache


class DashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.file = Path(tmp)/'data.json'
        self.server = {'uid': 'one', 'name': 'One', 'host': '192.0.2.1', 'username': 'root',
                       'ssh_port': 22, 'password': 'ssh-secret', 'protocols': {'awg2': {'installed': True}}}
        self.file.write_text(json.dumps({'servers': [self.server]}))
        self.now = 100
        self.cache = DisplayCache(monotonic=lambda: self.now, wall=lambda: self.now + 1000)
        self.stack.enter_context(patch.object(panel, 'DATA_FILE', str(self.file)))
        self.stack.enter_context(patch.object(panel, 'display_cache', self.cache))
        self.stack.enter_context(patch('dashboard_cache.display_cache', self.cache))
        self.admin = self.stack.enter_context(patch.object(panel, '_check_admin', return_value=True))
        self.stack.enter_context(patch.object(panel, 'get_current_user', return_value={'id': 'admin', 'role': 'admin'}))
        self.ssh = Mock()
        self.ssh.run_command.side_effect = lambda command, **kw: (batch_output(command, dict(VALUES, cpu='12.5')), '', 0)
        self.get_ssh = self.stack.enter_context(patch.object(panel, 'get_ssh', return_value=self.ssh))
        self.client = TestClient(panel.app)

    def test_stats_warm_stale_expired_and_authorization(self):
        first = self.client.post('/api/servers/0/stats')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()['cpu'], 12.5)
        self.assertEqual(first.headers['x-dashboard-stale'], '0')
        self.assertEqual(self.client.post('/api/servers/0/stats').json(), first.json())
        self.ssh.run_command.assert_called_once()
        self.now += 15
        self.ssh.run_command.side_effect = RuntimeError('unavailable')
        stale = self.client.post('/api/servers/0/stats')
        self.assertEqual(stale.json(), first.json())
        self.assertEqual(stale.headers['x-dashboard-stale'], '1')
        self.assertEqual(stale.headers['x-dashboard-error'], '1')
        self.assertEqual(stale.headers['x-dashboard-observed-at'], first.headers['x-dashboard-observed-at'])
        self.now += 285
        self.assertEqual(self.client.post('/api/servers/0/stats').status_code, 500)
        self.admin.return_value = False
        count = self.get_ssh.call_count
        self.assertEqual(self.client.post('/api/servers/0/stats').status_code, 403)
        self.assertEqual(self.client.post('/api/servers/0/check').status_code, 403)
        self.assertEqual(self.get_ssh.call_count, count)

    def test_partial_stats_preserve_errors_through_cache(self):
        self.ssh.run_command.side_effect = lambda command, **kw: (
            batch_output(command).replace(':end:ram:0', ':end:ram:1'), '', 0)
        response = self.client.post('/api/servers/0/stats')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['cpu'], 7.3)
        self.assertEqual(response.json()['errors'], {'ram': 'probe_failed'})
        self.assertEqual(response.headers['x-dashboard-stale'], '0')
        self.assertEqual(self.client.post('/api/servers/0/stats').json(), response.json())
        self.ssh.run_command.assert_called_once()
        self.ssh.disconnect.assert_called_once()

    def test_status_html_is_safe_and_manual_check_always_live(self):
        status = {'connection': 'ok', 'docker_installed': True, 'protocols': {'awg2': {
            'container_running': True, 'container_exists': True, 'private_key': 'vpn-secret',
            'exit_link': {'exit_name': 'Exit', 'psk': 'exit-secret'}}}}
        with patch.object(panel, '_collect_dashboard_status',
                          side_effect=lambda data, server: (copy.deepcopy(status), server['protocols'])) as collect:
            cold = self.client.get('/server/0').text
            self.assertIn('const INITIAL_STATUS = null;', cold)
            self.assertEqual(collect.call_count, 0)
            self.assertEqual(self.client.post('/api/servers/0/check').status_code, 200)
            html = self.client.get('/server/0').text
            snapshot = json.loads(re.search(r'const INITIAL_STATUS = (.*?);', html).group(1))
            self.assertFalse(snapshot['metadata']['stale'])
            self.assertNotIn('secret', json.dumps(snapshot))
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(self.client.post('/api/servers/0/check').status_code, 200)
            self.assertEqual(collect.call_count, 2)
            self.now += 31
            html = self.client.get('/server/0').text
            snapshot = json.loads(re.search(r'const INITIAL_STATUS = (.*?);', html).group(1))
            self.assertTrue(snapshot['metadata']['stale'])
            self.get_ssh.assert_not_called()
        self.assertEqual(self.client.post('/api/servers/0/check?server_uid=wrong').status_code, 409)
        self.assertEqual(self.client.post('/api/servers/0/stats?server_uid=wrong').status_code, 409)

    def test_container_mutation_invalidates_without_local_record_changes(self):
        self.client.post('/api/servers/0/stats')
        self.ssh.run_sudo_command.return_value = 'true', '', 0
        self.assertEqual(self.client.post('/api/servers/0/container/toggle',
                                         json={'protocol': 'awg2'}).status_code, 200)
        self.client.post('/api/servers/0/stats')
        self.assertEqual(self.ssh.run_command.call_count, 2)
