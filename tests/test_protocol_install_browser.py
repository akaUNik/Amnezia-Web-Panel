"""Exercise the rendered installation dialog with controlled HTTP responses."""
import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

import app as panel
import peer_lifecycle_lock as lifecycle


class ProtocolInstallBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        stack = cls.enterClassContext(ExitStack())
        cls.directory = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        playwright = stack.enter_context(sync_playwright())
        options = {}
        if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
        cls.browser = playwright.chromium.launch(headless=True, **options)
        stack.callback(cls.browser.close)

    def setUp(self):
        self.datafile = self.directory / 'data.json'
        admin = {'id': 'fixture', 'role': 'admin'}
        self.enterContext(patch.object(panel, 'DATA_FILE', str(self.datafile)))
        self.enterContext(patch.object(panel, 'get_current_user', return_value=admin))
        self.enterContext(patch.object(panel, '_check_admin', return_value=admin))
        self.client = TestClient(panel.app, cookies={'lang': 'en'})
        self.addCleanup(self.client.close)

    def open_server(self, protocols=None, viewport=None):
        protocols = protocols or {}
        self.check_protocols = {key: dict(record, container_exists=True, container_running=True)
                                for key, record in protocols.items()}
        self.datafile.write_text(json.dumps({'servers': [{
            'uid': 'install-fixture', 'name': 'Install fixture', 'host': '192.0.2.1',
            'ssh_port': 22, 'username': 'root', 'protocols': protocols,
        }]}))
        self.installs = []
        self.checks = []
        self.errors = []
        html = self.client.get('/server/0')
        self.assertEqual(html.status_code, 200)
        page = self.browser.new_page(viewport=viewport)
        self.addCleanup(page.close)
        self.addCleanup(page.unroute_all, behavior='ignoreErrors')
        page.clock.install()
        page.on('pageerror', lambda error: self.errors.append(str(error)))

        def route_request(route):
            parsed = urlsplit(route.request.url)
            if parsed.netloc != 'panel.test':
                route.abort()
            elif parsed.path == '/server/0':
                route.fulfill(content_type='text/html', body=html.text)
            elif parsed.path == '/api/servers/0/install':
                self.installs.append(route)
            elif parsed.path == '/api/servers/0/check':
                self.checks.append(route.request.method)
                route.fulfill(json={'connection': 'ok', 'docker_installed': True,
                                    'protocols': self.check_protocols})
            elif parsed.path.startswith('/static/'):
                response = self.client.get(parsed.path)
                route.fulfill(body=response.content, content_type=response.headers['content-type'])
            else:
                route.fulfill(json={})

        page.route('**/*', route_request)
        page.goto('http://panel.test/server/0')
        expect(page.locator('#checkBtn')).to_be_enabled()
        return page

    def start_install(self, page, protocol='awg3', another=False):
        page.evaluate('([protocol, another]) => openInstallModal(protocol, another)', [protocol, another])
        with page.expect_request('**/api/servers/0/install') as request:
            page.locator('#installBtn').click()
        self.assertEqual(request.value.method, 'POST')
        self.assertEqual(request.value.post_data_json['protocol'], protocol)
        self.assertEqual(request.value.post_data_json['install_another'], another)

    def test_waiting_does_not_invent_stages_or_percentages(self):
        page = self.open_server()
        self.start_install(page)
        page.clock.run_for(6500)
        dialog = page.locator('#installModal')
        expect(dialog.get_by_role('status')).to_have_text('Waiting for the installation result…')
        expect(dialog.locator('#installProgressBar')).to_be_hidden()
        expect(dialog.locator('#installLog')).to_be_hidden()
        expect(dialog.locator('#installDone')).to_be_hidden()
        expect(page.locator('.toast-success')).to_have_count(0)
        dialog.locator('.modal-close').click()
        page.evaluate("openInstallModal('wireguard')")
        expect(dialog.get_by_role('status')).to_be_visible()
        expect(dialog.locator('#installModalTitle')).to_contain_text('AmneziaWG')
        self.assertEqual(len(self.installs), 1)
        self.assertEqual(self.errors, [])
        self.installs[0].fulfill(json={'status': 'success', 'log': ['Interface verified']})
        expect(dialog.locator('#installDone')).to_be_visible()

    def test_lifecycle_errors_and_installer_error_are_distinct(self):
        page = self.open_server()
        failure = 'Docker build failed\n<missing header>\ncompiler exited with status 1'
        cases = (
            (503, 'peer_lifecycle_busy', 'Installation did not start', 'error'),
            (503, 'peer_lifecycle_uncertain', 'administrator must complete lifecycle recovery', 'error'),
            (503, 'peer_lifecycle_still_running', 'Installation is still running', 'info'),
            (400, None, failure, 'error'),
        )
        for count, (status, code, expected, tone) in enumerate(cases, 1):
            with self.subTest(code=code, status=status):
                self.start_install(page)
                payload = {'code': code, 'error': 'Generic lifecycle message'} if code else {'error': failure}
                self.installs[-1].fulfill(status=status, json=payload)
                line = page.locator(f'#installLog .log-line.{tone}')
                expect(line).to_contain_text(expected)
                expect(page.locator('#installWaiting')).to_be_hidden()
                expect(page.locator('#installDone')).to_be_visible()
                expect(page.locator('.toast-success')).to_have_count(0)
                self.assertEqual(len(self.installs), count)
                if code is None:
                    self.assertEqual(line.text_content(), f'Installation error: {failure}')
                    expect(line).to_have_css('white-space', 'pre-wrap')
                    expect(line.locator('*')).to_have_count(0)
                    expect(page.locator('.toast-error').last).to_contain_text('<missing header>')
        self.assertEqual(self.errors, [])

    def test_lost_install_result_requires_manual_verification(self):
        cases = (
            ('first install', {}, False, None),
            ('reinstall', {'awg3': {'installed': True}}, False, 502),
            ('additional instance', {'awg3': {'installed': True}}, True, 504),
            ('Cloudflare Quick Tunnel', {}, False, 524),
        )
        for mode, protocols, another, status in cases:
            with self.subTest(mode=mode, status=status):
                page = self.open_server(protocols, viewport={'width': 390, 'height': 844})
                checks_before = len(self.checks)
                self.start_install(page, another=another)
                # A Docker container can show up before an initial install is complete.
                self.check_protocols['awg3'] = {'container_exists': True, 'container_running': True}
                if status == 502:
                    self.installs[0].fulfill(status=status, content_type='text/html', body='<h1>Bad Gateway</h1>')
                elif status == 504:
                    self.installs[0].fulfill(status=status, json={'error': 'Gateway Timeout'})
                elif status == 524:
                    self.installs[0].fulfill(status=status, content_type='text/html', body='<h1>A timeout occurred</h1>')
                else:
                    self.installs[0].abort('connectionreset')
                page.clock.run_for(11000)
                line = page.locator('#installLog .log-line.info')
                expect(line).to_have_text(
                    'The installation result could not be confirmed. Check the target instance on the server before retrying.'
                )
                expect(page.locator('#installWaiting')).to_be_hidden()
                expect(page.locator('#installDone')).to_be_visible()
                expect(page.locator('#installLog .log-line.success, .toast-success')).to_have_count(0)
                self.assertEqual(len(self.installs), 1)
                self.assertEqual(len(self.checks), checks_before)
                self.assertEqual(self.errors, [])

    def test_success_uses_real_install_response_and_refreshes_protocol_card(self):
        manager = Mock()
        with patch.multiple(
            panel, get_ssh=Mock(), get_protocol_manager=Mock(return_value=manager),
            ensure_docker_installed=Mock(return_value='Docker ready'), get_used_ports=Mock(return_value={}),
        ), patch.multiple(lifecycle, _fenced=False, _fence_path=None):
            for protocol in ('awg3', 'wireguard'):
                with self.subTest(protocol=protocol):
                    manager.install_protocol.return_value = {
                        'status': 'success', 'log': ['Interface <ready>', '! Optional step skipped'],
                    }
                    page = self.open_server()
                    checks_before = len(self.checks)
                    self.start_install(page, protocol)
                    status_card = page.locator(f'#{protocol}-status')
                    expect(status_card.locator('.badge-success')).to_have_count(0)
                    route = self.installs[0]
                    response = self.client.post('/api/servers/0/install', json=route.request.post_data_json)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['protocol'], protocol)
                    record = json.loads(self.datafile.read_text())['servers'][0]['protocols'][protocol]
                    self.check_protocols[protocol] = dict(record, container_exists=True, container_running=True)
                    route.fulfill(json=response.json())
                    expect(page.locator('#installLog .log-line.success')).to_have_text(['✓ Docker ready', '✓ Interface <ready>'])
                    expect(page.locator('#installLog .log-line.warning')).to_have_text('⚠ Optional step skipped')
                    expect(page.locator('#installWaiting')).to_be_hidden()
                    expect(page.locator('#installDone')).to_be_visible()
                    expect(page.locator('.toast-success').last).to_have_text('✓ Protocol installed successfully!')
                    page.clock.run_for(900)
                    expect(status_card.locator('.badge-success')).to_be_visible()
                    self.assertEqual(len(self.checks), checks_before + 1)
                    self.assertEqual(len(self.installs), 1)
                    self.assertEqual(self.errors, [])
