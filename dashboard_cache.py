"""Bounded, process-local display observations; never an authority for actions."""
import asyncio
from collections import OrderedDict
from concurrent.futures import Future
import copy
from dataclasses import dataclass
import hashlib
import json
import threading
import time

from dashboard_observation import TARGET_FIELDS
from peer_recovery import GENERATION


TTL = {'stats': 15, 'status': 30}
STAT_FIELDS = frozenset(('cpu', 'ram_used', 'ram_total', 'ram_percent', 'disk_used',
    'disk_total', 'disk_percent', 'net_rx', 'net_tx', 'uptime', 'ipv6'))
PROTOCOL_FIELDS = frozenset(('protocol', 'base_protocol', 'instance', 'display_name',
    'container_name', 'container_exists', 'container_running', 'installed', 'port',
    'header_protection', 'status_preserved', 'clients_count', 'peers_count', 'mode',
    'internal_ip', 'web_port', 'web_exposed', 'expose_web', 'domain', 'site_url',
    'subnet', 'obfuscation'))


def projection(kind, value):
    if kind == 'stats':
        return {key: copy.deepcopy(value[key]) for key in STAT_FIELDS if key in value}
    protocols = {}
    for proto, info in value.get('protocols', {}).items():
        item = {key: copy.deepcopy(info[key]) for key in PROTOCOL_FIELDS if key in info}
        # Only display labels/state, never private keys, passwords, PSKs, configs
        # or unbounded remote error strings, including nested exit metadata.
        if info.get('exit_link'):
            item['exit_link'] = {key: info['exit_link'][key]
                for key in ('exit_uid', 'exit_name', 'stale') if key in info['exit_link']}
        if info.get('exit_link_status'):
            item['exit_link_status'] = {'up': bool(info['exit_link_status'].get('up'))}
        if info.get('exit_failover'):
            failover = info['exit_failover']
            item['exit_failover'] = {'enabled': bool(failover.get('enabled')),
                **{key: {'name': (failover.get(key) or {}).get('name', '')}
                   for key in ('primary', 'active')}}
        protocols[proto] = item
    return {'connection': value.get('connection'),
            'docker_installed': bool(value.get('docker_installed')), 'protocols': protocols}


def observation_key(namespace, data, server):
    identity = {key: server.get(key) for key in TARGET_FIELDS}
    identity['generation'] = data.get(GENERATION)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return str(namespace), server.get('uid'), digest


class ObservationInvalidated(RuntimeError):
    pass


@dataclass
class Entry:
    data: object = None
    observed_at: float = 0
    monotonic_at: float = 0
    future: object = None
    error: bool = False


class DisplayCache:
    def __init__(self, max_entries=128, max_age=300, max_bytes=65536,
                 monotonic=time.monotonic, wall=time.time):
        self.max_entries, self.max_age, self.max_bytes = max_entries, max_age, max_bytes
        self.monotonic, self.wall = monotonic, wall
        self._entries = OrderedDict()
        self._lock = threading.RLock()
        self._tasks = set()

    def _view(self, kind, entry):
        age = max(0, self.monotonic() - entry.monotonic_at)
        if entry.data is None or age >= self.max_age:
            return None
        return {'data': copy.deepcopy(entry.data), 'metadata': {
            'observed_at': entry.observed_at, 'age_seconds': age,
            'stale': age >= TTL[kind], 'refreshing': entry.future is not None,
            'error': entry.error, 'ttl': TTL[kind]}}

    def peek(self, kind, key):
        with self._lock:
            entry = self._entries.get((kind, *key))
            return self._view(kind, entry) if entry else None

    def invalidate(self, namespace=None, uid=None):
        with self._lock:
            for key in list(self._entries):
                if (namespace is None or key[1] == str(namespace)) and (uid is None or key[2] == uid):
                    del self._entries[key]

    async def observe(self, kind, key, collector, force=False):
        cache_key = (kind, *key)
        with self._lock:
            # Discard expired idle entries and previous target identities.
            for old_key, old_entry in list(self._entries.items()):
                if ((old_key[1:3] == cache_key[1:3] and old_key[3] != cache_key[3]) or
                        (old_entry.future is None and self._view(old_key[0], old_entry) is None)):
                    del self._entries[old_key]
            entry = self._entries.get(cache_key)
            if entry is None:
                if len(self._entries) >= self.max_entries:
                    idle = next((k for k, e in self._entries.items() if e.future is None), None)
                    if idle is None:
                        raise RuntimeError('Dashboard refresh capacity reached')
                    del self._entries[idle]
                entry = self._entries[cache_key] = Entry()
            self._entries.move_to_end(cache_key)
            view = self._view(kind, entry)
            if view and not view['metadata']['stale'] and not force:
                return view['data'], view['metadata']
            if entry.future is None:
                entry.future = Future()
                task = asyncio.create_task(self._refresh(kind, cache_key, entry, collector))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            future = entry.future
        # Cancellation of one HTTP waiter never cancels shared collection.
        waiter = asyncio.wrap_future(future)
        waiter.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        return await asyncio.shield(waiter)

    async def _refresh(self, kind, key, entry, collector):
        future = entry.future
        try:
            value = await collector()
            safe = projection(kind, value)
            if len(json.dumps(safe).encode()) > self.max_bytes:
                raise RuntimeError('Dashboard snapshot too large')
            with self._lock:
                if self._entries.get(key) is not entry:
                    raise ObservationInvalidated('Server changed during observation')
                entry.data = safe
                entry.observed_at, entry.monotonic_at = self.wall(), self.monotonic()
                entry.error, entry.future = False, None
                metadata = self._view(kind, entry)['metadata']
            future.set_result((value, metadata))
        except BaseException as error:
            with self._lock:
                entry.future = None
                entry.error = True
            future.set_exception(error)


display_cache = DisplayCache()
