"""Collect framed host statistics with a bounded SSH connection lifetime."""
from server_stats import HOST_STATS_COMMANDS, collect_host_stats


def collect_stats(ssh):
    ssh.connect()
    try:
        stats = collect_host_stats(ssh)
        errors = stats.get('errors', {})
        if 'batch' in errors and all(name in errors for name in HOST_STATS_COMMANDS):
            # A wholly failed observation must use the cache's stale/error path.
            raise RuntimeError('Host statistics unavailable: ' + errors['batch'])
        return stats
    finally:
        ssh.disconnect()
