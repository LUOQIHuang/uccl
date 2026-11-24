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


def device_files(device, device_class='uburma'):
    root = Path('/sys/class') / device_class / device
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


def log_delta(old, new):
    """Keep ordering and duplicates when the kernel journal tail rotates."""
    if old and new.startswith(old):
        return new[len(old):], 'appended_during_run'
    before, after = old.splitlines(keepends=True), new.splitlines(keepends=True)
    for count in range(min(len(before), len(after)), 0, -1):
        if before[-count:] == after[:count]:
            return ''.join(after[count:]), 'overlapping_tail_added_lines'
    return new, 'full_snapshot_no_exact_delta'


def kernel_delta(snapshots, run_root):
    records = {}
    for phase in ('before', 'after'):
        path = snapshots / ('connection_' + phase + '.json')
        records[phase] = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    boot_ids = [records[p].get('boot_id', {}).get('text', '').strip() for p in ('before', 'after')]
    same_boot = all(boot_ids) and boot_ids[0] == boot_ids[1]
    errors = {}
    for kind in ('dmesg', 'journal'):
        texts, usable = {}, True
        for phase in ('before', 'after'):
            data = records[phase]
            record = next((c for c in data.get('commands', []) if c.get('command', [None])[0] == 'dmesg'), None) if kind == 'dmesg' else data.get('kernel_journal')
            path = snapshots / (kind + '_' + phase + '.txt')
            raw = path.read_text(encoding='utf-8', errors='replace') if path.exists() else ''
            if record is not None:
                valid = record.get('rc') == 0 and not record.get('truncated', False)
                # stderr is never kernel log content, even when both snapshots match.
                raw = record.get('stdout', '') if valid else ''
            else:
                valid = (path.exists() or phase == 'before') and not re.search('Operation not permitted|read kernel buffer failed', raw)
            if not valid:
                usable = False
                errors[kind + '_' + phase] = record or dict(error='missing or unreadable log')
            texts[phase] = raw
        if usable:
            delta, scope = log_delta(texts['before'], texts['after'])
            if all(boot_ids) and not same_boot:
                delta, scope = texts['after'], 'different_boot_no_run_delta'
            return dict(source=kind, scope=scope, text=delta, collection_errors=errors)
    # Earlier benchmark runs retained journalctl tails despite dmesg permissions.
    paths = [run_root/'normal'/('kernel_' + p + '.txt') for p in ('before', 'after')]
    if all(p.exists() for p in paths):
        old, new = [p.read_text(encoding='utf-8', errors='replace') for p in paths]
        if 'kernel:' in old and 'kernel:' in new:
            delta, scope = log_delta(old, new)
            if not same_boot:
                delta, scope = new, 'journal_tail_boot_unverified'
            return dict(source='benchmark_journal_tail', scope=scope, text=delta, collection_errors=errors)
    return dict(source=None, scope='kernel_log_unavailable', text='', collection_errors=errors)


def ummu_matches(delta, run_root, peer_root=None):
    registrations = []
    roots = [run_root] + ([peer_root] if peer_root else [])
    for root in roots:
        path = root/'normal/bench.log'
        if not path.exists():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for match in re.finditer(r'\[URMA_REG\][^\n]*?thread=(\d+) stage=begin kind=(\w+) va=(0x[0-9a-fA-F]+) len=(\d+) atomic=(\d+)', text):
            thread, kind, va, size, atomic = match.groups()
            registrations.append(dict(node=root.name, thread=int(thread), kind=kind,
                                      va=int(va, 16), size=int(size), atomic=bool(int(atomic)),
                                      source=str(path)))
    lines, events = delta.splitlines(), []
    for i, line in enumerate(lines):
        event = re.search(r'ummu.*event (0x[0-9a-fA-F]+) received:', line)
        if not event:
            continue
        words = []
        for raw in lines[i+1:i+9]:
            word = re.search(r'kernel:.*ummu.*:\s+(0x[0-9a-fA-F]+)\s*$', raw)
            if not word:
                break
            words.append(word.group(1))
        matches = []
        for index, word in enumerate(words):
            value = int(word, 16)
            for reg in registrations:
                if reg['va'] <= value < reg['va'] + reg['size']:
                    matches.append(dict(word_index=index, word=word, registration=reg,
                                        owner='local' if reg['node'] == run_root.name else 'peer'))
        events.append(dict(header=line, code=event.group(1), raw_words=words, matches=matches))
    return events


