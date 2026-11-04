"""Synthetic dashboard timing; no server, credentials or external network needed.

Run: uv run --locked python -m scripts.benchmark_dashboard
All browser requests are intercepted; SSH is replaced before any API call.
"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit


class DelayedSSH:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.wait = 0

    def connect(self):
        pass

    def disconnect(self):
        pass

    def run_command(self, command, **kwargs):
        started = time.monotonic()
        with self.lock:
            self.wait += time.monotonic() - started
            self.calls += 1
            time.sleep(.03)
            if '__AWP_CPU__' in command:
                return ('__AWP_CPU__\n12.5\n__AWP_RAM__\n25 100\n'
                        '__AWP_DISK__\n50 200\n__AWP_NET__\n10 20\n'
                        '__AWP_UPTIME__\nup 2 days\n__AWP_IPV6__\n', '', 0)
            if 'uptime' in command:
                return 'up 2 days', '', 0
            return '0', '', 0


async def measure(panel, client, ssh):
    from playwright.async_api import async_playwright
    samples = []
    async with async_playwright() as pw:
        options = {'headless': True}
        if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
        browser = await pw.chromium.launch(**options)
        try:
            for label in ('cold', 'repeat'):
                html = client.get('/server/0').text
                page = await browser.new_page()
                await page.add_init_script("""
                    new MutationObserver(() => {
                        if (!window.firstRowAt && document.querySelector('.client-name'))
                            window.firstRowAt = performance.now();
                    }).observe(document, {childList: true, subtree: true});
                """)
                start = time.monotonic()
                count, waiting = ssh.calls, ssh.wait
                summary = None

                async def route_request(route):
                    nonlocal summary
                    path = urlsplit(route.request.url).path
                    if path == '/server/0':
                        await route.fulfill(content_type='text/html', body=html)
                    elif path.endswith('/check'):
                        await asyncio.sleep(.35)
                        await route.fulfill(json={'connection': 'ok', 'docker_installed': True,
                            'protocols': {'awg2': {'container_exists': True, 'container_running': True}}})
                    elif path.endswith('/stats'):
                        # Deterministic competing transport owner, separate from
                        # the per-command latency. A cache hit never waits on it.
                        ssh.lock.acquire()
                        timer = threading.Timer(.05, ssh.lock.release)
                        timer.start()
                        response = await asyncio.to_thread(client.post, path)
                        timer.join()
                        summary = time.monotonic() - start
                        await route.fulfill(status=response.status_code, body=response.content,
                                            headers=dict(response.headers))
                    elif path.endswith('/connections'):
                        await asyncio.sleep(.02)
                        await route.fulfill(json={'clients': [{'clientId': 'fake-peer',
                            'userData': {'clientName': 'Synthetic peer', 'clientIp': '10.8.1.2'}}]})
                    elif path.startswith('/static/'):
                        response = client.get(path)
                        await route.fulfill(status=response.status_code, body=response.content,
                            content_type=response.headers.get('content-type', 'application/octet-stream'))
                    elif route.request.url.startswith('http://panel.test/'):
                        await route.fulfill(json={})
                    else:
                        await route.abort()

                await page.route('**/*', route_request)
                await page.goto('http://panel.test/server/0')
                await page.locator('.client-name').filter(has_text='Synthetic peer').wait_for()
                first_row = await page.evaluate('window.firstRowAt / 1000')
                await page.wait_for_function("document.querySelectorAll('#serverStats .stat-pill').length === 4")
                samples.append({'path': label, 'first_row_ms': round(first_row * 1000),
                    'summary_ms': round(summary * 1000), 'ssh_commands': ssh.calls - count,
                    'transport_wait_ms': round((ssh.wait - waiting) * 1000)})
                await page.close()
        finally:
            await browser.close()
    return samples


def main():
    with tempfile.TemporaryDirectory(prefix='dashboard-benchmark-') as directory:
        state = Path(directory)
        os.environ.update(DATA_FILE=str(state/'data.json'), SECRET_KEY='synthetic-benchmark',
                          TUNNEL_STATE_FILE=str(state/'tunnels.json'))
        import app as panel
        from fastapi.testclient import TestClient
        server = {'uid': 'benchmark', 'name': 'Synthetic', 'host': '192.0.2.1',
                  'ssh_port': 22, 'username': 'root', 'protocols': {'awg2': {'installed': True}}}
        (state/'data.json').write_text(json.dumps({'servers': [server]}))
        admin = {'id': 'synthetic', 'role': 'admin'}
        ssh = DelayedSSH()
        with patch.object(panel, '_check_admin', return_value=admin), \
             patch.object(panel, 'get_current_user', return_value=admin), \
             patch.object(panel, 'get_ssh', return_value=ssh):
            client = TestClient(panel.app)
            print(json.dumps(asyncio.run(measure(panel, client, ssh)), indent=2))


if __name__ == '__main__':
    main()
