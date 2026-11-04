import asyncio
import json
import unittest

from dashboard_cache import DisplayCache, ObservationInvalidated, observation_key, projection


class DisplayCacheTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 100
        self.cache = DisplayCache(max_entries=2, monotonic=lambda: self.now, wall=lambda: self.now + 1000)
        self.key = ('state', 'uid', 'target')
        self.calls = 0

    async def collect(self):
        self.calls += 1
        return {'cpu': self.calls}

    async def test_ttl_age_failure_does_not_extend_last_success(self):
        await self.cache.observe('stats', self.key, self.collect)
        self.now += 14
        self.assertEqual((await self.cache.observe('stats', self.key, self.collect))[0]['cpu'], 1)
        self.now += 1
        self.assertTrue(self.cache.peek('stats', self.key)['metadata']['stale'])
        async def fail():
            raise RuntimeError('private failure')
        with self.assertRaises(RuntimeError):
            await self.cache.observe('stats', self.key, fail)
        view = self.cache.peek('stats', self.key)
        self.assertTrue(view['metadata']['error'])
        self.assertEqual(view['metadata']['observed_at'], 1100)
        self.now = 400
        self.assertIsNone(self.cache.peek('stats', self.key))
        self.assertEqual(self.calls, 1)

    async def test_single_flight_cancelled_client_and_server_separation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
            return await self.collect()
        first = asyncio.create_task(self.cache.observe('stats', self.key, slow))
        await entered.wait()
        second = asyncio.create_task(self.cache.observe('stats', self.key, slow))
        await asyncio.sleep(0)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        other = ('state', 'other', 'target')
        await self.cache.observe('stats', other, self.collect)
        release.set()
        self.assertEqual((await second)[0]['cpu'], 2)
        self.assertEqual(self.cache.peek('stats', other)['data']['cpu'], 1)
        self.assertEqual(self.calls, 2)

    async def test_status_ttl_projection_and_size_bound(self):
        async def status():
            return {'connection': 'ok', 'password': 'secret', 'protocols': {'awg2': {
                'container_running': True, 'private_key': 'secret', 'awg_params': {'key': 'secret'},
                'exit_link': {'psk': 'secret', 'exit_name': 'Exit'}}}}
        await self.cache.observe('status', self.key, status)
        self.now += 29
        view = self.cache.peek('status', self.key)
        self.assertFalse(view['metadata']['stale'])
        self.assertNotIn('secret', json.dumps(view))
        self.now += 1
        self.assertTrue(self.cache.peek('status', self.key)['metadata']['stale'])
        await self.cache.observe('stats', ('state', 'second', 'target'), self.collect)
        await self.cache.observe('stats', ('state', 'third', 'target'), self.collect)
        self.assertEqual(len(self.cache._entries), 2)
        self.assertIsNone(self.cache.peek('status', self.key))
        async def large():
            return {'uptime': 'a' * 70000}
        with self.assertRaisesRegex(RuntimeError, 'too large'):
            await self.cache.observe('stats', self.key, large)

    async def test_invalidation_discards_late_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
            return {'cpu': 1}
        first = asyncio.create_task(self.cache.observe('stats', self.key, slow))
        await entered.wait()
        self.cache.invalidate('state', 'uid')
        await self.cache.observe('stats', self.key, self.collect)
        release.set()
        with self.assertRaises(ObservationInvalidated):
            await first
        self.assertEqual(self.cache.peek('stats', self.key)['data']['cpu'], 1)

    async def test_refresh_capacity_stays_bounded_across_invalidations(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow():
            entered.set()
            await release.wait()
            return {'cpu': 1}
        first = asyncio.create_task(self.cache.observe('stats', self.key, slow))
        await entered.wait()
        self.cache.invalidate()
        entered.clear()
        second = asyncio.create_task(self.cache.observe('stats', self.key, slow))
        await entered.wait()
        self.cache.invalidate()
        try:
            with self.assertRaisesRegex(RuntimeError, 'capacity'):
                await self.cache.observe('stats', self.key, slow)
        finally:
            release.set()
            for task in (first, second):
                with self.assertRaises(ObservationInvalidated):
                    await task

    def test_identity_contains_no_raw_credentials(self):
        key = observation_key('state', {}, {'uid': 'uid', 'password': 'secret'})
        self.assertNotIn('secret', str(key))

    def test_stats_projection_keeps_only_bounded_error_codes(self):
        result = projection('stats', {'cpu': 0, 'errors': {
            'cpu': 'unavailable', 'ram': 'private remote output',
            'password': 'unavailable'}})
        self.assertEqual(result, {'cpu': 0, 'errors': {'cpu': 'unavailable'}})
