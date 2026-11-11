"""Read-only URMA connection inventory; no GPU context, module or service changes."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

LIMIT = 4 * 1024 * 1024


def read(path, limit=16384):
    try:
        with Path(path).open('rb', buffering=0) as stream:
            chunks = []
            size = 0
            while size <= limit:
                data = stream.read(min(4096, limit + 1 - size))
                if not data:
                    break
                chunks.append(data)
                size += len(data)
        raw = b''.join(chunks)
        return dict(text=raw[:limit].decode('utf8', 'replace'), truncated=len(raw) > limit)
    except OSError as exc:
        return dict(error=str(exc))


def command(args):
    try:
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, errors='replace', timeout=15)
        return dict(command=args, executable=shutil.which(args[0]), rc=result.returncode, stdout=result.stdout[:LIMIT],
                    stderr=result.stderr[:LIMIT], truncated=max(len(result.stdout), len(result.stderr)) > LIMIT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(command=args, error=str(exc))


def device_files(device):
    root = Path('/sys/class/uburma') / device
    result = dict(path=str(root), resolved=str(root.resolve()), present=root.exists(), files={})
    if not root.exists():
        return result
    # Walk this device only, not driver/device/subsystem symlinks to other trees.
    def visit(directory, depth):
        try:
            children = sorted(directory.iterdir())
        except OSError:
            return
        for path in children:
            if len(result['files']) >= 256 or path.is_symlink():
                continue
            if path.is_file():
                result['files'][str(path.relative_to(root))] = read(path)
            elif depth < 4 and path.is_dir():
                visit(path, depth + 1)
    visit(root, 0)
    return result


def snapshot(output, phase, device, ifname):
    output.mkdir(parents=True, exist_ok=True)
    result = dict(host=socket.gethostname(), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  kernel=os.uname().release, boot_id=read('/proc/sys/kernel/random/boot_id'),
                  uptime=read('/proc/uptime'), selected_device=device_files(device))
    commands = [
        ['ip', '-details', 'address', 'show'], ['ip', 'route', 'show'],
        ['ip', 'neigh', 'show'], ['ip', 'link', 'show', 'dev', ifname],
        ['urma_admin', '--help'], ['urma_admin', 'show'],
        ['lsmod'], ['fuser', '/dev/mxcd', '/dev/uburma/' + device],
        ['systemctl', 'list-units', '--all', '--type=service', '--no-pager', '--plain'],
        ['dmesg', '--color=never'],
    ]
    result['commands'] = [command(args) for args in commands]
    units = result['commands'][-2].get('stdout', '')
    services = sorted(set(re.findall(r'\b([\w@.-]*(?:urma|ubcore|ubmad|tpsa)[\w@.-]*\.service)\b', units, re.I)))
    result['service_status'] = [command(['systemctl', 'status', name, '--no-pager', '--full']) for name in services[:12]]
    result['modules'] = {name: dict(loaded=Path('/sys/module', name).exists(),
        srcversion=read(Path('/sys/module', name, 'srcversion')),
        installed=command(['modinfo', name])) for name in ('udma', 'ubcore', 'uburma', 'ummu', 'metax', 'udma_metax_p2p_bridge')}
    dmesg = result['commands'][-1]
    (output / ('dmesg_' + phase + '.txt')).write_text(dmesg.get('stdout', '') + dmesg.get('stderr', '') + dmesg.get('error', ''))
    (output / ('connection_' + phase + '.json')).write_text(json.dumps(result, indent=2))
    return result


def diagnose(output, run_root):
    benches = sorted(run_root.glob('normal/bench.log'))
    text = '\n'.join(p.read_text(errors='replace') for p in benches)
    before = output / 'dmesg_before.txt'
    after = output / 'dmesg_after.txt'
    old = before.read_text(errors='replace') if before.exists() else ''
    new = after.read_text(errors='replace') if after.exists() else ''
    appended = bool(old) and new.startswith(old)
    delta = new[len(old):] if appended else new
    (output / 'dmesg_delta.txt').write_text(delta)
    evidence = {name: [line for line in delta.splitlines() if pattern.search(line)][-12:]
        for name, pattern in [
            ('tp_exchange', re.compile('Failed to exchange tp info|Failed to send create req message|Session .*timeout')),
            ('mad_tx', re.compile('ubmad.*Tx status error|ubmad.*get tjetty failed|Invalid eid')),
            ('device_activation', re.compile('wait activate dev resp timeout|failed to activate ubase dev|udrv activate.*failed'))]}
    bind = 'urma_bind_jetty failed' in text
    result = dict(classification='URMA_BIND_FAILED' if bind else 'INSPECT_RUN_STATUS',
        primary_error=next((line for line in text.splitlines() if 'urma_bind_jetty failed' in line), None),
        dmesg_scope='appended_during_run' if appended else 'full_snapshot_no_exact_delta', evidence=evidence,
        note='TP/MAD evidence narrows the failure to connection setup. It does not prove reboot age, firmware failure, a specific port, or that trace activation is unrelated. Compare the no-trace baseline first.')
    (output / 'connection_diagnosis.json').write_text(json.dumps(result, indent=2))
    print('CONNECTION_DIAGNOSIS ' + result['classification'], flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('before', 'after'), required=True)
    parser.add_argument('--device', default='udmac0d1e2')
    parser.add_argument('--ifname', default='enp33s0')
    parser.add_argument('--run-root', type=Path)
    args = parser.parse_args()
    snapshot(args.output, args.phase, args.device, args.ifname)
    if args.phase == 'after' and args.run_root:
        diagnose(args.output, args.run_root)
