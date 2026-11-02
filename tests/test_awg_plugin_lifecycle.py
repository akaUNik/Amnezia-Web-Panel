"""Background plugin work shares peer admission and owns it through recovery."""
import asyncio
from contextlib import ExitStack
import copy
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import app as panel
import peer_lifecycle_lock as lifecycle
from managers.awg_plugin_updater import PluginUpdateError


class PluginLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(lifecycle, '_fence_path', None))
        self.stack.enter_context(patch.object(lifecycle, '_fenced', False))
        self.stack.enter_context(patch.object(lifecycle, 'RESPONSE_WAIT_SECONDS', 0.02))
        self.stack.enter_context(patch.object(panel, '_awg_plugin_save'))
        self.stack.enter_context(patch.object(panel, '_awg_plugin_ops', {}))
        self.stack.enter_context(patch.object(panel, '_awg_plugin_active', {}))
        self.entered = threading.Event()
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.seen_interlock = False
        self.failure = None
        test = self
        class Updater:
            def update(self, component, target, **kwargs):
                test.seen_interlock = lifecycle._active.get()
                test.entered.set()
                if not test.release.wait(3):
                    raise AssertionError('test release was not signaled')
                if test.failure:
                    raise test.failure
                return {'state': 'updated', 'installed': target}
        self.factory = self.stack.enter_context(patch.object(panel, '_awg_plugin_updater', return_value=Updater()))
        self.op = {'id': 'operation', 'server_uid': 'uid', 'component': 'userspace',
                   'state': 'running', 'lines': [], 'target': '3.1.20260828'}
        panel._awg_plugin_ops['operation'] = self.op
        panel._awg_plugin_active['uid'] = 'operation'

    def start(self):
        return asyncio.create_task(panel._run_awg_plugin_update(
            'operation', 0, 'userspace', '3.1.20260828', True))

    async def wait_entered(self):
        self.assertTrue(await asyncio.to_thread(self.entered.wait, 2))

    async def wait_terminal(self):
        async with asyncio.timeout(2):
            while self.op['state'] == 'running':
                await asyncio.sleep(0.005)

    async def test_plugin_waits_for_existing_peer_mutation(self):
        held, finish = asyncio.Event(), asyncio.Event()
        @lifecycle.serialize_peer_lifecycle
        async def mutate_peer():
            held.set()
            await finish.wait()
        peer = asyncio.create_task(mutate_peer())
        await held.wait()
        update = self.start()
        try:
            await asyncio.sleep(0.01)
            self.factory.assert_not_called()
        finally:
            finish.set()
        await peer
        await self.wait_entered()
        self.assertTrue(self.seen_interlock)
        self.release.set()
        await update
        self.assertEqual(self.op['state'], 'success')

    async def test_http_response_budget_does_not_end_background_ownership(self):
        update = self.start()
        await self.wait_entered()
        changed = asyncio.Event()
        @lifecycle.serialize_peer_lifecycle
        async def mutate_peer():
            changed.set()
        peer = asyncio.create_task(mutate_peer())
        try:
            await asyncio.sleep(0.06)
            self.assertFalse(update.done())
            self.assertEqual(self.op['state'], 'running')
            self.assertEqual(panel._awg_plugin_active, {'uid': 'operation'})
            self.assertFalse(changed.is_set())
        finally:
            self.release.set()
        await update
        # The HTTP-style waiter may time out, but its admitted mutation must
        # still run after the plugin releases its interlock.
        try:
            await peer
        except lifecycle.PeerLifecycleBusy:
            pass
        await asyncio.wait_for(changed.wait(), 2)
        self.assertTrue(self.seen_interlock)

    async def test_outer_cancellation_keeps_guard_until_update_rollback_finishes(self):
        self.failure = PluginUpdateError('update_failed', 'controlled failure after rollback')
        update = self.start()
        await self.wait_entered()
        update.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await update
        self.assertEqual(self.op['state'], 'running')
        self.assertIn('uid', panel._awg_plugin_active)
        # A second lifecycle writer cannot enter while the admitted updater
        # is still finishing rollback, even after its waiter was cancelled.
        with patch.object(lifecycle, 'QUEUE_WAIT_SECONDS', 0.01):
            @lifecycle.serialize_peer_lifecycle
            async def mutate_peer():
                self.fail('entered during update rollback')
            with self.assertRaises(lifecycle.PeerLifecycleBusy):
                await mutate_peer()
        self.release.set()
        await self.wait_terminal()
        self.assertEqual(self.op['error']['code'], 'update_failed')
        self.assertNotIn('uid', panel._awg_plugin_active)

    async def test_admission_timeout_marks_operation_failed_without_starting(self):
        with patch.object(lifecycle, 'QUEUE_WAIT_SECONDS', 0.01):
            async with lifecycle.snapshot_interlock():
                await self.start()
        self.factory.assert_not_called()
        self.assertEqual(self.op['state'], 'failed')
        self.assertEqual(self.op['error']['code'], 'peer_lifecycle_busy')
        self.assertNotIn('uid', panel._awg_plugin_active)

    async def test_cancelled_queued_operation_never_runs_and_releases_active_key(self):
        async with lifecycle.snapshot_interlock():
            update = self.start()
            await asyncio.sleep(0.01)
            update.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await update
        await asyncio.sleep(0)
        self.factory.assert_not_called()
        self.assertEqual(self.op['state'], 'cancelled')
        self.assertNotIn('uid', panel._awg_plugin_active)

    async def test_delayed_error_cannot_remove_a_new_operation_active_key(self):
        async def uncertain(*args):
            panel._awg_plugin_active['uid'] = 'new-operation'
            raise lifecycle.PeerLifecycleUncertain('peer_lifecycle_uncertain')
        with patch.object(panel, '_run_awg_plugin_update_locked', side_effect=uncertain):
            await self.start()
        self.assertEqual(panel._awg_plugin_active['uid'], 'new-operation')

    async def test_uncertainty_fence_prevents_update(self):
        with patch.object(lifecycle, '_fenced', True):
            await self.start()
        self.factory.assert_not_called()
        self.assertEqual(self.op['error']['code'], 'peer_lifecycle_uncertain')
        self.assertNotIn('uid', panel._awg_plugin_active)

    async def test_real_user_revocation_waits_for_rollback_then_updates_restored_peer(self):
        self.failure = PluginUpdateError('update_failed', 'controlled rollback')
        data = {'users': [{'id': 'u', 'enabled': False, 'access_revision': 1,
                           'access_sync_pending': True}],
                'servers': [{'uid': 'uid', 'host': 'server.invalid'}],
                'user_connections': [{'id': 'c', 'user_id': 'u', 'server_id': 0,
                                      'protocol': 'awg3', 'client_id': 'peer'}]}
        remote = {'enabled': True, 'toggles': 0}
        def save(value):
            data.clear()
            data.update(copy.deepcopy(value))
        def manager_call(manager, method, protocol, *args):
            if method == 'get_clients':
                return [{'clientId': 'peer', 'enabled': remote['enabled']}]
            self.assertEqual(method, 'toggle_client')
            remote['enabled'] = args[-1]
            remote['toggles'] += 1
        self.stack.enter_context(patch.object(panel, 'DATA_LOCK', asyncio.Lock()))
        self.stack.enter_context(patch.object(panel, '_USER_STATE_TASKS', {}))
        self.stack.enter_context(patch.object(panel, 'load_data', side_effect=lambda: copy.deepcopy(data)))
        self.stack.enter_context(patch.object(panel, 'save_data', side_effect=save))
        ssh = SimpleNamespace(connect=lambda: None, disconnect=lambda: None)
        self.stack.enter_context(patch.object(panel, 'get_ssh', return_value=ssh))
        self.stack.enter_context(patch.object(panel, 'get_protocol_manager', return_value=object()))
        self.stack.enter_context(patch.object(panel, '_manager_call', side_effect=manager_call))
        update = self.start()
        await self.wait_entered()
        revoke = asyncio.create_task(panel.perform_toggle_user({}, 'u', False))
        try:
            await asyncio.sleep(0.04)
            self.assertFalse(revoke.done())
            self.assertEqual(remote['toggles'], 0)
            self.assertTrue(data['users'][0]['access_sync_pending'])
        finally:
            self.release.set()
        await update
        self.assertTrue(await revoke)
        self.assertFalse(remote['enabled'])
        self.assertEqual(remote['toggles'], 1)
        self.assertFalse(data['users'][0]['access_sync_pending'])

    async def test_busy_user_propagation_remains_pending_without_starting_task(self):
        self.stack.enter_context(patch.object(panel, '_USER_STATE_TASKS', {}))
        with patch.object(lifecycle, 'QUEUE_WAIT_SECONDS', 0.01):
            async with lifecycle.snapshot_interlock():
                self.assertFalse(await panel.perform_toggle_user({}, 'u', False))
        self.assertEqual(panel._USER_STATE_TASKS, {})

    async def test_nested_mass_operation_can_propagate_without_reacquiring(self):
        self.stack.enter_context(patch.object(panel, '_USER_STATE_TASKS', {}))
        async def propagate(user_id):
            self.assertTrue(lifecycle._active.get())
            return True
        self.stack.enter_context(patch.object(panel, '_propagate_user_state', side_effect=propagate))
        @lifecycle.serialize_peer_lifecycle
        async def mass_operation():
            return await panel.perform_toggle_user({}, 'u', False)
        self.assertTrue(await mass_operation())
