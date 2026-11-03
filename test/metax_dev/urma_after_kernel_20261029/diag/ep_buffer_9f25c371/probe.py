import ctypes,faulthandler,hashlib,os
from pathlib import Path
faulthandler.enable()
faulthandler.dump_traceback_later(10,repeat=True)
ctypes.CDLL('/opt/maca-3.7.0/lib/libmcruntime.so',mode=os.RTLD_NOW|os.RTLD_GLOBAL)
print('PHASE=RUNTIME_LOADED',flush=True)
import torch
print('PHASE=TORCH_IMPORTED',flush=True)
import uccl.ep as ep
extension=Path(ep.__file__).resolve()
expected=Path('/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/ep_private_b73a94c1/module/ep.abi3.so')
print('EXTENSION=',extension,flush=True)
assert extension==expected, 'unexpected extension path'
assert hashlib.sha256(extension.read_bytes()).hexdigest()=='14f1b7b3c7734cfe8609473ecc96bb3478cc7c224bc54027922f75523b7dec10'
print('PHASE=DEVICE_SET_BEGIN',flush=True)
torch.cuda.set_device(0)
print('PHASE=DEVICE_SET_DONE',flush=True)
for label,size in [('LL',ep.get_low_latency_rdma_size_hint(128,2048,2,16)),('NORMAL',ep.Config(24,8,512,16,512).get_rdma_buffer_size_hint(7168*2,2))]:
 size=int(size)
 print(f'PHASE={label}_SIZE_HINT BYTES={size}',flush=True)
 assert 0<size<=512*1024*1024, 'unexpected memory allocation size'
 print(f'PHASE={label}_GET_RDMA_BUFFER_BEGIN',flush=True)
 capsule,host=ep.get_rdma_buffer(size,0)
 print(f'PHASE={label}_GET_RDMA_BUFFER_DONE HOST={host}',flush=True)
 tensor=torch.utils.dlpack.from_dlpack(capsule)
 print(f'PHASE={label}_DLPACK_DONE SIZE={tensor.numel()}',flush=True)
 torch.cuda.synchronize()
 print(f'PHASE={label}_SYNC_DONE',flush=True)
 del tensor,capsule
 print(f'PHASE={label}_RELEASE_DONE',flush=True)
faulthandler.cancel_dump_traceback_later()
print('EP_BUFFER_PROBE_OK',flush=True)
