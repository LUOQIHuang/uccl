"""URMA checks and thread-start affinity; no torch dependency."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

URMA_SHA = '5826d74b98e5f80843ad419e09dc15737a79aa9f3d3854405b553da56db1fe2e'

def parse_cpus(value):
    cpus = [int(part) for part in value.split(',')]
    if not cpus or len(cpus) != len(set(cpus)) or min(cpus) < 0:
        raise ValueError('CPU list must contain distinct nonnegative CPU numbers')
    return cpus

def prepare_utils(source):
    needle = '            proxy.start_dual()'
    if source.count(needle) != 1:
        raise ValueError('unexpected proxy start loop in utils.py')
    return source.replace(needle, '            _diag_start_proxy(proxy)')

def library_manifest(directory):
    import hashlib
    if not directory.is_dir() or not list(directory.glob('liburma.so*')):
        raise ValueError(f'PRIVATE_UMDK_MISSING: {directory}')
    cache, result = {}, {}
    for path in sorted(directory.glob('*.so*')):
        if path.is_file():
            actual = path.resolve()
            if actual not in cache:
                cache[actual] = hashlib.sha256(actual.read_bytes()).hexdigest()
            result[path.name] = cache[actual]
    return result

def hardware_check(args):
    kernel = os.uname().release
    if kernel != args.expected_kernel:
        raise RuntimeError(f'URMA_KERNEL_MISMATCH: actual={kernel}, expected={args.expected_kernel}')
    device = Path('/sys/class/uburma')/args.urma_device
    if not device.exists() or not (Path('/dev/uburma')/args.urma_device).exists():
        raise RuntimeError(f'URMA_DEVICE_MISSING: {args.urma_device}')
    if not Path('/sys/module/udma_metax_p2p_bridge').is_dir():
        raise RuntimeError('URMA_GPU_P2P_BRIDGE_MISSING')
    runpm = Path('/sys/module/metax/parameters/runpm_link').read_text().strip()
    if runpm != '0':
        raise RuntimeError(f'RUNPM_LINK_MUST_BE_ZERO: actual={runpm}')
    runtime = (Path('/sys/bus/pci/devices')/args.gpu_pci/'power/runtime_status').read_text().strip()
    if runtime not in ('active', 'suspended'):
        raise RuntimeError(f'GPU_RUNTIME_PM_UNEXPECTED: {runtime}')
    result = subprocess.run(['setpci', '-s', args.atomic_port, 'CAP_EXP+28.w'], capture_output=True, text=True, timeout=5)
    value = result.stdout.strip()
    if result.returncode or not re.fullmatch('[0-9a-fA-F]{4}', value) or not (int(value, 16) & 0x40):
        raise RuntimeError(f'PCIE_ATOMIC_REQUEST_NOT_ENABLED: port={args.atomic_port} value={value!r}')
    journal = subprocess.run(['journalctl','-b','-k','-q','--no-pager'], capture_output=True, text=True, timeout=15)
    if journal.returncode:
        raise RuntimeError('KERNEL_JOURNAL_UNAVAILABLE')
    errors = [line for line in journal.stdout.splitlines() if re.search('kill queue timeout|destroy ce ringbuf failed|results in a decrease in the number of available ringbufs', line)]
    if errors:
        raise RuntimeError(f'GPU_QUEUE_ERRORS_IN_THIS_BOOT: {errors[-5:]}')
    queue = Path('/sys/kernel/debug/mxcd/ceq')
    if queue.is_file():
        for line in queue.read_text().splitlines():
            parts = line.split()
            if len(parts)==3 and parts[0].isdecimal() and parts[1].isdecimal() and parts[2]!='0x0':
                raise RuntimeError(f'GPU_NONZERO_QUEUE_STATUS: {line}')
    return dict(kernel=kernel, device=args.urma_device, device_path=str(device.resolve()), runpm_link=runpm, gpu_pci=args.gpu_pci, runtime_status=runtime, atomic_port=args.atomic_port, atomic_control=value)

def counters(args):
    result = dict(device=args.urma_device, note='shared UB port, raw flit counts; not process-isolated')
    if not shutil.which('ubctl'):
        return dict(result, error='ubctl not found')
    chip, device, port = args.ubctl_selector.split(',')
    command = ['ubctl','-c',chip,'-d',device,'-m','dl','-p',port,'-f','pkt_stats']
    try:
        completed = subprocess.run(command,capture_output=True,text=True,timeout=10)
        result.update(command=command, rc=completed.returncode, output=completed.stdout, stderr=completed.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result['error'] = str(exc)
    return result

class ProxyAffinity:
    """New std::threads inherit the initiating thread's temporary CPU mask.

    This also affects helpers born inside that Proxy before they change their
    own affinity. Every surviving thread observed at start is checked later;
    ambiguous affinity overrides fail instead of silently labelling a case.
    """
    def __init__(self, cpus, pinned, output):
        self.cpus, self.pinned, self.output = cpus, pinned, Path(output)
        self.groups = []

    def start(self, proxy):
        index = len(self.groups)
        if index >= len(self.cpus):
            raise RuntimeError('more Proxy threads than requested CPUs')
        before = {int(p.name) for p in Path('/proc/self/task').iterdir()}
        original = os.sched_getaffinity(0)
        target = self.cpus[index]
        try:
            if self.pinned:
                os.sched_setaffinity(0, {target})
            proxy.start_dual()
        finally:
            os.sched_setaffinity(0, original)
        created = sorted({int(p.name) for p in Path('/proc/self/task').iterdir()} - before)
        if not created:
            raise RuntimeError('cannot observe the newly started native Proxy thread')
        group = dict(proxy_index=index, target_cpu=target if self.pinned else None, parent_before=sorted(original), parent_after=sorted(os.sched_getaffinity(0)), new_tids=created)
        self.groups.append(group)
        with self.output.open('a') as out:
            out.write(json.dumps(group)+'\n')
        print('DIAG_PROXY_START '+json.dumps(group), flush=True)

    def verify(self, threads):
        if len(self.groups) != len(self.cpus):
            raise RuntimeError('not all expected Proxy threads were started')
        lookup = {thread['tid']: thread for thread in threads}
        for group in self.groups:
            live = [lookup[tid] for tid in group['new_tids'] if tid in lookup]
            if not live:
                raise RuntimeError('observed Proxy thread group disappeared during benchmark')
            if self.pinned and any(t['allowed'] != str(group['target_cpu']) for t in live):
                raise RuntimeError(f'URMA_AFFINITY_NOT_VERIFIED: group={group} live={live}')
        print('DIAG_URMA_AFFINITY_OK', 'pinned' if self.pinned else 'inherited', flush=True)
