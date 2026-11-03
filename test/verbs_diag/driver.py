"""Two-node verbs affinity/chunk experiment; no URMA device required."""
import argparse
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
EXPECTED = {'test_internode.py': 'd679e4bdad167251dbc9d2c2f12634501b0f72db33231e1f1ce26d06ebac1b5f', 'buffer.py': '606d5a088fe5656aa6052862520c45a9092ca08bd1b7b3bd273683f9b549188c', 'utils.py': '5d9a7be095a3851ed300e67936bbad91ea75ef8c5ecf6e5bb87b0944441a5199'}
DEFAULT_SHA = '144f1fca126eb8971a5278c3baf0b44e1a29fb7b03ce51c928feb7abc19af798'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def pair(args, payload):
    end = time.monotonic() + 180
    if args.node_rank == 0:
        with socket.socket() as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.settimeout(180)
            server.bind(('', args.port))
            server.listen(1)
            connection, _ = server.accept()
    else:
        while True:
            try:
                connection = socket.create_connection((args.master_addr, args.port), timeout=3)
                break
            except OSError:
                if time.monotonic() >= end:
                    raise
                time.sleep(0.5)
    with connection:
        connection.settimeout(30)
        connection.sendall(json.dumps(payload).encode()+b'\n')
        raw = connection.makefile('rb').readline(65537)
        if len(raw) > 65536:
            raise RuntimeError('peer message too large')
        peer = json.loads(raw)
    if peer != payload or payload.get('rc') != 0:
        raise RuntimeError(f'PAIR_STOP local={payload!r} peer={peer!r}')

def capture(path, command, timeout=15):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    path.write_text(result.stdout + result.stderr)
    return result

def snapshot_host(root, hca):
    data = dict(host=socket.gethostname(), kernel=os.uname().release, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), parent_affinity=sorted(os.sched_getaffinity(0)))
    data['numa_cpulists'] = {str(p): p.read_text().strip() for p in Path('/sys/devices/system/node').glob('node*/cpulist')}
    data['hca_device'] = str((Path('/sys/class/infiniband')/hca/'device').resolve())
    root.joinpath('host.json').write_text(json.dumps(data, indent=2))
    for name, command in [('lscpu.txt', ['lscpu']), ('ibv_devinfo.txt', ['ibv_devinfo', '-d', hca])]:
        if shutil.which(command[0]):
            capture(root/name, command)

def idle_check(case, hca):
    gpu = capture(case/'gpu_before.txt', ['mx-smi'])
    if gpu.returncode or 'no process found' not in gpu.stdout or 'Available' not in gpu.stdout:
        raise RuntimeError('GPU_NOT_IDLE_OR_UNAVAILABLE: see gpu_before.txt')
    if not (Path('/sys/class/infiniband')/hca).is_dir():
        raise RuntimeError(f'VERBS_DEVICE_MISSING: {hca}')
    if not shutil.which('fuser'):
        raise RuntimeError('fuser is required for the device-idle check')
    devices = [Path('/dev/mxcd')]
    verbs = Path('/sys/class/infiniband')/hca/'device/infiniband_verbs'
    devices += [Path('/dev/infiniband')/p.name for p in verbs.glob('uverbs*')]
    if len(devices) == 1:
        raise RuntimeError('cannot resolve selected HCA uverbs device')
    for dev in devices:
        if not dev.exists():
            raise RuntimeError(f'DEVICE_MISSING: {dev}')
        result = subprocess.run(['fuser', str(dev)], capture_output=True, text=True, timeout=5)
        if result.returncode != 1:
            raise RuntimeError(f'DEVICE_BUSY_OR_CHECK_FAILED: {dev}: {result.stdout} {result.stderr}')
    stale = glob.glob(f'/dev/shm/uccl_barrier_*_uid{os.getuid()}_ht_th*')
    if stale:
        raise RuntimeError(f'EXISTING_BARRIER: {stale}; inspect ownership; no automatic deletion')

def counters(hca):
    result = {}
    for p in (Path('/sys/class/infiniband')/hca/'ports/1/counters').glob('*'):
        try:
            result[p.name] = p.read_text().strip()
        except OSError:
            pass
    return result

