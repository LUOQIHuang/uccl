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

faulthandler.enable()
runtime = pathlib.Path(os.environ.get('MACA_PATH', '/opt/maca-3.7.0'))/'lib/libmcruntime.so'
if runtime.exists():
    ctypes.CDLL(str(runtime), mode=os.RTLD_NOW | os.RTLD_GLOBAL)
case = pathlib.Path(os.environ['VERBS_DIAG_CASE'])
bench = pathlib.Path(os.environ['VERBS_DIAG_BENCH']).resolve()
sys.path[:0] = [str(case.parent / 'python'), str(bench)]
import torch
import torch.distributed as dist
import uccl.ep as ep
import utils
import buffer

extension = pathlib.Path(ep.__file__).resolve()
assert hashlib.sha256(extension.read_bytes()).hexdigest() == os.environ['VERBS_DIAG_SHA']
assert pathlib.Path(utils.__file__).resolve() == bench / 'utils.py'
assert pathlib.Path(buffer.__file__).resolve() == bench / 'buffer.py'
assert os.environ['UCCL_EP_TRANSPORT'] == 'verbs'
hint = ep.Config(24, 8, 512, 16, 512).get_rdma_buffer_size_hint(7168 * 2, 2)
assert hint > 0, 'extension lacks the required multi-node buffer topology'
print('DIAG_EXTENSION', str(extension), flush=True)
print('DIAG_RDMA_HINT_BYTES', hint, flush=True)
print('DIAG_ENV', json.dumps({k: os.environ.get(k) for k in ('UCCL_EP_TRANSPORT', 'UCCL_EP_VERBS_REG', 'UCCL_IB_HCA', 'UCCL_EP_DISABLE_CPU_AFFINITY', 'MCCL_IB_DISABLE', 'MCCL_SOCKET_IFNAME')}), flush=True)

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
def measured_profile(fn, kernel_names, *args, **kwargs):
    global counter
    index = counter
    counter += 1
    op = 'dispatch_fp8' if index < 8 else 'dispatch_bf16' if index < 16 else 'combine'
    rdma = list(range(4, 33, 4))[index % 8] if index < 16 else list(range(12, 33, 4))[index-16]
    result = original_profile(fn, kernel_names, *args, **kwargs)
    snapshot(f'{op}_rdma{rdma}')
    dist.barrier()
    # Separate measurement of the complete callable using CUDA events. This
    # is additional work outside the profiler's original measurement.
    avg, minimum, maximum = utils.bench(fn, num_warmups=5, num_tests=15)
    dist.barrier()
    row = dict(op=op, sm=24, nvl=4, rdma=rdma, transmit_us=result[0]*1e6, notify_us=result[1]*1e6, api_cuda_event_us=float(avg)*1e6, api_min_us=float(minimum)*1e6, api_max_us=float(maximum)*1e6)
    print('DIAG_METRIC '+json.dumps(row), flush=True)
    with (case/'metrics.jsonl').open('a') as out:
        out.write(json.dumps(row)+'\n')
    return result

utils.bench_kineto = measured_profile
snapshot('before_benchmark')
sys.argv = [str(case.parent/'test_internode_diag.py'), '--num-processes=1', '--num-tokens=4096', '--hidden=7168', '--num-topk-groups=2', '--num-topk=8', '--num-experts=256', '--test-ll-compatibility', '--pressure-test-mode=0']
runpy.run_path(sys.argv[0], run_name='__main__')
assert counter == 22, counter
snapshot('after_benchmark')
print('DIAG_BENCH_DONE', flush=True)
