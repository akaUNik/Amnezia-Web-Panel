"""Execute the modal lifecycle and real polling code with deterministic timers."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for JavaScript checks')
class PluginModalTests(unittest.TestCase):
    def run_scenario(self, pending_response=False):
        source = (Path(__file__).parents[1] / 'templates/server.html').read_text()
        lifecycle = source[source.index('    function openAwgPluginModal()'):source.index('    function awgPluginElement(')]
        polling = source[source.index('    async function pollAwgPluginUpdate('):source.index("    document.addEventListener('DOMContentLoaded', loadAwgPluginStatus);")]
        script = r'''
const assert = require('node:assert/strict');
let seq = 0;
const timers = new Map();
const intervals = new Map();
const setTimeout = fn => { timers.set(++seq, fn); return seq; };
const clearTimeout = id => timers.delete(id);
const clearInterval = id => intervals.delete(id);
const op = {opId: 'op', offset: 0};
const awgPluginOps = {kernel_module: op};
let awgPluginStatus = {};
let calls = 0;
let stage = {};
let journal = {};
const document = {
    getElementById: id => id.startsWith('awgPluginLog-') ? journal : null,
    querySelector: () => stage,
};
const openModal = () => {};
const closeModal = () => {};
const renderAwgPluginModal = () => {
    stage = {textContent: 'running'};
    journal = {textContent: ''};
    if (!op.creep) { intervals.set(++seq, true); op.creep = seq; }
};
const awgPluginLabel = () => ({success: 'success'});
const setAwgProgress = (...args) => {
    if (!args[4]) { clearInterval(op.creep); op.creep = null; }
};
const loadAwgPluginStatus = async () => {};
let release;
const apiCall = async () => {
    calls++;
    if (pendingResponse && calls === 2) await new Promise(resolve => { release = resolve; });
    return {state: calls === 1 ? 'running' : 'success', next_offset: 1,
            result: {installed: 'new'}, lines: [{ts: 't', stage: 'done', text: 'journal'}]};
};
'''
        script = 'const pendingResponse = ' + str(pending_response).lower() + ';\n' + script
        script += lifecycle + polling
        script += r'''
(async () => {
    openAwgPluginModal();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 1);
    assert.equal(timers.size, 1);
    closeAwgPluginModal();
    assert.equal(timers.size, 0);
    assert.equal(op.timer, null);
    assert.equal(op.creep, null);
    openAwgPluginModal();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(calls, 2);
    if (pendingResponse) {
        closeAwgPluginModal();
        openAwgPluginModal();
        assert.equal(calls, 2, 'reopening must not duplicate the pending request');
        release();
        await new Promise(resolve => setImmediate(resolve));
    }
    assert.match(stage.textContent, /success/);
    assert.equal((journal.textContent.match(/journal/g) || []).length, 2, 'both responses must remain in the reopened journal');
    assert.equal(awgPluginOps.kernel_module, undefined);
    assert.equal(intervals.size, 0);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reopening_resumes_polling_and_displays_completion(self):
        self.run_scenario()

    def test_reopening_during_request_preserves_journal_and_displays_completion(self):
        self.run_scenario(pending_response=True)
