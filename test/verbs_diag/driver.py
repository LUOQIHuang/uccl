"""Two-node Normal affinity/chunk diagnostics for verbs or URMA."""
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
import urma_support
import profile_support
import transport_support
import tempfile

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

def snapshot_host(root, hca, backend='verbs', device='udmac0d1e2'):
    data = dict(host=socket.gethostname(), kernel=os.uname().release, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), parent_affinity=sorted(os.sched_getaffinity(0)))
    data['numa_cpulists'] = {str(p): p.read_text().strip() for p in Path('/sys/devices/system/node').glob('node*/cpulist')}
    data['backend'] = backend
    if backend == 'urma':
        data['urma_device'] = device
        data['urma_device_path'] = str((Path('/sys/class/uburma')/device).resolve())
    else:
        data['hca_device'] = str((Path('/sys/class/infiniband')/hca/'device').resolve())
    root.joinpath('host.json').write_text(json.dumps(data, indent=2))
    for name, command in [('lscpu.txt', ['lscpu']), ('ibv_devinfo.txt', ['ibv_devinfo', '-d', hca])]:
        if shutil.which(command[0]) and (name != 'ibv_devinfo.txt' or backend == 'verbs'):
            capture(root/name, command)

def idle_check(case, hca, args=None):
    gpu = capture(case/'gpu_before.txt', ['mx-smi'])
    if gpu.returncode or 'no process found' not in gpu.stdout or 'Available' not in gpu.stdout:
        raise RuntimeError('GPU_NOT_IDLE_OR_UNAVAILABLE: see gpu_before.txt')
    is_urma = args is not None and args.backend == 'urma'
    if is_urma:
        state = urma_support.hardware_check(args)
        (case/'urma_preflight.json').write_text(json.dumps(state, indent=2))
    elif not (Path('/sys/class/infiniband')/hca).is_dir():
        raise RuntimeError(f'VERBS_DEVICE_MISSING: {hca}')
    if not shutil.which('fuser'):
        raise RuntimeError('fuser is required for the device-idle check')
    devices = [Path('/dev/mxcd')]
    if is_urma:
        devices.append(Path('/dev/uburma')/args.urma_device)
    else:
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
    parser.add_argument('--backend', choices=('verbs','urma'), default='verbs')
    parser.add_argument('--node-rank', type=int, choices=[0, 1], required=True)
    parser.add_argument('--master-addr', required=True)
    parser.add_argument('--job', required=True, help='same unique identifier on both nodes')
    parser.add_argument('--port', type=int, help='control port; default 31080 for verbs, 31180 for URMA; torchrun uses port+1')
    parser.add_argument('--bench-dir', type=Path, default=task_root/'uccl-ep-xpu-integration/ep/bench')
    parser.add_argument('--module', type=Path)
    parser.add_argument('--module-sha', help='defaults to the historical private extension hash for the selected backend')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check-only', action='store_true', help='check local paths, Python and ELF dependencies; do not run benchmark or contact peer')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--suite', choices=('scan','focused','boundaries'), default='scan', help='focused: dispatch32/combine16; boundaries adds combine12')
    parser.add_argument('--affinity-mode', choices=('both','unpinned','pinned'), default='both')
    parser.add_argument('--profile-cpu', action='store_true', help='URMA focused only: sample native Proxy threads during BF16 dispatch')
    parser.add_argument('--perf-callgraph', choices=('dwarf','fp','none'), default='dwarf')
    parser.add_argument('--trace-transport', action='store_true', help='URMA boundaries only: separate API-count/timing passes with a host-built interposer')
    parser.add_argument('--trace-iterations', type=int, default=30)
    parser.add_argument('--urma-include-dir', type=Path, default=os.environ.get('URMA_DIAG_INCLUDE_DIR'))
    parser.add_argument('--host-cxx', default=os.environ.get('URMA_DIAG_HOST_CXX','g++'))
    parser.add_argument('--hca', default='rocep40s0f0')
    parser.add_argument('--ifname', default='enp33s0')
    parser.add_argument('--gpu', default='0')
    parser.add_argument('--case-timeout', type=int, default=1200)
    parser.add_argument('--urma-device', default='udmac0d1e2')
    parser.add_argument('--urma-cpus', default='4,5,6,7', help='explicit Proxy-start CPUs for URMA pinned cases')
    parser.add_argument('--urma-lib-dir', type=Path, default=Path(os.environ.get('VERBS_DIAG_LIBRARY_DIR', str(dev_root/'urma_after_kernel_20261029/diag/umdk_private_618957c3/lib'))))
    parser.add_argument('--expected-kernel', default='6.6.0-gdr_f00955680_diag', help='URMA baseline kernel check; no old boot-ID binding')
    parser.add_argument('--gpu-pci', default='0000:a1:00.0')
    parser.add_argument('--atomic-port', default='0000:a0:08.0')
    parser.add_argument('--ubctl-selector', default='0,1,1', help='UB chip,device,port counters selector')
    args = parser.parse_args()
    if args.profile_cpu and (args.backend!='urma' or args.suite!='focused'):
        parser.error('--profile-cpu requires --backend urma --suite focused')
    if args.trace_transport and (args.backend!='urma' or args.suite!='boundaries' or args.profile_cpu):
        parser.error('--trace-transport requires URMA boundaries, without --profile-cpu')
    if not 1 <= args.trace_iterations <= 200:
        parser.error('--trace-iterations must be 1..200')
    expected_metrics = len(profile_support.plan(args.suite))
    args.module_sha = args.module_sha or (urma_support.URMA_SHA if args.backend=='urma' else DEFAULT_SHA)
    args.output = args.output or dev_root/f'{args.backend}_diag_results'
    args.port = args.port if args.port is not None else (31180 if args.backend=='urma' else 31080)
    if not re.fullmatch(r'\d+,\d+,\d+', args.ubctl_selector):
        parser.error('invalid --ubctl-selector')
    if not re.fullmatch('[a-zA-Z0-9_-]+', args.job) or not (1024 <= args.port < 65535) or not (1 <= args.repeats <= 10):
        parser.error('invalid job, port, or repeats')
    base = dev_root/'urma_after_kernel_20261029/diag'
    default_module = ('ep_native_relax_from_204_4f3b30a6' if args.node_rank==0 else 'ep_native_relax_4f3b30a6') if args.backend=='urma' else ('ep_host_atomic_from_204_4ed19a6b' if args.node_rank==0 else 'ep_host_atomic_4ed19a6b')
    module = args.module or base/default_module/'module/ep.abi3.so'
    if module.is_dir():
        module /= 'ep.abi3.so'
    if args.backend=='urma':
        try:
            urma_support.parse_cpus(args.urma_cpus)
        except ValueError as exc:
            parser.error(str(exc))
        os.environ['LD_LIBRARY_PATH'] = str(args.urma_lib_dir.resolve()) + ':' + os.environ.get('LD_LIBRARY_PATH','')
        os.environ['URMA_DIAG_LIBRARY_DIR'] = str(args.urma_lib_dir.resolve())
        os.environ['URMA_DIAG_CPUS'] = args.urma_cpus
        os.environ['UCCL_EP_TRANSPORT'] = 'urma'
        os.environ['UCCL_URMA_TM'] = 'rc'
        os.environ['UCCL_URMA_DEV_NAME'] = args.urma_device
        os.environ.pop('UCCL_METAX_URMA_HOST_ATOMICS', None)
    else:
        os.environ['UCCL_EP_TRANSPORT'] = 'verbs'
    if args.check_only:
        print(f'PYTHON={sys.executable}\nBENCH={args.bench_dir}\nMODULE={module}\nOUTPUT={args.output}', flush=True)
        if not module.is_file() or sha(module) != args.module_sha:
            raise RuntimeError(f'EXTENSION_MISSING_OR_SHA_MISMATCH: {module}')
        for filename, expected in EXPECTED.items():
            if sha(args.bench_dir/filename) != expected:
                raise RuntimeError(f'BENCH_SOURCE_CHANGED: {filename}')
        if args.backend=='urma':
            print('URMA_HARDWARE', json.dumps(urma_support.hardware_check(args)), flush=True)
            print('URMA_LIBRARY_SHA', json.dumps(urma_support.library_manifest(args.urma_lib_dir)), flush=True)
        if args.profile_cpu:
            print('PERF_CAPABILITY', json.dumps(profile_support.capability_check(args.perf_callgraph)), flush=True)
        trace_temporary = None
        if args.trace_transport:
            trace_temporary = tempfile.TemporaryDirectory(prefix='uccl_trace_check_')
            trace_library, manifest, _ = transport_support.build(trace_temporary.name,args.urma_lib_dir,args.urma_include_dir,args.host_cxx)
            print('TRACE_BUILD_SELFTEST_OK',json.dumps(manifest),flush=True)
            os.environ['LD_PRELOAD']=str(trace_library)
            os.environ['DIAG_TRACE_LIBRARY']=str(trace_library)
            os.environ['UCCL_URMA_TRACE_REAL_LIB']=str((args.urma_lib_dir/'liburma.so').resolve())
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
    if os.environ.get('UCCL_EP_TRANSPORT')=='urma':
        libraries=[pathlib.Path(line.split()[-1]).resolve() for line in maps.read_text().splitlines() if '/liburma.so' in line]
        expected=pathlib.Path(os.environ['URMA_DIAG_LIBRARY_DIR']).resolve()
        assert libraries and all(path.is_relative_to(expected) for path in libraries), 'unexpected liburma: '+str(libraries)
        assert extension.get_num_proxy_threads()==len(os.environ['URMA_DIAG_CPUS'].split(',')), 'CPU list does not match Proxy count'
