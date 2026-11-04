"""Peer action queue: one request at a time, dedup, delete cancel, toast."""
import os
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / 'static' / 'js' / 'peer-queue.js'
CSS = ROOT / 'static' / 'css' / 'style.css'

STUBS = """
window._ = k => k;
window.requests = [];
window.apiCall = (url, method, body) => new Promise((resolve, reject) => {
    window.requests.push({ url, method, body, resolve, reject });
});
"""

ROWS = """
<div id="toastContainer"></div>
<div class="client-item" data-server-id="1" data-protocol="awg"
     data-client-id="p1" data-peer-name="Peer One"><div class="client-meta"></div></div>
<div class="client-item" data-server-id="1" data-protocol="awg"
     data-client-id="p2" data-peer-name="Peer Two"><div class="client-meta"></div></div>
"""


class PeerQueueBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.addClassCleanup(cls.pw.stop)
        options = {'headless': True}
        if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
        cls.browser = cls.pw.chromium.launch(**options)
        cls.addClassCleanup(cls.browser.close)

    def page(self):
        page = self.browser.new_page()
        page.set_content(ROWS)
        page.add_style_tag(path=str(CSS))
        page.add_script_tag(content=STUBS)
        page.add_script_tag(path=str(JS))
        return page

    def test_completed_toggle_updates_both_row_layouts_before_queue_drains(self):
        for users_page in (False, True):
            with self.subTest(users_page=users_page):
                page = self.page()
                self.addCleanup(page.close)
                # Use the two production layouts: server dims the row; users
                # dims only the information panel. Keep another row's editor.
                page.evaluate("""users => {
                    const row = document.querySelector('[data-client-id="p1"]');
                    row.innerHTML = `<div class="client-info"><div class="client-meta"></div></div>
                        <button class="peer-toggle" onclick="PeerQueue.enqueue({serverId:1,protocol:'awg',clientId:'p1',action:'disable'})">toggle</button>`;
                    (users ? row.querySelector('.client-info') : row).setAttribute('data-peer-opacity', '');
                    document.querySelector('[data-client-id="p2"]').insertAdjacentHTML('beforeend', '<input class="rename-editor" value="draft">');
                }""", users_page)
                page.locator('.peer-toggle').click()
                page.evaluate("PeerQueue.enqueue({serverId:1,protocol:'awg',clientId:'p2',action:'disable'})")
                page.evaluate('requests[0].resolve({status:"success",enabled:false})')
                page.wait_for_function('requests.length === 2')
                button = page.locator('.peer-toggle')
                self.assertIn('peer-toggle-off', button.get_attribute('class'))
                self.assertEqual(button.get_attribute('title'), 'start_peer_title')
                self.assertEqual(page.locator('[data-peer-opacity]').evaluate('el => el.style.opacity'), '0.5')
                self.assertEqual(page.locator('.rename-editor').input_value(), 'draft')
                button.click()
                page.evaluate('requests[1].resolve({status:"success",enabled:false})')
                page.wait_for_function('requests.length === 3')
                self.assertTrue(page.evaluate('requests[2].body.enable'))
                page.evaluate('requests[2].resolve({status:"success",enabled:true})')
                page.wait_for_function("!document.querySelector('.peer-toggle').classList.contains('peer-toggle-off')")
                self.assertEqual(page.locator('[data-client-id="p1"] .peer-disabled-badge').count(), 0)
                page.close()

    def test_completed_delete_removes_only_its_row_while_next_task_runs(self):
        page = self.page()
        self.addCleanup(page.close)
        page.evaluate("""() => {
            PeerQueue.enqueue({serverId:1,protocol:'awg',clientId:'p1',action:'delete'});
            PeerQueue.enqueue({serverId:1,protocol:'awg',clientId:'p2',action:'disable'});
            requests[0].resolve({status:'success'});
        }""")
        page.wait_for_function('requests.length === 2')
        self.assertEqual(page.locator('[data-client-id="p1"]').count(), 0)
        self.assertEqual(page.locator('[data-client-id="p2"]').count(), 1)

    def test_one_request_at_a_time_fifo(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p2', peerName:'Peer Two', action:'enable'});
        """)
        self.assertEqual(page.evaluate('requests.length'), 1)
        self.assertEqual(page.evaluate('requests[0].body.client_id'), 'p1')
        page.evaluate('requests[0].resolve({status:"success"})')
        page.wait_for_function('window.requests.length === 2', timeout=4000)
        self.assertEqual(page.evaluate('requests[1].body.client_id'), 'p2')
        page.close()

    def test_duplicate_pending_action_is_ignored(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
        """)
        self.assertEqual(page.evaluate('requests.length'), 1)
        self.assertEqual(page.locator('.peer-queue-toast').count(), 1)
        page.close()

    def test_delete_cancels_queued_toggle_for_same_peer(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p2', peerName:'Peer Two', action:'enable'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'delete'});
        """)
        page.evaluate('requests[0].resolve({status:"success"})')
        page.wait_for_function('window.requests.length === 2', timeout=4000)
        self.assertEqual(page.evaluate('requests[1].body.client_id'), 'p2')
        page.evaluate('requests[1].resolve({status:"success"})')
        page.wait_for_function('window.requests.length === 3', timeout=4000)
        self.assertIn('/connections/remove', page.evaluate('requests[2].url'))
        self.assertEqual(page.evaluate('requests[2].body.client_id'), 'p1')
        page.close()

    def test_toggle_after_delete_is_not_queued(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'delete'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
        """)
        self.assertEqual(page.evaluate('requests.length'), 1)
        self.assertIn('/connections/remove', page.evaluate('requests[0].url'))
        self.assertEqual(page.locator('.peer-queue-toast').count(), 1)
        page.close()

    def test_toast_shows_peer_action_and_reaches_100_then_hides(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'disable'});
        """)
        toast = page.locator('.peer-queue-toast')
        self.assertEqual(toast.count(), 1)
        text = toast.inner_text()
        self.assertIn('Peer One', text)
        self.assertIn('disable_connection_progress', text)
        page.evaluate('requests[0].resolve({status:"success"})')
        page.wait_for_function(
            "document.querySelector('.peer-queue-percent').textContent === '100%'", timeout=3000)
        page.wait_for_selector('.peer-queue-toast', state='detached', timeout=4000)
        page.close()

    def test_toast_layout_is_4px_track_with_gradient(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
        """)
        style = page.evaluate("""(() => {
            const track = document.querySelector('.peer-queue-track');
            const fill = document.querySelector('.peer-queue-fill');
            const t = getComputedStyle(track);
            const f = getComputedStyle(fill);
            return {
                height: t.height,
                border: t.borderTopWidth + ' ' + t.borderTopStyle,
                background: t.backgroundColor,
                gradient: f.backgroundImage,
                transition: f.transitionDuration,
                percentAbove: document.querySelector('.peer-queue-head .peer-queue-percent') !== null,
            };
        })()""")
        self.assertEqual(style['height'], '4px')
        self.assertEqual(style['border'], '1px solid')
        self.assertIn('rgb', style['background'])
        self.assertIn('linear-gradient', style['gradient'])
        self.assertIn('rgb', style['gradient'])
        self.assertEqual(style['transition'], '0.5s')
        self.assertTrue(style['percentAbove'])
        page.close()

    def test_toast_has_only_peer_action_bar_percent(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
        """)
        self.assertEqual(page.locator('.peer-queue-toast .peer-queue-peer').count(), 1)
        self.assertEqual(page.locator('.peer-queue-toast .peer-queue-action').count(), 1)
        self.assertEqual(page.locator('.peer-queue-toast .peer-queue-percent').count(), 1)
        self.assertEqual(page.locator('.peer-queue-toast img, .peer-queue-toast .client-avatar').count(), 0)
        self.assertTrue(page.locator('.peer-queue-error').is_hidden())
        self.assertTrue(page.locator('.peer-queue-close').is_hidden())
        text = page.locator('.peer-queue-toast').inner_text()
        self.assertIn('Peer One', text)
        self.assertIn('enable_connection_progress', text)
        self.assertNotIn('10.8.0.', text)
        page.close()

    def test_failed_toast_stays_and_next_task_runs(self):
        page = self.page()
        page.evaluate("""
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p1', peerName:'Peer One', action:'enable'});
            PeerQueue.enqueue({serverId:1, protocol:'awg', clientId:'p2', peerName:'Peer Two', action:'enable'});
        """)
        page.evaluate('requests[0].reject(new Error("boom"))')
        page.wait_for_function('window.requests.length === 2', timeout=4000)
        failed = page.locator('.peer-queue-toast.peer-queue-failed')
        self.assertEqual(failed.count(), 1)
        self.assertIn('boom', failed.inner_text())
        page.wait_for_timeout(1000)
        self.assertEqual(failed.count(), 1)
        failed.locator('.peer-queue-close').click()
        self.assertEqual(failed.count(), 0)
        page.close()


if __name__ == '__main__':
    unittest.main()
