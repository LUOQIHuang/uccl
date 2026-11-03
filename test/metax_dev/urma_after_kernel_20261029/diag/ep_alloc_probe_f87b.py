import ctypes
import faulthandler
import os
from pathlib import Path

faulthandler.enable()
faulthandler.dump_traceback_later(10, repeat=True)
ctypes.CDLL('/opt/maca-3.7.0/lib/libmcruntime.so', mode=os.RTLD_NOW | os.RTLD_GLOBAL)
print('PHASE=RUNTIME_LOADED', flush=True)
import torch
import uccl.ep as ep
print('EXTENSION=', Path(ep.__file__).resolve(), flush=True)
torch.cuda.set_device(0)
print('PHASE=DEVICE_SET', flush=True)
size = ep.get_low_latency_rdma_size_hint(128, 2048, 2, 16)
print('PHASE=SIZE_HINT_DONE BYTES=', size, flush=True)
print('PHASE=GET_RDMA_BUFFER_BEGIN', flush=True)
capsule, host = ep.get_rdma_buffer(size, 0)
print('PHASE=GET_RDMA_BUFFER_DONE HOST=', host, flush=True)
tensor = torch.utils.dlpack.from_dlpack(capsule)
print('PHASE=DLPACK_DONE SIZE=', tensor.numel(), flush=True)
torch.cuda.synchronize()
print('PHASE=SYNC_DONE', flush=True)
del tensor
print('PHASE=RELEASE_DONE', flush=True)
faulthandler.cancel_dump_traceback_later()
