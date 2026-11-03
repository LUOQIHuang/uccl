"""Run a private, reduced copy of the existing Normal benchmark."""
import ctypes
import faulthandler
import hashlib
import json
import os
import pathlib
import runpy
import sys
import time
from datetime import timedelta
import urma_support
import profile_support

faulthandler.enable()
runtime = pathlib.Path(os.environ.get('MACA_PATH', '/opt/maca-3.7.0'))/'lib/libmcruntime.so'
if runtime.exists():
    ctypes.CDLL(str(runtime), mode=os.RTLD_NOW | os.RTLD_GLOBAL)
case = pathlib.Path(os.environ['VERBS_DIAG_CASE'])
bench = pathlib.Path(os.environ['VERBS_DIAG_BENCH']).resolve()
backend = os.environ.get('UCCL_EP_TRANSPORT', 'verbs')
utils_dir = case.parent/'diagnostic_utils' if backend=='urma' else bench
sys.path[:0] = [str(case.parent / 'python'), str(utils_dir), str(bench)]
import torch
import torch.distributed as dist
import uccl.ep as ep
import utils
import buffer

extension = pathlib.Path(ep.__file__).resolve()
assert hashlib.sha256(extension.read_bytes()).hexdigest() == os.environ['VERBS_DIAG_SHA']
assert pathlib.Path(utils.__file__).resolve() == utils_dir / 'utils.py'
assert pathlib.Path(buffer.__file__).resolve() == bench / 'buffer.py'
assert backend in ('verbs','urma')
hint = ep.Config(24, 8, 512, 16, 512).get_rdma_buffer_size_hint(7168 * 2, 2)
assert hint > 0, 'extension lacks the required multi-node buffer topology'
print('DIAG_EXTENSION', str(extension), flush=True)
print('DIAG_RDMA_HINT_BYTES', hint, flush=True)
print('DIAG_ENV', json.dumps({k: os.environ.get(k) for k in ('UCCL_EP_TRANSPORT', 'UCCL_EP_VERBS_REG', 'UCCL_IB_HCA', 'UCCL_EP_DISABLE_CPU_AFFINITY', 'MCCL_IB_DISABLE', 'MCCL_SOCKET_IFNAME')}), flush=True)
loaded = sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines() if '.so' in line and '/' in line})
(case/'runtime.json').write_text(json.dumps(dict(backend=backend, extension=str(extension), python=sys.executable, torch_version=torch.__version__, torch_file=torch.__file__, loaded_libraries=loaded), indent=2))
affinity_control = None
if backend=='urma':
    expected_library = pathlib.Path(os.environ['URMA_DIAG_LIBRARY_DIR']).resolve()
    urma_libraries = [pathlib.Path(p).resolve() for p in loaded if '/liburma.so' in p]
    assert urma_libraries and all(p.is_relative_to(expected_library) for p in urma_libraries), str(urma_libraries)
    assert os.environ.get('UCCL_METAX_URMA_HOST_ATOMICS') is None
    cpus = urma_support.parse_cpus(os.environ['URMA_DIAG_CPUS'])
    assert len(cpus)==ep.get_num_proxy_threads(), 'CPU list must match the number of Proxy threads'
    affinity_control = urma_support.ProxyAffinity(cpus, os.environ['URMA_DIAG_PINNED']=='1', case/'proxy_starts.jsonl')
    utils._diag_start_proxy = affinity_control.start
    print('DIAG_PRIVATE_URMA_OK', str(urma_libraries), flush=True)

def snapshot(label):
    records = []
    for task in pathlib.Path('/proc/self/task').iterdir():
        try:
            status = dict(line.split(':', 1) for line in (task/'status').read_text().splitlines() if ':' in line)
            fields = (task/'stat').read_text().rsplit(')', 1)[1].split()
            records.append(dict(tid=int(task.name), name=status.get('Name', '').strip(), allowed=status.get('Cpus_allowed_list', '').strip(), cpu=int(fields[36]), voluntary=status.get('voluntary_ctxt_switches', '').strip(), involuntary=status.get('nonvoluntary_ctxt_switches', '').strip(), schedstat=(task/'schedstat').read_text().strip()))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    with (case/'affinity.jsonl').open('a') as out:
        out.write(json.dumps(dict(time=time.time(), label=label, pid=os.getpid(), threads=records))+'\n')
    return records

def lazy_init(local_rank, num_local_ranks):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend='nccl', timeout=timedelta(seconds=180))
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'cuda:{local_rank}')
    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(dist.get_world_size())))

utils.init_dist_under_torchrun = lazy_init
# Suppress only the original benchmark's shared-barrier glob sweeps; never
# delete pre-existing jobs' shared memory. The driver rejects stale names.
import glob
original_glob = glob.glob
def scoped_glob(pattern, *args, **kwargs):
    if isinstance(pattern, str) and pattern.startswith('/dev/shm/uccl_barrier_*'):
        return []
    return original_glob(pattern, *args, **kwargs)
glob.glob = scoped_glob

original_profile = utils.bench_kineto
counter = 0
configurations = profile_support.plan(os.environ.get('DIAG_SUITE', 'scan'))
def measured_profile(fn, kernel_names, *args, **kwargs):
    global counter
    index = counter
    counter += 1
    op, rdma = configurations[index]
    sampling = os.environ.get('DIAG_PROFILE_CPU')=='1' and op=='dispatch_bf16'
    profiler = None
    if sampling:
        assert affinity_control is not None and not args
        assert len(affinity_control.groups)==len(cpus)
        assert all(len(group['new_tids'])==1 for group in affinity_control.groups), 'ambiguous Proxy TIDs; refuse CPU sampling'
        affinity_control.verify(snapshot('before_cpu_profile'))
        profiler = profile_support.ProxyProfile(case, [group['new_tids'][0] for group in affinity_control.groups], os.environ['DIAG_PERF_CALLGRAPH'])
        kwargs['num_tests'] = 200
        dist.barrier()
        profiler.start()
    try:
        result = original_profile(fn, kernel_names, *args, **kwargs)
    finally:
        if profiler is not None:
            profiler.stop()
    observed = snapshot(f'{op}_rdma{rdma}')
    if affinity_control is not None:
        affinity_control.verify(observed)
    dist.barrier()
    # Separate measurement of the complete callable using CUDA events. This
    # is additional work outside the profiler's original measurement.
    avg, minimum, maximum = utils.bench(fn, num_warmups=5, num_tests=15)
    dist.barrier()
    row = dict(op=op, sm=24, nvl=4, rdma=rdma, transmit_us=result[0]*1e6, notify_us=result[1]*1e6, api_cuda_event_us=float(avg)*1e6, api_min_us=float(minimum)*1e6, api_max_us=float(maximum)*1e6)
    row.update(cpu_profile=sampling, profiler_num_tests=200 if sampling else 30)
    print('DIAG_METRIC '+json.dumps(row), flush=True)
    with (case/'metrics.jsonl').open('a') as out:
        out.write(json.dumps(row)+'\n')
    return result

utils.bench_kineto = measured_profile
snapshot('before_benchmark')
sys.argv = [str(case.parent/'test_internode_diag.py'), '--num-processes=1', '--num-tokens=4096', '--hidden=7168', '--num-topk-groups=2', '--num-topk=8', '--num-experts=256', '--test-ll-compatibility', '--pressure-test-mode=0']
runpy.run_path(sys.argv[0], run_name='__main__')
assert counter == len(configurations), counter
snapshot('after_benchmark')
print('DIAG_BENCH_DONE', flush=True)
