"""Settings update checks use only synthetic API responses, never GitHub."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

import app as panel
from project_info import LATEST_RELEASE_API_URL, RELEASES_URL

ROOT = Path(__file__).resolve().parents[1]


class UpdateBrowserTests(unittest.TestCase):
    def test_settings_locales_api_states_retry_and_trusted_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp).resolve()/'data.json'
            data.write_text(json.dumps({'users':[], 'servers':[], 'user_connections':[], 'settings':{}}))
            admin = {'id':'admin','role':'admin'}
            with patch.object(panel,'DATA_FILE',str(data)), \
                 patch.object(panel,'_check_admin',return_value=admin), \
                 patch.object(panel,'get_current_user',return_value=admin):
                client = TestClient(panel.app)
                with sync_playwright() as pw:
                    options = {'headless':True}
                    if os.environ.get('PANEL_BROWSER_EXECUTABLE'):
                        options['executable_path'] = os.environ['PANEL_BROWSER_EXECUTABLE']
                    browser = pw.chromium.launch(**options)
                    try:
                        for locale in ('en','ru','fr','zh','fa'):
                            client.cookies.set('lang',locale)
                            html = client.get('/settings').text
                            strings = json.loads((ROOT/'translations'/f'{locale}.json').read_text())
                            context = browser.new_context(viewport={'width':375 if locale=='ru' else 1280,'height':900})
                            page = context.new_page()
                            errors, requests = [], []
                            page.on('pageerror',lambda error:errors.append(str(error)))
                            result = {'status':200,'body':{'tag_name':'v999.0.0','draft':False,'prerelease':False,'html_url':'https://evil.invalid'}}
                            def route_request(route):
                                url = route.request.url
                                requests.append(url)
                                if url == LATEST_RELEASE_API_URL:
                                    self.assertNotIn('authorization',route.request.headers)
                                    route.fulfill(status=result['status'],json=result['body'])
                                elif url == RELEASES_URL:
                                    route.fulfill(content_type='text/html',body='<title>Own releases</title>Releases')
                                elif url.startswith('http://panel.test/'):
                                    path = urlsplit(url).path
                                    if path=='/settings':
                                        route.fulfill(content_type='text/html',body=html)
                                    elif path.startswith('/static/'):
                                        response=client.get(path)
                                        route.fulfill(status=response.status_code,body=response.content,
                                                      content_type=response.headers.get('content-type','application/octet-stream'))
                                    else:
                                        route.fulfill(json={})
                                else:
                                    route.abort()
                            context.route('**/*',route_request)
                            page.goto('http://panel.test/settings')
                            self.assertEqual(page.url,'http://panel.test/settings')
                            self.assertIn(strings['nav_settings'],page.title())
                            self.assertEqual(page.locator('html').get_attribute('lang'),locale)
                            self.assertTrue(page.locator('#downloadUpdateBtn').is_visible())
                            button=page.locator('#checkUpdateBtn')
                            button.click()
                            page.wait_for_function("document.getElementById('updateStatus').dataset.state==='available'")
                            self.assertIn('v999.0.0',page.locator('#updateStatus').inner_text())
                            self.assertIn(strings['update_available'],page.locator('#updateStatus').inner_text())
                            self.assertFalse(button.is_disabled())
                            self.assertEqual(page.locator('#downloadUpdateBtn').get_attribute('href'),RELEASES_URL)
                            self.assertTrue(page.locator('#settings-about').evaluate('(node)=>node.scrollWidth<=node.clientWidth+1'))
                            self.assertTrue(button.evaluate('(node)=>node.scrollWidth<=node.clientWidth+1'))
                            if os.environ.get('PANEL_QA_SCREENSHOTS') and locale in ('en','ru'):
                                target=Path(os.environ['PANEL_QA_SCREENSHOTS']);target.mkdir(parents=True,exist_ok=True)
                                page.locator('#settings-about').screenshot(path=str(target/f'updates-{locale}.png'))
                            for status, state, key in ((404,'no_release','update_no_release'),(503,'error','update_error')):
                                result['status']=status;result['body']={}
                                button.click()
                                page.wait_for_function('(state)=>document.getElementById("updateStatus").dataset.state===state',arg=state)
                                self.assertIn(strings[key],page.locator('#updateStatus').inner_text())
                                self.assertFalse(button.is_disabled())
                                self.assertTrue(page.locator('#checkUpdateSpinner').is_hidden())
                                self.assertTrue(page.locator('#downloadUpdateBtn').is_visible())
                            result.update(status=200,body={'tag_name':'<img src=x onerror="alert(1)">','draft':False,'prerelease':False})
                            button.click()
                            page.wait_for_function("document.getElementById('updateStatus').dataset.state==='uncomparable'")
                            self.assertEqual(page.locator('#updateStatus img').count(),0)
                            with page.expect_popup() as popup:
                                page.locator('#downloadUpdateBtn').click()
                            popup.value.wait_for_load_state()
                            self.assertEqual(popup.value.url,RELEASES_URL)
                            self.assertEqual(sum(url==LATEST_RELEASE_API_URL for url in requests),4)
                            self.assertFalse(any('PRVTPRO' in url or 'gitverse' in url.lower() or 'evil.invalid' in url for url in requests))
                            self.assertEqual(errors,[])
                            context.close()
                    finally:
                        browser.close()