def pci_inventory(gpu_pci, atomic_port):
    endpoint = Path('/sys/bus/pci/devices') / gpu_pci
    resolved = endpoint.resolve()
    ancestors = [part for part in reversed(resolved.parts[:-1])
                 if re.fullmatch(r'[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]', part)]
    port = ancestors[0] if atomic_port == 'auto' and ancestors else atomic_port
    result = dict(gpu_pci=gpu_pci, present=endpoint.exists(), gpu_path=str(resolved),
                  gpu_class=read(endpoint / 'class'), gpu_vendor=read(endpoint / 'vendor'),
                  runtime_status=read(endpoint / 'power/runtime_status'), ancestors=ancestors,
                  atomic_port=port, atomic_port_is_ancestor=port in ancestors,
                  lspci=[command(['lspci', '-s', pci, '-vv']) for pci in [gpu_pci] + ancestors])
    if port != 'auto':
        result['atomic_control'] = command(['setpci', '-s', port, 'CAP_EXP+28.w'])
    return result


def snapshot(output, phase, device, ifname, gpu_pci=None, atomic_port='auto'):
    output.mkdir(parents=True, exist_ok=True)
    result = dict(host=socket.gethostname(), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  kernel=os.uname().release, boot_id=read('/proc/sys/kernel/random/boot_id'),
                  uptime=read('/proc/uptime'), selected_device=device_files(device))
    result['selected_ifname'] = ifname
    result['selected_ubcore_device'] = device_files(device, 'ubcore')
    if gpu_pci:
        result['pci'] = pci_inventory(gpu_pci, atomic_port)
    commands = [
        ['ip', '-details', 'address', 'show'], ['ip', 'route', 'show'],
        ['ip', 'neigh', 'show'], ['ip', 'link', 'show', 'dev', ifname],
        ['urma_admin', '--help'], ['urma_admin', 'show'],
        ['lsmod'], ['fuser', '/dev/mxcd', '/dev/uburma/' + device],
        ['systemctl', 'list-units', '--all', '--type=service', '--no-pager', '--plain'],
        ['dmesg', '--color=never'],
    ]
    result['commands'] = [command(args) for args in commands]
    # The default show command can hide the physical device behind bonding.
    result['physical_device_queries'] = [command(args) for args in (
        ['urma_admin', 'show', '--all'], ['urma_admin', 'show', '--dev', device])]
    result['kernel_journal'] = command(['journalctl', '-b', '-k', '-q', '--no-pager', '-n', '2000', '-o', 'short-monotonic'])
    (output / ('journal_' + phase + '.txt')).write_text(result['kernel_journal'].get('stdout', ''), encoding='utf-8')
    units = result['commands'][-2].get('stdout', '')
    services = sorted(set(re.findall(r'\b([\w@.-]*(?:urma|ubcore|ubmad|tpsa)[\w@.-]*\.service)\b', units, re.I)))
    result['service_status'] = [command(['systemctl', 'status', name, '--no-pager', '--full']) for name in services[:12]]
    result['modules'] = {name: dict(loaded=Path('/sys/module', name).exists(),
        srcversion=read(Path('/sys/module', name, 'srcversion')),
        installed=command(['modinfo', name])) for name in ('udma', 'ubcore', 'uburma', 'ummu', 'metax', 'udma_metax_p2p_bridge')}
    dmesg = result['commands'][-1]
    (output / ('dmesg_' + phase + '.txt')).write_text(dmesg.get('stdout', ''), encoding='utf-8')
    (output / ('connection_' + phase + '.json')).write_text(json.dumps(result, indent=2))
    return result


