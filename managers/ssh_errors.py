"""Transport failures known to precede command submission."""


class SSHConnectionUnavailable(ConnectionError):
    """No command was submitted by this failed connection attempt."""

    code = 'ssh_connection_unavailable'
    status_code = 503
