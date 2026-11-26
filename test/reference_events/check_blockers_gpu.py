"""Isolate a local MetaX initialization failure before starting a paired run."""

import ctypes
import os
from pathlib import Path
import socket
import subprocess
import sys


def runtime():
    path = Path(os.environ['MACA_PATH']) / 'lib/libmcruntime.so'
    print('MACA_RUNTIME=' + str(path.resolve()), flush=True)
    return ctypes.CDLL(str(path), mode=os.RTLD_NOW | os.RTLD_GLOBAL)


def torch_smoke():
    runtime()
    import torch

    print(f'TORCH={torch.__version__} PATH={torch.__file__}', flush=True)
    torch.cuda.set_device(0)
    value = torch.ones(1, dtype=torch.float32, device='cuda:0') + 1
    torch.cuda.synchronize()
    if value.item() != 2:
        raise RuntimeError('unexpected value from GPU smoke')
    print('TORCH_GPU_OK device=' + torch.cuda.get_device_name(0), flush=True)


def native_smoke():
    maca = runtime()

    def call(name, signature, *args):
        fn = getattr(maca, name)
        fn.argtypes = signature
        fn.restype = ctypes.c_int
        rc = fn(*args)
        print(f'{name} rc={rc}', flush=True)
        if rc:
            raise RuntimeError(f'{name} failed rc={rc}')

    count = ctypes.c_int()
    ptr = ctypes.c_void_p()
    call('mcGetDeviceCount', [ctypes.POINTER(ctypes.c_int)], ctypes.byref(count))
    print(f'MACA_DEVICE_COUNT={count.value}', flush=True)
    if count.value < 1:
        raise RuntimeError('MACA found no usable device')
    call('mcSetDevice', [ctypes.c_int], 0)
    call('mcMalloc', [ctypes.POINTER(ctypes.c_void_p), ctypes.c_size_t], ctypes.byref(ptr), 4096)
    call('mcFree', [ctypes.c_void_p], ptr)
    print('NATIVE_GPU_OK', flush=True)


def run_probe(name):
    try:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), name], timeout=25).returncode == 0
    except subprocess.TimeoutExpired:
        print(f'GPU_PROBE_TIMED_OUT={name}', flush=True)
        return False


def main():
    if sys.argv[1:] == ['torch']:
        torch_smoke()
        return 0
    if sys.argv[1:] == ['native']:
        native_smoke()
        return 0
    if len(sys.argv) != 1:
        raise SystemExit('usage: bash check_blockers_gpu.sh')

    print(f'HOST={socket.gethostname()} KERNEL={os.uname().release} UID={os.geteuid()}', flush=True)
    print('BOOT_ID=' + Path('/proc/sys/kernel/random/boot_id').read_text().strip(), flush=True)
    if run_probe('torch'):
        print('BLOCKERS_GPU_READY', flush=True)
        return 0
    native_ok = run_probe('native')
    print(f'BLOCKERS_GPU_NOT_READY torch=failed native={"passed" if native_ok else "failed"}', flush=True)
    return 1


if __name__ == '__main__':
    sys.exit(main())
