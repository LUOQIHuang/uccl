"""Single-node GPU registration probe. No peer or benchmark timing required."""
import argparse
import json
from pathlib import Path
import support

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--module', type=Path, required=True)
p.add_argument('--mode', choices=('normal', 'low_latency'), default='normal')
p.add_argument('--channels', type=int, choices=(1, 8), default=8)
p.add_argument('--transports', type=int, choices=(1, 4), default=4)
p.add_argument('--bytes', type=int, default=117506432)
a = p.parse_args()
if a.bytes < 1: p.error('--bytes must be positive')
ep, runtime = support.load_extension(a.module)
if not hasattr(ep, 'probe_urma_registration'):
    raise RuntimeError('REGISTRATION_PROBE_REQUIRES_REBUILD')
import torch
torch.cuda.set_device(0)
main = torch.empty(a.bytes, dtype=torch.uint8, device='cuda:0')
# Same logical control length as kAtomicBufferSize; allocation rounded to 4KiB.
control = torch.empty(86016, dtype=torch.uint8, device='cuda:0')
torch.cuda.synchronize()
print('REGISTRATION_PROBE_BEGIN ' + json.dumps(dict(
    runtime=runtime, mode=a.mode, channels=a.channels if a.mode=='normal' else 1,
    transports=a.transports, bytes=a.bytes, main=main.data_ptr(), control=control.data_ptr())), flush=True)
ep.probe_urma_registration(main.data_ptr(), a.bytes, control.data_ptr(), 81960,
                          0, a.mode=='normal', a.channels, a.transports)
print('REGISTRATION_PROBE_PASSED', flush=True)
