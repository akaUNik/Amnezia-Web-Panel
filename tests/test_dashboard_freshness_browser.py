import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app as panel


class DashboardFreshnessBrowserTests(unittest.TestCase):
    def test_warm_status_expiry_manual_check_and_refresh_errors(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            options = {'headless': True}
            if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
                options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
            browser = pw.chromium.launch(**options)
            file = Path(tmp)/'data.json'
            file.write_text(json.dumps({'servers': [{'uid': 'fixture', 'name': 'Test', 'host': '192.0.2.1',
                'username': 'root', 'ssh_port': 22, 'protocols': {'awg2': {'installed': True}}}]}))
            admin = {'id': 'admin', 'role': 'admin'}
            status = {'connection': 'ok', 'docker_installed': True,
                'protocols': {'awg2': {'container_running': True, 'container_exists': True}}}
            with patch.object(panel, 'DATA_FILE', str(file)), \
                 patch.object(panel, 'get_current_user', return_value=admin), \
                 patch.object(panel, 'get_ssh', side_effect=AssertionError('Real SSH forbidden')):
                client = TestClient(panel.app)
                for stale in (False, True):
                    timestamp = time.time() - (40 if stale else 0)
                    snapshot = {'data': status, 'metadata': {'observed_at': timestamp,
                        'age_seconds': 40 if stale else 0, 'ttl': 30, 'stale': stale,
                        'refreshing': False, 'error': False}}
                    with patch.object(panel.display_cache, 'peek', return_value=snapshot):
                        html = client.get('/server/0').text
                    page = browser.new_page()
                    page.clock.install()
                    held, seen, errors = {}, [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route_request(route):
                        path = urlsplit(route.request.url).path
                        seen.append(path)
                        if path == '/server/0':
                            route.fulfill(content_type='text/html', body=html)
                        elif path.endswith('/check'):
                            held['check'] = route
                        elif path.endswith('/stats'):
                            held['stats'] = route
                        elif path.endswith('/connections'):
                            route.fulfill(json={'clients': []})
                        elif path.startswith('/static/'):
                            res = client.get(path)
                            route.fulfill(body=res.content, content_type=res.headers.get('content-type'))
                        elif route.request.url.startswith('http://panel.test/'):
                            route.fulfill(json={})
                        else:
                            route.abort()

                    def wait_request(name):
                        for _ in range(100):
                            if name in held:
                                return held.pop(name)
                            page.wait_for_timeout(10)
                        self.fail('Request did not arrive: ' + name)

                    page.route('**/*', route_request)
                    page.goto('http://panel.test/server/0')
                    stats_request = wait_request('stats')
                    self.assertEqual(page.locator('#statsFreshness').get_attribute('data-state'), 'refreshing')
                    self.assertTrue(page.locator('#proto-awg2').is_visible())
                    self.assertEqual(sum(p.endswith('/check') for p in seen), int(stale))
                    stats_request.fulfill(json={'cpu': 10}, headers={
                        'X-Dashboard-Observed-At': str(timestamp), 'X-Dashboard-Age-Seconds': '40',
                        'X-Dashboard-Ttl': '15', 'X-Dashboard-Stale': '1', 'X-Dashboard-Error': '1'})
                    page.wait_for_function("document.getElementById('statsFreshness').dataset.state === 'error'")
                    self.assertIn('Stale', page.locator('#statsFreshness').inner_text())
                    if not stale:
                        # Warm HTML skipped /check; expiry starts a live refresh.
                        page.clock.fast_forward(31000)
                    check = wait_request('check')
                    self.assertTrue(page.locator('#proto-awg2').is_visible())
                    check.fulfill(json=status, headers={'X-Dashboard-Observed-At': str(time.time()),
                        'X-Dashboard-Age-Seconds': '0', 'X-Dashboard-Ttl': '30', 'X-Dashboard-Stale': '0'})
                    page.wait_for_function("!document.getElementById('checkBtn').disabled")
                    self.assertEqual(page.locator('#statusFreshness').get_attribute('data-state'), 'fresh')
                    page.locator('#checkBtn').click()
                    wait_request('check').fulfill(status=500, json={'error': 'Synthetic failure'})
                    page.wait_for_function("document.getElementById('statusFreshness').dataset.state === 'error'")
                    # Five-minute retention also applies to already-open pages.
                    page.clock.fast_forward(301000)
                    self.assertNotIn('Observed', page.locator('#statsFreshness').inner_text())
                    self.assertEqual(page.locator('#serverStats .stat-pill').count(), 0)
                    self.assertTrue(page.locator('#proto-awg2').is_hidden())
                    for route in held.values():
                        route.fulfill(status=500, json={'error': 'Synthetic failure'})
                    self.assertEqual(errors, [])
                    page.close()
            browser.close()
