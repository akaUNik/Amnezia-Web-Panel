import asyncio
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from fastapi.responses import Response
from starlette.requests import Request
import app as panel
from tests.test_host_stats_batch import batch_output, VALUES
from dashboard_cache import DisplayCache, observation_key


class DashboardInvalidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delayed_stats_cannot_republish_after_configuration_changes(self):
        for mutation in ('credentials', 'delete', 'protocol', 'restore', 'explicit_reset'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                file = Path(tmp)/'data.json'
                server = {'uid': 'one', 'host': '192.0.2.1', 'username': 'root',
                          'protocols': {'awg2': {'installed': True}}}
                file.write_text(json.dumps({'servers': [server]}))
                entered, release = threading.Event(), threading.Event()
                cache = DisplayCache()
                ssh = Mock()
                def command(command, **kwargs):
                    entered.set()
                    if not release.wait(3):
                        raise AssertionError('test barrier not released')
                    return batch_output(command, dict(VALUES, cpu='12.5')), '', 0
                ssh.run_command.side_effect = command
                with patch.object(panel, 'DATA_FILE', str(file)), \
                     patch.object(panel, 'DATA_LOCK', asyncio.Lock()), \
                     patch.object(panel, '_check_admin', return_value=True), \
                     patch.object(panel, 'display_cache', cache), \
                     patch('dashboard_cache.display_cache', cache), \
                     patch.object(panel, 'get_ssh', return_value=ssh):
                    key = observation_key(file, panel.load_data(), server)
                    task = asyncio.create_task(panel.api_server_stats(Request({'type': 'http'}), 0, Response()))
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    async with panel.DATA_LOCK:
                        state = panel.load_data()
                        if mutation == 'credentials':
                            state['servers'][0]['password'] = 'new'
                        elif mutation == 'delete':
                            state['servers'] = []
                        elif mutation == 'protocol':
                            state['servers'][0]['protocols']['awg2']['port'] = '60000'
                        elif mutation == 'restore':
                            state[panel.GENERATION] = 'new-generation'
                            file.write_text(json.dumps(state))
                        elif mutation == 'explicit_reset':
                            cache.begin_mutation(file, 'one')
                            cache.end_mutation(file, 'one')
                        if mutation not in ('restore', 'explicit_reset'):
                            panel.save_data(state)
                    release.set()
                    response = await task
                    self.assertEqual(response.status_code, 409)
                    self.assertIsNone(cache.peek('stats', key))
                    if state['servers']:
                        fresh_key = observation_key(file, state, state['servers'][0])
                        self.assertIsNone(cache.peek('stats', fresh_key))

    async def test_active_mutation_blocks_display_until_every_writer_finishes(self):
        cache = DisplayCache()
        key = ('state', 'uid', 'identity')
        async def collect():
            return {'cpu': 1}
        await cache.observe('stats', key, collect)
        cache.begin_mutation('state', 'uid')
        cache.begin_mutation('state', 'uid')
        cache.end_mutation('state', 'uid')
        self.assertIsNone(cache.peek('stats', key))
        with self.assertRaisesRegex(RuntimeError, 'change in progress'):
            await cache.observe('stats', key, collect)
        cache.end_mutation('state', 'uid')
        self.assertEqual((await cache.observe('stats', key, collect))[0], {'cpu': 1})
