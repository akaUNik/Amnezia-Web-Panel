"""Conditional application of dashboard observations to current panel state."""
import copy

from peer_recovery import GENERATION


TARGET_FIELDS = ('uid', 'host', 'ssh_port', 'username', 'password', 'private_key')


def same_target(left, right):
    return all(left.get(key) == right.get(key) for key in TARGET_FIELDS)


def reconcile_status(current, baseline, original, candidate, status):
    """Caller holds DATA_LOCK and STATE_FILE_LOCK; no remote work here.

    UID survives reorder. Target/recovery conflicts discard the entire result;
    protocol conflicts discard only that instance and forbid snapshot publication.
    Unrelated current state (assignments, accounting, server names) stays intact.
    """
    matches = [s for s in current.get('servers', []) if s.get('uid') == original.get('uid')]
    if (not original.get('uid') or len(matches) != 1 or not same_target(matches[0], original)
            or current.get(GENERATION) != baseline.get(GENERATION)):
        return False
    server = matches[0]
    before = original.get('protocols', {})
    protocols = server.setdefault('protocols', {})
    for proto in list(status['protocols']):
        if protocols.get(proto) != before.get(proto):
            status['protocols'].pop(proto)
            status['observation_conflict'] = True
            continue
        if proto in candidate:
            protocols[proto] = copy.deepcopy(candidate[proto])
        else:
            protocols.pop(proto, None)
    return True
