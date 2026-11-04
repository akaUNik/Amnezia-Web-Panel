"""Real rendered page; held API responses, no network or SSH."""
from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
import app as panel


class DashboardLoadingBrowserTests(unittest.TestCase):
    def test_loading_selection_polling_and_editor(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
            options = {'headless': True}
            if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
                options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
            browser = pw.chromium.launch(**options)
            admin = {'id': 'fixture', 'role': 'admin'}
            datafile = Path(tmp)/'data.json'
            with patch.object(panel, 'DATA_FILE', str(datafile)), \
                 patch.object(panel, 'get_current_user', return_value=admin), \
                 patch.object(panel, '_check_admin', return_value=admin), \
                 patch.object(panel, 'get_ssh', side_effect=AssertionError('Real SSH forbidden')):
                client = TestClient(panel.app)
                for saved in (True, False):
                    datafile.write_text(json.dumps({'servers': [{'uid': 'fixture', 'name': 'Test',
                        'host': '192.0.2.1', 'ssh_port': 22, 'username': 'root',
                        'protocols': {'awg2': {'installed': True}, 'awg2__2': {'installed': True}} if saved else {}}]}))
                    html = client.get('/server/0').text
                    page = browser.new_page()
                    held, connections, errors = {}, [], []
                    smart_calls = [0]
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route_request(route):
                        parsed = urlsplit(route.request.url)
                        path = parsed.path
                        if path == '/server/0':
                            route.fulfill(content_type='text/html', body=html)
                        elif path.endswith('/check'):
                            held['check'] = route
                        elif path.endswith('/smartroute/status'):
                            smart_calls[0] += 1
                            if smart_calls[0] == 1:
                                held['status'] = route
                            else:
                                route.fulfill(json={'installed': True, 'enabled': True})
                        elif path.endswith('/connections'):
                            connections.append((parse_qs(parsed.query)['protocol'][0], route))
                        elif path.startswith('/static/'):
                            res = client.get(path)
                            route.fulfill(body=res.content, content_type=res.headers.get('content-type'))
                        elif parsed.netloc == 'panel.test':
                            route.fulfill(json={})
                        else:
                            route.abort()

                    def pump_until(predicate):
                        for _ in range(100):
                            if predicate():
                                return
                            page.wait_for_timeout(20)
                        self.fail('Expected browser request did not arrive')

                    def answer_connection(name='First peer'):
                        proto, route = connections.pop(0)
                        route.fulfill(json={'clients': [{'clientId': 'peer',
                            'userData': {'clientName': name, 'clientIp': '10.8.1.2'}}]})
                        return proto

                    def answer_check(protocols):
                        held.pop('check').fulfill(json={'connection': 'ok', 'docker_installed': True,
                            'protocols': protocols})
                        page.wait_for_function("!document.getElementById('checkBtn').disabled")

                    running = {'awg2': {'container_exists': True, 'container_running': True},
                               'awg2__2': {'container_exists': True, 'container_running': True}}
                    page.route('**/*', route_request)
                    page.goto('http://panel.test/server/0')
                    pump_until(lambda: 'check' in held)
                    if saved:
                        pump_until(lambda: connections)
                        self.assertEqual(answer_connection(), 'awg2')
                        page.locator('.client-name').wait_for()
                        self.assertTrue(page.locator('#checkBtn').is_disabled())
                    else:
                        self.assertEqual(connections, [])
                    answer_check(running)
                    # Smart Route is still held, but the grid is visible.
                    self.assertNotIn('apps-pending', page.locator('#protocols-grid').get_attribute('class'))
                    if not saved:
                        pump_until(lambda: connections)
                        answer_connection()
                        page.locator('.client-name').wait_for()
                    self.assertEqual(connections, [])
                    held.pop('status').fulfill(json={'installed': True, 'enabled': True})
                    # Rapid A -> B -> A: original A response must be dropped.
                    page.evaluate('() => { loadConnections(); }')
                    pump_until(lambda: connections)
                    page.select_option('#connProtoSelect', 'awg2__2')
                    page.select_option('#connProtoSelect', 'awg2')
                    answer_connection('Stale peer')
                    pump_until(lambda: connections)
                    self.assertNotIn('Stale peer', page.locator('#connectionsListInner').inner_text())
                    self.assertEqual(answer_connection('Current peer'), 'awg2')
                    page.locator('.client-name').filter(has_text='Current peer').wait_for()
                    # Polls preserve an editor opened before or during a request.
                    page.evaluate("document.getElementById('connectionsListInner').insertAdjacentHTML('beforeend', '<input class=rename-editor value=draft>')")
                    page.evaluate('() => { loadConnections(true); }')
                    page.wait_for_timeout(50)
                    self.assertEqual(connections, [])
                    page.evaluate("() => { document.querySelector('.rename-editor').remove(); loadConnections(true); }")
                    pump_until(lambda: connections)
                    page.evaluate("document.getElementById('connectionsListInner').insertAdjacentHTML('beforeend', '<input class=rename-editor value=draft>')")
                    answer_connection('Poll peer')
                    page.wait_for_function('!window.connLoading')
                    self.assertEqual(page.locator('.rename-editor').input_value(), 'draft')
                    # Stopped instance falls back, and disappearing all instances
                    # invalidates a list still in flight.
                    page.evaluate('() => { checkServer(); }')
                    pump_until(lambda: 'check' in held)
                    answer_check({'awg2': {'container_exists': True, 'container_running': False},
                                  'awg2__2': running['awg2__2']})
                    pump_until(lambda: connections)
                    self.assertEqual(connections[0][0], 'awg2__2')
                    page.evaluate('() => { checkServer(); }')
                    pump_until(lambda: 'check' in held)
                    answer_check({})
                    answer_connection('Deleted peer')
                    page.wait_for_function('!window.connLoading')
                    self.assertTrue(page.locator('#connectionsSection').is_hidden())
                    self.assertNotIn('Deleted peer', page.locator('#connectionsListInner').inner_text())
                    self.assertEqual(errors, [])
                    page.close()
            browser.close()

    def test_slow_queue_script_does_not_break_initial_polling(self):
        with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw, ExitStack() as cleanup:
            options = {'headless': True}
            if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
                options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
            browser = pw.chromium.launch(**options)
            cleanup.callback(browser.close)
            datafile = Path(tmp) / 'data.json'
            datafile.write_text(json.dumps({'servers': [{'uid': 'fixture', 'name': 'Test',
                'host': '192.0.2.1', 'ssh_port': 22, 'username': 'root', 'peer_poll_interval': 5,
                'protocols': {'awg2': {'installed': True}}}]}))
            admin = {'id': 'fixture', 'role': 'admin'}
            with patch.object(panel, 'DATA_FILE', str(datafile)), \
                 patch.object(panel, 'get_current_user', return_value=admin), \
                 patch.object(panel, '_check_admin', return_value=admin), \
                 patch.object(panel, 'get_ssh', side_effect=AssertionError('Real SSH forbidden')):
                with closing(TestClient(panel.app)) as client:
                    html = client.get('/server/0').text
                    page = browser.new_page()
                    held, errors = [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route_request(route):
                        path = urlsplit(route.request.url).path
                        if path == '/server/0':
                            route.fulfill(content_type='text/html', body=html)
                        elif path == '/static/js/peer-queue.js':
                            held.append(route)
                        elif path.startswith('/static/'):
                            response = client.get(path)
                            route.fulfill(body=response.content, content_type=response.headers.get('content-type'))
                        elif path.endswith('/check'):
                            route.fulfill(json={'connection': 'ok', 'docker_installed': True,
                                'protocols': {'awg2': {'container_exists': True, 'container_running': True}}})
                        elif path.endswith('/connections'):
                            route.fulfill(json={'clients': [{'clientId': 'peer', 'userData': {'clientName': 'One'}}]})
                        elif route.request.url.startswith('http://panel.test/'):
                            route.fulfill(json={})
                        else:
                            route.abort()

                    page.route('**/*', route_request)
                    page.goto('http://panel.test/server/0', wait_until='commit')
                    for _ in range(100):
                        if held:
                            break
                        page.wait_for_timeout(20)
                    self.assertTrue(held)
                    # Allow fast API responses to overtake the held script.
                    page.wait_for_timeout(500)
                    held[0].fulfill(content_type='application/javascript',
                                    body=(Path(__file__).resolve().parents[1] / 'static/js/peer-queue.js').read_text())
                    page.locator('.client-name').filter(has_text='One').wait_for()
                    self.assertEqual(errors, [])
                    page.wait_for_function('Boolean(window.connPollTimer)')
                    self.assertEqual(errors, [])
                    page.close()
            browser.close()