def terminate(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()

def main():
    task_root = Path(os.environ.get('VERBS_DIAG_HOME', str(Path.home()))).expanduser()
    dev_root = Path(os.environ.get('VERBS_DIAG_DEV_ROOT', str(task_root/'metax_dev'))).expanduser()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-rank', type=int, choices=[0, 1], required=True)
    parser.add_argument('--master-addr', required=True)
    parser.add_argument('--job', required=True, help='same unique identifier on both nodes')
    parser.add_argument('--port', type=int, default=31080, help='control port; torchrun uses port+1')
    parser.add_argument('--bench-dir', type=Path, default=task_root/'uccl-ep-xpu-integration/ep/bench')
    parser.add_argument('--module', type=Path)
    parser.add_argument('--module-sha', default=DEFAULT_SHA)
    parser.add_argument('--output', type=Path, default=dev_root/'verbs_diag_results')
    parser.add_argument('--check-only', action='store_true', help='check local paths, Python and ELF dependencies; do not run benchmark or contact peer')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--hca', default='rocep40s0f0')
    parser.add_argument('--ifname', default='enp33s0')
    parser.add_argument('--gpu', default='0')
    parser.add_argument('--case-timeout', type=int, default=1200)
    args = parser.parse_args()
    if not re.fullmatch('[a-zA-Z0-9_-]+', args.job) or not (1024 <= args.port < 65535) or not (1 <= args.repeats <= 10):
        parser.error('invalid job, port, or repeats')
    base = dev_root/'urma_after_kernel_20261029/diag'
    module = args.module or base/('ep_host_atomic_from_204_4ed19a6b' if args.node_rank==0 else 'ep_host_atomic_4ed19a6b')/'module/ep.abi3.so'
    if module.is_dir():
        module /= 'ep.abi3.so'
    if args.check_only:
        print(f'PYTHON={sys.executable}\nBENCH={args.bench_dir}\nMODULE={module}\nOUTPUT={args.output}', flush=True)
        if not module.is_file() or sha(module) != args.module_sha:
            raise RuntimeError(f'EXTENSION_MISSING_OR_SHA_MISMATCH: {module}')
        for filename, expected in EXPECTED.items():
            if sha(args.bench_dir/filename) != expected:
                raise RuntimeError(f'BENCH_SOURCE_CHANGED: {filename}')
        probe = '''import ctypes,importlib.util,os,pathlib,sys,types
runtime=pathlib.Path(os.environ.get('MACA_PATH','/opt/maca-3.7.0'))/'lib/libmcruntime.so'
if runtime.exists(): ctypes.CDLL(str(runtime),mode=os.RTLD_NOW|os.RTLD_GLOBAL)
import torch
print('PYTHON_REAL=',pathlib.Path(sys.executable).resolve())
print('TORCH=',torch.__version__)
print('TORCH_PATH=',torch.__file__)
package=types.ModuleType('uccl')
package.__path__=[]
sys.modules['uccl']=package
spec=importlib.util.spec_from_file_location('uccl.ep',sys.argv[1])
extension=importlib.util.module_from_spec(spec)
sys.modules['uccl.ep']=extension
spec.loader.exec_module(extension)
print('EXTENSION_IMPORT_OK=',pathlib.Path(extension.__file__).resolve())
maps=pathlib.Path('/proc/self/maps')
if maps.exists():
    print('LOADED_HOME_LIBRARIES=',sorted({line.split()[-1] for line in maps.read_text().splitlines() if '/home/' in line and '.so' in line}))
'''
        check = subprocess.run([sys.executable, '-c', probe, str(module.resolve())], text=True)
        if check.returncode:
            raise RuntimeError('selected Python cannot import MetaX torch/extension; inspect paths and runtime libraries')
        if shutil.which('ldd'):
            linked = subprocess.run(['ldd', str(module.resolve())], capture_output=True, text=True, timeout=15)
            print(linked.stdout + linked.stderr)
            if linked.returncode or 'not found' in linked.stdout:
                print('LDD_NOTE: unresolved standalone entries may be provided by the torch preload; extension import above is the load check')
        print('CHECK_ONLY_PASSED (no GPU benchmark; does not prove runtime ABI compatibility)')
        return
    root = args.output/args.job/f'node{args.node_rank}'
    root.mkdir(parents=True, exist_ok=False)
    error = None
    fingerprint = dict(job=args.job, repeats=args.repeats, hca=args.hca, ifname=args.ifname, sha=args.module_sha, gpu=args.gpu, bundle={p.name: sha(p) for p in HERE.glob('*.py')})
    try:
        if not module.is_file() or sha(module) != args.module_sha:
            raise RuntimeError(f'EXTENSION_MISSING_OR_SHA_MISMATCH: {module}')
        for filename, expected in EXPECTED.items():
            if sha(args.bench_dir/filename) != expected:
                raise RuntimeError(f'BENCH_SOURCE_CHANGED: {filename}; this bundle targets the uploaded baseline')
        source = (args.bench_dir/'test_internode.py').read_text()
        for old in ('for nvl_chunk_size in range(4, 45, 4):', 'for nvl_chunk_size in range(1, 8, 1):'):
            if source.count(old) != 1:
                raise RuntimeError('unexpected tuning-loop structure')
            source = source.replace(old, 'for nvl_chunk_size in (4,):')
        (root/'test_internode_diag.py').write_text(source)
        package = root/'python/uccl'
        package.mkdir(parents=True)
        (package/'__init__.py').write_text('')
        (package/'ep.abi3.so').symlink_to(module.resolve())
        snapshot_host(root, args.hca)
    except Exception as exc:
        error = str(exc)
    (root/'setup.json').write_text(json.dumps(dict(fingerprint=fingerprint, error=error, module=str(module), python=sys.executable, library_path=os.environ.get('LD_LIBRARY_PATH','')), indent=2))
    if error:
        print(f'SETUP_ERROR {error}', flush=True)
    pair(args, dict(stage='setup', meta=fingerprint, rc=1 if error else 0))
    if error:
        raise RuntimeError(error)
    for repeat in range(1, args.repeats+1):
        order = ('pinned', 'unpinned') if repeat % 2 else ('unpinned', 'pinned')
        for affinity in order:
            name = f'r{repeat}_{affinity}'
            case = root/name
            case.mkdir()
            rc = 0
            try:
                idle_check(case, args.hca)
            except Exception as exc:
                rc = 1
                (case/'preflight_error.txt').write_text(str(exc))
                print(str(exc), flush=True)
            pair(args, dict(stage='ready', job=args.job, case=name, rc=rc))
            env = os.environ.copy()
            for key in ('LD_PRELOAD', 'PYTHONPATH', 'UMDK_HOME', 'UCCL_PROJECT_DIAG', 'UCCL_URMA_TM', 'UCCL_URMA_DEV_NAME', 'UCCL_DIAG_EXIT_DELAY'):
                env.pop(key, None)
            env.update(UCCL_EP_TRANSPORT='verbs', UCCL_EP_VERBS_REG='dmabuf', UCCL_IB_HCA=args.hca, CUDA_VISIBLE_DEVICES=args.gpu, MCCL_IB_DISABLE='1', NCCL_IB_DISABLE='1', MCCL_SOCKET_IFNAME=args.ifname, NCCL_SOCKET_IFNAME=args.ifname, MCCL_DEBUG='INFO', NCCL_DEBUG='INFO', UCCL_EP_DISABLE_CPU_AFFINITY='0' if affinity=='pinned' else '1', UCCL_RDMA_ADAPTIVE_SLEEP='0', UCCL_METAX_URMA_HOST_ATOMICS='1', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', VERBS_DIAG_CASE=str(case.resolve()), VERBS_DIAG_BENCH=str(args.bench_dir.resolve()), VERBS_DIAG_SHA=args.module_sha, UCCL_TORCHRUN_NODE_RANK=str(args.node_rank), UCCL_EXIT_GUARD='1')
            for key in ('TMPDIR', 'XDG_CACHE_HOME'):
                directory = case/key.lower()
                directory.mkdir()
                env[key] = str(directory.resolve())
            (case/'counters_before.json').write_text(json.dumps(counters(args.hca)))
            command = [sys.executable, str(HERE/'exit_guard.py'), '--nnodes=2', '--nproc_per_node=1', f'--node_rank={args.node_rank}', f'--master_addr={args.master_addr}', f'--master_port={args.port+1}', '--no-python', sys.executable, '-u', str(HERE/'worker.py')]
            print(f'CASE_BEGIN {name} output={case}', flush=True)
            with (case/'bench.log').open('w') as log:
                process = subprocess.Popen(command, cwd=args.bench_dir, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    rc = process.wait(timeout=args.case_timeout)
                except subprocess.TimeoutExpired:
                    terminate(process)
                    rc = 124
                except KeyboardInterrupt:
                    terminate(process)
                    raise
            text = (case/'bench.log').read_text(errors='replace')
            checks = dict(correctness=text.count('[testing] Running with')==24, tuning=text.count('[tuning] SMs ')==22, metrics=text.count('DIAG_METRIC ')==22, done='DIAG_BENCH_DONE' in text, exit_ack=f'UCCL_EXIT_ACK_OK node_rank={args.node_rank}' in text, dmabuf='GPU MR registration path=dmabuf requested=dmabuf' in text, affinity=('Local CPU thread pinned to NUMA node' in text)==(affinity=='pinned'), no_errors=not re.search(r'Traceback \(most recent call last\)|AssertionError|Failed CR status|Segmentation fault|ChildFailedError|\[WARNING\]', text))
            if not all(checks.values()) and rc == 0:
                rc = 90
            (case/'status.json').write_text(json.dumps(dict(rc=rc, checks=checks), indent=2))
            (case/'counters_after.json').write_text(json.dumps(counters(args.hca)))
            print(f'CASE_END {name} rc={rc} checks={checks}', flush=True)
            pair(args, dict(stage='done', job=args.job, case=name, rc=rc))
    print(f'VERBS_DIAG_ALL_PASSED output={root}', flush=True)

if __name__ == '__main__':
    main()
