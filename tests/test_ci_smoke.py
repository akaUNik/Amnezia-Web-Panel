"""Exercise process, HTTP, state isolation, and shutdown in the smoke runner."""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.ci.smoke import smoke


@unittest.skipIf(os.name == 'nt', 'fixture uses a Unix executable shebang')
class SmokeTests(unittest.TestCase):
    def test_http_resources_temporary_state_and_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp)/'fixture'
            record = Path(tmp)/'record.json'
            executable.write_text(f'#!{sys.executable}\n' + '''
import json, os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
data=Path(os.environ['DATA_FILE'])
port=json.loads(data.read_text())['settings']['ssl']['panel_port']
Path(os.environ['SMOKE_RECORD']).write_text(json.dumps({'port':port,'data':str(data),'cwd':str(Path.cwd())}))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'<html>'+b'x'*150)
HTTPServer(('127.0.0.1',port),Handler).serve_forever()
''')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'SMOKE_RECORD':str(record)}):
                smoke(executable,timeout=10)
            state=json.loads(record.read_text())
            self.assertFalse(Path(state['data']).exists())
            self.assertFalse(Path(state['cwd']).exists())
            with socket.socket() as sock:
                self.assertNotEqual(sock.connect_ex(('127.0.0.1',state['port'])),0)

    def test_crashed_process_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable=Path(tmp)/'fixture'
            executable.write_text(f'#!{sys.executable}\nraise SystemExit(3)\n')
            executable.chmod(0o755)
            with self.assertRaises(RuntimeError):
                smoke(executable,timeout=10)
