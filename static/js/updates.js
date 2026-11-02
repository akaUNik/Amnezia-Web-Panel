/* SemVer comparison and stable release checks. No GitHub token is used. */
(function (root) {
    'use strict';
    const identifier = '(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)';
    const semver = new RegExp('^v?(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)' +
        '(?:-(' + identifier + '(?:\\.' + identifier + ')*))?' +
        '(?:\\+([0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*))?$');

    function parseVersion(value) {
        const match = typeof value === 'string' && value.match(semver);
        if (!match || match[0] !== value) return null;
        return {numbers: match.slice(1, 4).map(BigInt), prerelease: match[4] ? match[4].split('.') : []};
    }

    function compareVersions(left, right) {
        const a = parseVersion(left), b = parseVersion(right);
        if (!a || !b) return null;
        for (let i = 0; i < 3; i++) {
            if (a.numbers[i] !== b.numbers[i]) return a.numbers[i] > b.numbers[i] ? 1 : -1;
        }
        if (!a.prerelease.length || !b.prerelease.length) {
            return Number(!a.prerelease.length) - Number(!b.prerelease.length);
        }
        for (let i = 0; i < Math.max(a.prerelease.length, b.prerelease.length); i++) {
            if (a.prerelease[i] === undefined) return -1;
            if (b.prerelease[i] === undefined) return 1;
            const x = a.prerelease[i], y = b.prerelease[i];
            if (x === y) continue;
            const nx = /^[0-9]+$/.test(x), ny = /^[0-9]+$/.test(y);
            if (nx && ny) return BigInt(x) > BigInt(y) ? 1 : -1;
            if (nx !== ny) return nx ? -1 : 1;
            return x > y ? 1 : -1;
        }
        return 0;
    }

    async function lookupRelease(source, current, fetchImpl = root.fetch, timeoutMs = 10000) {
        const controller = new AbortController();
        let timer;
        try {
            // The race also bounds a stalled response body and test/fetch doubles
            // that ignore abort. The rejection remains handled after fetch wins.
            const timeout = new Promise((_, reject) => {
                timer = setTimeout(() => {
                    controller.abort();
                    reject(new Error('timeout'));
                }, timeoutMs);
            });
            const request = (async () => {
                const response = await fetchImpl(source.api_url, {
                    signal: controller.signal, credentials: 'omit',
                    headers: {'Accept': 'application/vnd.github+json'}
                });
                if (response.status === 404) return {state: 'no_release'};
                if (response.status === 403 || response.status === 429) return {state: 'rate_limit'};
                if (!response.ok) return {state: 'error'};
                const data = await response.json();
                if (!data || typeof data !== 'object' || typeof data.tag_name !== 'string' ||
                    data.draft !== false || data.prerelease !== false) return {state: 'error'};
                const version = parseVersion(data.tag_name);
                if (version && version.prerelease.length) return {state: 'error'};
                const compared = compareVersions(data.tag_name, current);
                if (compared === null) return {state: 'uncomparable'};
                return {state: compared > 0 ? 'available' : 'no_update', version: data.tag_name};
            })();
            return await Promise.race([request, timeout]);
        } catch (_) {
            return {state: controller.signal.aborted ? 'timeout' : 'error'};
        } finally {
            clearTimeout(timer);
        }
    }

    async function check(options) {
        const {document: doc, source, current, messages} = options;
        const button = doc.getElementById('checkUpdateBtn');
        if (button.disabled) return;
        const text = doc.getElementById('checkUpdateBtnText');
        const spinner = doc.getElementById('checkUpdateSpinner');
        const status = doc.getElementById('updateStatus');
        const download = doc.getElementById('downloadUpdateBtn');
        download.href = source.releases_url;
        button.disabled = true;
        text.textContent = messages.checking;
        spinner.classList.remove('hidden');
        status.classList.add('hidden');
        try {
            const result = await lookupRelease(source, current, options.fetchImpl, options.timeoutMs);
            status.dataset.state = result.state;
            status.textContent = messages[result.state] + (result.state === 'available' ? ': ' + result.version : '');
            status.style.border = '1px solid var(' + (result.state === 'available' ? '--success' : '--border-color') + ')';
            status.classList.remove('hidden');
            return result;
        } finally {
            button.disabled = false;
            text.textContent = messages.check;
            spinner.classList.add('hidden');
        }
    }

    const api = {parseVersion, compareVersions, lookupRelease, check};
    root.PanelUpdates = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : window);
