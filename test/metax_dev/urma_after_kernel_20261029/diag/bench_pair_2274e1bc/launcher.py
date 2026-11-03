import ctypes, os, faulthandler
faulthandler.enable()
faulthandler.dump_traceback_later(45, repeat=True)
print('PHASE=PYTHON_LAUNCHER_BEGIN', flush=True)
ctypes.CDLL('/opt/maca-3.7.0/lib/libmcruntime.so', mode=os.RTLD_NOW | os.RTLD_GLOBAL)
print('MCRUNTIME_PRELOAD_OK', flush=True)
import pathlib, runpy, sys
from datetime import timedelta
import torch
import torch.distributed as dist
import uccl.ep as ep
import utils
from buffer import Buffer
module = pathlib.Path(os.environ['UCCL_EP_MODULE_ROOT']).resolve()
bench = pathlib.Path('/home/f00955680/uccl-ep-xpu-integration/ep/bench').resolve()
extension = pathlib.Path(ep.__file__).resolve()
print('EXTENSION:', extension, flush=True)
print('BUFFER_MODULE:', pathlib.Path(__import__('buffer').__file__).resolve(), flush=True)
assert extension.is_relative_to(module), 'unexpected extension path'
import hashlib
assert hashlib.sha256(extension.read_bytes()).hexdigest() == os.environ['UCCL_EP_EXPECTED_SHA'], 'unexpected extension hash'
print('URMA_LIB_MAPS:', sorted({line.split()[-1] for line in pathlib.Path('/proc/self/maps').read_text().splitlines() if '/liburma.so' in line}), flush=True)
assert any(os.environ['UCCL_PRIVATE_UMDK'] in line and '/liburma.so' in line for line in pathlib.Path('/proc/self/maps').read_text().splitlines()), 'private liburma not loaded'
assert pathlib.Path(utils.__file__).resolve().is_relative_to(bench), 'unexpected utils path'
assert pathlib.Path(__import__('buffer').__file__).resolve().is_relative_to(bench), 'unexpected buffer path'
size = ep.Config(24, 8, 512, 16, 512).get_rdma_buffer_size_hint(7168 * 2, 2)
print('RDMA_HINT_BYTES:', size, flush=True)
assert size > 0, 'missing multi-node buffer hint'
def phase(name):
    print(f"PHASE={name} RANK={os.environ.get('RANK', '?')}", flush=True)
def lazy_init(local_rank, num_local_ranks):
    phase('SET_DEVICE_BEGIN')
    torch.cuda.set_device(local_rank)
    phase('SET_DEVICE_DONE')
    dist.init_process_group(backend='nccl', timeout=timedelta(seconds=100))
    phase('PROCESS_GROUP_DONE')
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'cuda:{local_rank}')
    phase('NEW_GROUP_BEGIN')
    group = dist.new_group(list(range(dist.get_world_size())))
    phase('NEW_GROUP_DONE')
    return dist.get_rank(), dist.get_world_size(), group
utils.init_dist_under_torchrun = lazy_init
# The benchmark's broad /dev/shm sweep is unsafe on shared hosts. Do not
# remove any existing barrier files; the launcher rejects same-UID collisions.
import glob as _glob
_original_glob = _glob.glob
def _scoped_glob(pattern, *args, **kwargs):
    if isinstance(pattern, str) and pattern.startswith('/dev/shm/uccl_barrier_*'):
        phase('SKIP_SHARED_BARRIER_SWEEP')
        return []
    return _original_glob(pattern, *args, **kwargs)
_glob.glob = _scoped_glob
_original_all_gather_object = dist.all_gather_object
_gather_sequence = 0
def _traced_all_gather_object(*args, **kwargs):
    global _gather_sequence
    _gather_sequence += 1
    current = _gather_sequence
    phase(f'ALL_GATHER_OBJECT_{current}_BEGIN')
    result = _original_all_gather_object(*args, **kwargs)
    phase(f'ALL_GATHER_OBJECT_{current}_DONE')
    return result
dist.all_gather_object = _traced_all_gather_object
original_buffer_init = Buffer.__init__
def traced_buffer_init(self, *args, **kwargs):
    phase('BUFFER_INIT_BEGIN')
    try:
        result = original_buffer_init(self, *args, **kwargs)
    except BaseException as exc:
        phase(f'BUFFER_INIT_FAILED_{type(exc).__name__}')
        raise
    phase('BUFFER_INIT_DONE')
    return result
Buffer.__init__ = traced_buffer_init
script = sys.argv.pop(1)
phase('BENCH_SCRIPT_BEGIN')
runpy.run_path(script, run_name='__main__')
phase('BENCH_SCRIPT_DONE')
faulthandler.cancel_dump_traceback_later()
