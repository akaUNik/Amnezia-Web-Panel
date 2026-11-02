"""Launch a binary with disposable state and verify bundled web resources."""
import argparse
import json
import os
from pathlib import Path
import platform
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request


def smoke(binary, timeout=90):
    binary = Path(binary).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='panel-binary-smoke-') as directory:
        state = Path(directory).resolve()
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        data = state / 'data.json'
        data.write_text(json.dumps({'users': [], 'servers': [], 'user_connections': [],
                                    'settings': {'ssl': {'panel_port': port}}}))
        env = {**os.environ, 'DATA_FILE': str(data),
               'SSH_KNOWN_HOSTS_FILE': str(state / 'known_hosts'),
               'TUNNEL_STATE_FILE': str(state / 'tunnels.json'),
               'TUNNEL_BIN_DIR': str(state / 'bin'),
               'SECRET_KEY': 'isolated-binary-smoke-fixture'}
        options = ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == 'nt' else {'start_new_session': True})
        with (state / 'process.log').open('wb') as log:
            process = subprocess.Popen([str(binary)], cwd=state, env=env,
                                       stdout=log, stderr=log, **options)
            try:
                deadline = time.monotonic() + timeout
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Binary exited before readiness')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/login', timeout=1) as response:
                            if response.status != 200 or b'<html' not in response.read().lower():
                                raise RuntimeError('Login HTML missing')
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Binary readiness timed out')
                        time.sleep(.2)
                for resource in ('css/style.css', 'js/qrcode.min.js'):
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/static/{resource}', timeout=5) as response:
                        if response.status != 200 or len(response.read()) < 100:
                            raise RuntimeError(f'Bundled resource missing: {resource}')
            except Exception:
                # Only this process's isolated fixture log is printed.
                print((state / 'process.log').read_text(errors='replace'))
                raise
            finally:
                if process.poll() is None:
                    if os.name == 'nt':
                        process.send_signal(signal.CTRL_BREAK_EVENT)
                    else:
                        os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=True)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                    raise RuntimeError('Binary did not stop after shutdown signal')
                if process.returncode not in (0, -signal.SIGTERM):
                    raise RuntimeError(f'Unexpected binary exit: {process.returncode}')
    print(f'Smoke passed: {binary.name} ({platform.system()} {platform.machine()})')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary')
    args = parser.parse_args()
    smoke(args.binary)
