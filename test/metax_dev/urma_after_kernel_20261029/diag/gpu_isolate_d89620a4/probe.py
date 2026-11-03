import ctypes
import faulthandler
import gc
import hashlib
import os
import sys
from datetime import timedelta
from pathlib import Path

faulthandler.enable()
faulthandler.dump_traceback_later(25, repeat=True)
stage = sys.argv[1]
sizes = {"control": 403243776, "exact": 483892608, "group": 483892608}
assert stage in sizes

def phase(name):
    print(f"PHASE={name} STAGE={stage}", flush=True)

ctypes.CDLL("/opt/maca-3.7.0/lib/libmcruntime.so", mode=os.RTLD_NOW | os.RTLD_GLOBAL)
phase("RUNTIME_LOADED")
import torch
import torch.distributed as dist
import uccl.ep as ep

extension = Path(ep.__file__).resolve()
expected = Path("/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/ep_private_b73a94c1/module/ep.abi3.so")
assert extension == expected
assert hashlib.sha256(extension.read_bytes()).hexdigest() == "14f1b7b3c7734cfe8609473ecc96bb3478cc7c224bc54027922f75523b7dec10"
assert any("/umdk_private_618957c3/lib/liburma.so" in line for line in Path("/proc/self/maps").read_text().splitlines())
print(f"EXTENSION={extension} SIZE_BYTES={sizes[stage]}", flush=True)
phase("SET_DEVICE_BEGIN")
torch.cuda.set_device(0)
phase("SET_DEVICE_DONE")
if stage == "group":
    phase("PROCESS_GROUP_BEGIN")
    dist.init_process_group(backend="nccl", init_method="tcp://127.0.0.1:29863", rank=0, world_size=1, timeout=timedelta(seconds=40))
    phase("PROCESS_GROUP_DONE")
    dist.new_group([0])
    phase("NEW_GROUP_DONE")
torch.set_default_dtype(torch.bfloat16)
torch.set_default_device("cuda:0")
phase("GET_RDMA_BUFFER_BEGIN")
capsule, host = ep.get_rdma_buffer(sizes[stage], 0)
phase("GET_RDMA_BUFFER_DONE")
assert not host
tensor = torch.utils.dlpack.from_dlpack(capsule)
assert tensor.numel() == sizes[stage]
phase("SYNC_BEGIN")
torch.cuda.synchronize()
phase("SYNC_DONE")
del tensor, capsule
gc.collect()
phase("RELEASE_DONE")
if stage == "group":
    dist.destroy_process_group()
    phase("PROCESS_GROUP_DESTROYED")
faulthandler.cancel_dump_traceback_later()
print(f"STAGE_OK={stage}", flush=True)