def diagnose(output, run_root, snapshot_root=None, peer_root=None):
    output.mkdir(parents=True, exist_ok=True)
    benches = sorted(run_root.glob('normal/bench.log'))
    text = '\n'.join(p.read_text(encoding='utf-8', errors='replace') for p in benches)
    kernel = kernel_delta(snapshot_root or output, run_root)
    delta = kernel.pop('text')
    (output / 'kernel_delta.txt').write_text(delta, encoding='utf-8')
    (output / 'dmesg_delta.txt').write_text(delta if kernel['source']=='dmesg' else '', encoding='utf-8')
    evidence = {name: [line for line in delta.splitlines() if pattern.search(line)][-12:]
        for name, pattern in [
            ('tp_exchange', re.compile('Failed to exchange tp info|Failed to send create req message|Session .*timeout')),
            ('mad_tx', re.compile('ubmad.*Tx status error|ubmad.*get tjetty failed|Invalid eid')),
            ('device_activation', re.compile('wait activate dev resp timeout|failed to activate ubase dev|udrv activate.*failed')),
            ('ummu', re.compile(r'ummu.*event 0x[0-9a-fA-F]+ received')),
            ('tp_events', re.compile('trigger TP level AE|udma_ae_tp_level_error'))]}
    bind = 'urma_bind_jetty failed' in text
    completion = next((line for line in text.splitlines() if '[URMA_COMPLETION_ERROR]' in line), None)
    fields = dict(re.findall(r'(\w+)=(\d+)', completion)) if completion else {}
    ack_timeout = fields.get('status') == '9'
    internal_atomic = fields.get('wr_id') == str(2**64-1) and fields.get('atomic') == '1'
    cpu_timeout = 'DeepEP error: timeout (dispatch CPU)' in text
    result = dict(classification='URMA_BIND_FAILED' if bind else 'INSPECT_RUN_STATUS',
        primary_error=next((line for line in text.splitlines() if 'urma_bind_jetty failed' in line), None),
        dmesg_scope=kernel['scope'] if kernel['source']=='dmesg' else 'unavailable_see_kernel_log',
        kernel_log=kernel, evidence=evidence, ummu_events=ummu_matches(delta, run_root, peer_root),
        note='TP/MAD evidence narrows the failure to connection setup. It does not prove reboot age, firmware failure, a specific port, or that trace activation is unrelated. Compare the no-trace baseline first.')
    if completion:
        result.update(classification='URMA_ACK_TIMEOUT' if ack_timeout else 'URMA_COMPLETION_FAILED',
                      primary_error=completion, completion=fields, internal_control_atomic=internal_atomic,
                      initial_barrier_candidate=internal_atomic and fields.get('token') == '65536' and
                          '[testing] Running with BF16, without top-k (async=False, previous=False)' in text,
                      note='SDK status=9 is ACK timeout (retry limit exceeded). The UINT64_MAX WR id denotes internal control, not a measured tail FADD. In the first Dispatch prepare, code submits a host-memory arrival-barrier FADD. Check both peers, URMA EID/TP and run-scoped driver errors; IP ping/NCCL bootstrap do not validate this path. GPU P2P or fence causality is not established.')
    elif cpu_timeout:
        result.update(classification='DISPATCH_CPU_TIMEOUT_CHECK_PEER',
                      primary_error=next(line for line in text.splitlines() if 'DeepEP error: timeout (dispatch CPU)' in line),
                      note='The peer may have failed first. Compare its first URMA completion error before attributing this CPU wait to GPU performance.')
    (output / 'connection_diagnosis.json').write_text(json.dumps(result, indent=2))
    print('CONNECTION_DIAGNOSIS ' + result['classification'], flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--phase', choices=('before', 'after'))
    parser.add_argument('--analyze-only', action='store_true', help='analyze existing files; no commands or device access')
    parser.add_argument('--snapshot-root', type=Path)
    parser.add_argument('--peer-root', type=Path)
    parser.add_argument('--device', default='udmac0d1e2')
    parser.add_argument('--ifname', default='enp33s0')
    parser.add_argument('--gpu-pci')
    parser.add_argument('--atomic-port', default='auto')
    parser.add_argument('--run-root', type=Path)
    args = parser.parse_args()
    if args.analyze_only:
        if not args.run_root:
            parser.error('--analyze-only requires --run-root')
    else:
        if not args.phase:
            parser.error('--phase required for snapshot')
        snapshot(args.output, args.phase, args.device, args.ifname, args.gpu_pci, args.atomic_port)
    if args.analyze_only or (args.phase == 'after' and args.run_root):
        diagnose(args.output, args.run_root, args.snapshot_root, args.peer_root)
