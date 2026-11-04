import asyncio
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from starlette.requests import Request
import app as panel


class DashboardReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def test_barriers_preserve_current_state_and_discard_changed_targets(self):
        for mutation in ('assignment', 'reorder', 'name', 'target', 'delete', 'protocol', 'restore'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                file = Path(tmp)/'data.json'
                original = {'uid': 'one', 'host': '192.0.2.1', 'username': 'root',
                            'name': 'Original', 'protocols': {'awg2': {'installed': True}}}
                file.write_text(json.dumps({'servers': [original, {'uid': 'two', 'name': 'Other'}],
                                            'user_connections': []}))
                entered, release = threading.Event(), threading.Event()

                def observe(data, server):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('test barrier not released')
                    server['protocols']['awg2']['header_protection'] = True
                    return {'connection': 'ok', 'protocols': {'awg2': {'container_running': True}}}, server['protocols']

                with patch.object(panel, 'DATA_FILE', str(file)), \
                     patch.object(panel, 'DATA_LOCK', asyncio.Lock()), \
                     patch.object(panel, '_check_admin', return_value=True), \
                     patch.object(panel, '_collect_dashboard_status', side_effect=observe):
                    task = asyncio.create_task(panel.api_check_server(Request({'type': 'http'}), 0))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    async with panel.DATA_LOCK:
                        with panel.STATE_FILE_LOCK:
                            current = panel.load_data()
                            if mutation == 'assignment':
                                current['user_connections'].append({'id': 'new', 'user_id': 'u'})
                            elif mutation == 'reorder':
                                current['servers'].reverse()
                            elif mutation == 'name':
                                current['servers'][0]['name'] = 'Edited'
                            elif mutation == 'target':
                                current['servers'][0]['password'] = 'changed'
                            elif mutation == 'delete':
                                current['servers'].pop(0)
                            elif mutation == 'protocol':
                                current['servers'][0]['protocols']['awg2']['port'] = '60000'
                            elif mutation == 'restore':
                                current[panel.GENERATION] = 'restored'
                            # Simulate recovery's own atomic writer for generation.
                            file.write_text(json.dumps(current))
                    release.set()
                    result = await task
                    after = panel.load_data()
                    if mutation in ('target', 'delete', 'restore'):
                        self.assertEqual(result.status_code, 409)
                        self.assertEqual(after, current)
                    else:
                        server = next(s for s in after['servers'] if s['uid'] == 'one')
                        expected = copy.deepcopy(current)
                        if mutation == 'protocol':
                            self.assertTrue(result['observation_conflict'])
                            self.assertNotIn('awg2', result['protocols'])
                        else:
                            next(s for s in expected['servers'] if s['uid'] == 'one')['protocols']['awg2']['header_protection'] = True
                            self.assertTrue(server['protocols']['awg2']['header_protection'])
                        self.assertEqual(after, expected)

    async def test_partial_observation_preserves_dynamic_and_exit_records(self):
        for failure in (False, True):
            server = {'uid': 'one', 'protocols': {
                'awg2__2': {'installed': True},
                'awg2': {'installed': True, 'exit_link': {'exit_uid': 'exit'}},
                'xray': {'installed': True}}}
            manager = Mock()
            manager.check_docker_installed.return_value = True
            manager.get_server_status.side_effect = RuntimeError('unavailable') if failure else None
            manager.get_server_status.return_value = {'container_exists': False}
            with patch.object(panel, 'get_ssh', return_value=Mock()), \
                 patch.object(panel, 'get_protocol_manager', return_value=manager), \
                 patch.object(panel.AWGManager, 'prefetch_awg_state'):
                status, candidate = panel._collect_dashboard_status({'servers': [server]}, server)
            self.assertIn('awg2__2', candidate)
            self.assertIn('awg2', candidate)
            if failure:
                self.assertIn('xray', candidate)
                self.assertNotIn('stale', candidate['awg2']['exit_link'])
            else:
                self.assertNotIn('xray', candidate)
                self.assertEqual(candidate['awg2']['exit_link']['stale'], 'entry_container_missing')