if os.environ.get('DIAG_TRACE_LIBRARY'):
    tracer=ctypes.CDLL(os.environ['DIAG_TRACE_LIBRARY'])
    assert tracer.uccl_trace_ready()==1, 'trace resolver failed'
    tracer.uccl_trace_real_path.restype=ctypes.c_char_p
    real=pathlib.Path(tracer.uccl_trace_real_path().decode()).resolve()
    assert real.is_relative_to(pathlib.Path(os.environ['URMA_DIAG_LIBRARY_DIR']).resolve()), str(real)
    print('TRACE_REAL_LIBRARY_OK=',real)
'''
        check = subprocess.run([sys.executable, '-c', probe, str(module.resolve())], text=True)
        if check.returncode:
            raise RuntimeError('selected Python cannot import MetaX torch/extension; inspect paths and runtime libraries')
        if trace_temporary is not None:
            os.environ.pop('LD_PRELOAD',None)
            os.environ.pop('DIAG_TRACE_LIBRARY',None)
            trace_temporary.cleanup()
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
    fingerprint = dict(job=args.job, backend=args.backend, repeats=args.repeats, hca=args.hca if args.backend=='verbs' else None, ifname=args.ifname, sha=args.module_sha, gpu=args.gpu, bundle={p.name: sha(p) for p in HERE.glob('*.py')})
    fingerprint.update(suite=args.suite, affinity_mode=args.affinity_mode, profile_cpu=args.profile_cpu, perf_callgraph=args.perf_callgraph if args.profile_cpu else None)
    fingerprint.update(trace_transport=args.trace_transport,trace_iterations=args.trace_iterations if args.suite=='boundaries' else None)
    try:
        if not module.is_file() or sha(module) != args.module_sha:
            raise RuntimeError(f'EXTENSION_MISSING_OR_SHA_MISMATCH: {module}')
        for filename, expected in EXPECTED.items():
            if sha(args.bench_dir/filename) != expected:
                raise RuntimeError(f'BENCH_SOURCE_CHANGED: {filename}; this bundle targets the uploaded baseline')
        if args.backend=='urma':
            fingerprint['urma'] = dict(device=args.urma_device, cpus=urma_support.parse_cpus(args.urma_cpus), kernel=args.expected_kernel, libraries=urma_support.library_manifest(args.urma_lib_dir))
            state = urma_support.hardware_check(args)
            (root/'urma_preflight.json').write_text(json.dumps(state, indent=2))
            private_utils = root/'diagnostic_utils'
            private_utils.mkdir()
            (private_utils/'utils.py').write_text(urma_support.prepare_utils((args.bench_dir/'utils.py').read_text()))
        source = (args.bench_dir/'test_internode.py').read_text()
        source = profile_support.prepare_benchmark(source, args.suite)
        (root/'test_internode_diag.py').write_text(source)
        if args.profile_cpu:
            (root/'perf_capability.json').write_text(json.dumps(profile_support.capability_check(args.perf_callgraph), indent=2))
        if args.trace_transport:
            trace_library, _, fingerprint['trace_build'] = transport_support.build(root/'transport_build',args.urma_lib_dir,args.urma_include_dir,args.host_cxx)
        package = root/'python/uccl'
        package.mkdir(parents=True)
        (package/'__init__.py').write_text('')
        (package/'ep.abi3.so').symlink_to(module.resolve())
        snapshot_host(root, args.hca, args.backend, args.urma_device)
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
        if args.backend=='urma':
            order = tuple(reversed(order))  # Confirm the historical inherited-affinity baseline first.
        if args.affinity_mode!='both':
            order = (args.affinity_mode,)
        for affinity in order:
            name = f'r{repeat}_{affinity}'
            case = root/name
            case.mkdir()
            rc = 0
            try:
                idle_check(case, args.hca, args)
            except Exception as exc:
                rc = 1
                (case/'preflight_error.txt').write_text(str(exc))
                print(str(exc), flush=True)
            pair(args, dict(stage='ready', job=args.job, case=name, rc=rc))
            env = os.environ.copy()
            env.update(DIAG_SUITE=args.suite, DIAG_PROFILE_CPU='1' if args.profile_cpu else '0', DIAG_PERF_CALLGRAPH=args.perf_callgraph)
            env['DIAG_TRACE_ITERATIONS']=str(args.trace_iterations)
            for key in ('LD_PRELOAD', 'PYTHONPATH', 'UMDK_HOME', 'UCCL_PROJECT_DIAG', 'UCCL_URMA_TM', 'UCCL_URMA_DEV_NAME', 'UCCL_DIAG_EXIT_DELAY'):
                env.pop(key, None)
            env.pop('DIAG_TRACE_LIBRARY',None)
            env.pop('UCCL_URMA_TRACE_REAL_LIB',None)
            if args.trace_transport:
                env.update(LD_PRELOAD=str(trace_library),DIAG_TRACE_LIBRARY=str(trace_library),UCCL_URMA_TRACE_REAL_LIB=str((args.urma_lib_dir/'liburma.so').resolve()),DIAG_TRACE_ITERATIONS=str(args.trace_iterations))
            env.update(UCCL_EP_TRANSPORT='verbs', UCCL_EP_VERBS_REG='dmabuf', UCCL_IB_HCA=args.hca, CUDA_VISIBLE_DEVICES=args.gpu, MCCL_IB_DISABLE='1', NCCL_IB_DISABLE='1', MCCL_SOCKET_IFNAME=args.ifname, NCCL_SOCKET_IFNAME=args.ifname, MCCL_DEBUG='INFO', NCCL_DEBUG='INFO', UCCL_EP_DISABLE_CPU_AFFINITY='0' if affinity=='pinned' else '1', UCCL_RDMA_ADAPTIVE_SLEEP='0', UCCL_METAX_URMA_HOST_ATOMICS='1', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1', VERBS_DIAG_CASE=str(case.resolve()), VERBS_DIAG_BENCH=str(args.bench_dir.resolve()), VERBS_DIAG_SHA=args.module_sha, UCCL_TORCHRUN_NODE_RANK=str(args.node_rank), UCCL_EXIT_GUARD='1')
            if args.backend=='urma':
                for key in ('UCCL_EP_VERBS_REG','UCCL_IB_HCA','UCCL_METAX_URMA_HOST_ATOMICS'):
                    env.pop(key, None)
                env.update(UCCL_EP_TRANSPORT='urma', UCCL_URMA_TM='rc', UCCL_URMA_DEV_NAME=args.urma_device, UCCL_EP_DISABLE_CPU_AFFINITY='1', URMA_DIAG_CPUS=args.urma_cpus, URMA_DIAG_PINNED='1' if affinity=='pinned' else '0')
            for key in ('TMPDIR', 'XDG_CACHE_HOME'):
                directory = case/key.lower()
                directory.mkdir()
                env[key] = str(directory.resolve())
            (case/'counters_before.json').write_text(json.dumps(urma_support.counters(args) if args.backend=='urma' else counters(args.hca)))
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
            checks = dict(correctness=text.count('[testing] Running with')==24, tuning=text.count('[tuning] SMs ')==expected_metrics, metrics=text.count('DIAG_METRIC ')==expected_metrics, done='DIAG_BENCH_DONE' in text, exit_ack=f'UCCL_EXIT_ACK_OK node_rank={args.node_rank}' in text, dmabuf='GPU MR registration path=dmabuf requested=dmabuf' in text, affinity=('Local CPU thread pinned to NUMA node' in text)==(affinity=='pinned'), no_errors=not re.search(r'Traceback \(most recent call last\)|AssertionError|Failed CR status|Segmentation fault|ChildFailedError|\[WARNING\]', text))
            if args.backend=='urma':
                checks.pop('dmabuf')
                checks['urma_device'] = f': {args.urma_device}' in text and '[Proxy] UCCL_EP_TRANSPORT=urma initialized' in text
                checks['private_urma_library'] = 'DIAG_PRIVATE_URMA_OK' in text
                checks['affinity'] = text.count('DIAG_URMA_AFFINITY_OK ')==expected_metrics+(1 if args.profile_cpu else 0)
            if args.profile_cpu:
                perf_status = case/'perf.status.json'
                checks['cpu_profile'] = 'DIAG_PERF_DONE' in text and perf_status.is_file() and json.loads(perf_status.read_text()).get('ok') is True
            if args.trace_transport:
                checks['transport_trace'] = text.count('DIAG_TRANSPORT_OK ')==expected_metrics
            if args.suite=='boundaries':
                checks['boundary_measurements'] = text.count('DIAG_BOUNDARY_OK ')==expected_metrics
            if not all(checks.values()) and rc == 0:
                rc = 90
            (case/'status.json').write_text(json.dumps(dict(rc=rc, checks=checks), indent=2))
            (case/'counters_after.json').write_text(json.dumps(urma_support.counters(args) if args.backend=='urma' else counters(args.hca)))
            print(f'CASE_END {name} rc={rc} checks={checks}', flush=True)
            pair(args, dict(stage='done', job=args.job, case=name, rc=rc))
    print(f'{args.backend.upper()}_DIAG_ALL_PASSED output={root}', flush=True)

if __name__ == '__main__':
    main()
