"""Run unittest discovery with fresh state and required browser/JS tooling."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest


def main():
    if not shutil.which('node'):
        raise RuntimeError('Node.js is required for JavaScript regression checks')
    with tempfile.TemporaryDirectory(prefix='panel-ci-tests-') as directory:
        state=Path(directory).resolve()
        (state/'known_hosts').touch()
        os.environ.update(DATA_FILE=str(state/'data.json'),
                          SSH_KNOWN_HOSTS_FILE=str(state/'known_hosts'),
                          TUNNEL_STATE_FILE=str(state/'tunnels.json'),
                          TUNNEL_BIN_DIR=str(state/'bin'),
                          SECRET_KEY='isolated-ci-fixture')
        suite=unittest.defaultTestLoader.discover('tests')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        # The pre-existing pytest-only live-server module is outside unittest's
        # required synthetic coverage. All other skipped groups are failures.
        required_skips=[(test,reason) for test,reason in result.skipped
                        if not test.id().endswith('.test_playwright_e2e')]
        if required_skips:
            print('Required regression groups skipped:',required_skips)
        raise SystemExit(not result.wasSuccessful() or bool(required_skips))


if __name__=='__main__':
    main()
