"""Bounded in-memory channel with independent stdout/stderr queues."""
import threading


class Channel:
    def __init__(self, output=b'ok', error=b'', ack=True, status=True, stdin=False):
        self.output = bytearray(output)
        self.error = bytearray(error)
        self.ack = ack
        self.status = status
        self.expect_stdin = stdin
        self.input = bytearray()
        self.input_closed = False
        self.closed = False
        self.started = threading.Event()
        self.finished = threading.Event()
        self.commands = []

    def settimeout(self, timeout):
        self.timeout = timeout

    def exec_command(self, command):
        self.commands.append(command)
        self.started.set()
        if not self.ack:
            if not self.finished.wait(2):
                raise AssertionError('exec ACK was not interrupted')
            raise EOFError('exec ACK missing')

    def recv_ready(self):
        return bool(self.output)

    def recv_stderr_ready(self):
        return bool(self.error)

    def recv(self, size):
        chunk = bytes(self.output[:size])
        del self.output[:size]
        return chunk

    def recv_stderr(self, size):
        chunk = bytes(self.error[:size])
        del self.error[:size]
        return chunk

    def send_ready(self):
        return not self.output and not self.error

    def send(self, data):
        chunk = data[:7]
        self.input.extend(chunk)
        return len(chunk)

    def shutdown_write(self):
        self.input_closed = True

    def exit_status_ready(self):
        return self.status and not self.output and not self.error and (not self.expect_stdin or self.input_closed)

    def recv_exit_status(self):
        if not self.exit_status_ready():
            raise AssertionError('exit status read before draining both windows')
        return 0

    def close(self):
        self.closed = True
        self.finished.set()
