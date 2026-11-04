"""One remote observation, with independently parsed summary sections."""
import math


STATS_COMMAND = r"""
printf '\n__AWP_CPU__\n'
CPU=$(LC_ALL=C top -bn1 2>/dev/null | awk '/Cpu\(s\)/ {print $2; exit}' | cut -d'%' -f1)
if [ -n "$CPU" ]; then printf '%s\n' "$CPU"; else
    BEFORE=$(grep '^cpu ' /proc/stat)
    sleep 0.5
    AFTER=$(grep '^cpu ' /proc/stat)
    printf '%s\n%s\n' "$BEFORE" "$AFTER" | awk '{u=$2+$4; t=$2+$4+$5; if(NR==1){pu=u;pt=t} else if(t>pt) printf "%.1f", (u-pu)/(t-pt)*100}'
fi
printf '\n__AWP_RAM__\n'
free -b | awk 'NR==2{printf "%d %d", $3, $2}'
printf '\n__AWP_DISK__\n'
df -B1 / | awk 'NR==2{printf "%d %d", $3, $2}'
printf '\n__AWP_NET__\n'
DEV=$(ip route | awk '/default/ {print $5}' | head -1)
awk -v dev="$DEV:" '$1==dev{printf "%d %d", $2, $10}' /proc/net/dev
printf '\n__AWP_UPTIME__\n'
uptime -p 2>/dev/null || uptime
printf '\n__AWP_IPV6__\n'
ip -6 addr show scope global 2>/dev/null | awk '/inet6/ && $2 !~ /^fd[0-9a-fA-F]{2}:/ {split($2, a, "/"); if (a[2] <= 64) print a[1]}'
"""


def collect_stats(ssh):
    ssh.connect()
    try:
        out, err, code = ssh.run_command(STATS_COMMAND)
        if code != 0:
            raise RuntimeError(err or f'SSH exit code {code}')
        return parse_stats(out)
    finally:
        ssh.disconnect()


def parse_stats(output):
    sections = {}
    current = None
    for line in output.splitlines():
        if line in {f'__AWP_{key}__' for key in ('CPU', 'RAM', 'DISK', 'NET', 'UPTIME', 'IPV6')}:
            current = line[6:-2].lower()
            sections[current] = []
        elif current:
            sections[current].append(line)
    values = {key: '\n'.join(lines).strip() for key, lines in sections.items()}
    stats = {}
    try:
        cpu = float(values.get('cpu', '').splitlines()[0])
        stats['cpu'] = round(cpu, 1) if math.isfinite(cpu) else 0
    except (ValueError, IndexError):
        stats['cpu'] = 0
    for key in ('ram', 'disk', 'net'):
        try:
            first, second = (int(value) for value in values.get(key, '').split())
        except ValueError:
            first, second = 0, 0
        if key == 'net':
            stats.update(net_rx=first, net_tx=second)
        else:
            stats.update({f'{key}_used': first, f'{key}_total': second,
                          f'{key}_percent': round(first / second * 100, 1) if second > 0 else 0})
    stats['uptime'] = values.get('uptime', '')
    stats['ipv6'] = [line.strip() for line in values.get('ipv6', '').splitlines() if line.strip()]
    return stats
