"""CPU checks against installed real verbs headers; GPU/driver boundaries simulated."""
import os
from pathlib import Path
import subprocess
import tempfile
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
with tempfile.TemporaryDirectory(prefix='uccl-verbs-host-') as tmp:
    compiler=os.environ.get('CXX','g++');target=Path(tmp)/'diag_test'
    flags=[compiler,'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(REPO/'ep/include')]
    gpu=Path(tmp)/'gpu_stage_test'
    subprocess.run(flags+[str(REPO/'ep/tests/gpu_stage_trace_test.cpp'),'-o',str(gpu)],check=True)
    subprocess.run([str(gpu)],check=True)
    roles=Path(tmp)/'gpu_stage_roles_test'
    subprocess.run(flags+['-O3',str(REPO/'ep/tests/gpu_stage_roles_test.cpp'),'-o',str(roles)],check=True)
    subprocess.run([str(roles)],check=True)
    runtime=Path(tmp)/'gpu_stage_runtime_test'
    subprocess.run(flags+['-I'+str(REPO/'ep/tests/gpu_stage_runtime_support'),str(REPO/'ep/tests/gpu_stage_runtime_test.cpp'),'-o',str(runtime)],check=True)
    subprocess.run([str(runtime)],check=True)
    source=(REPO/'ep/src/fifo.cpp').read_text()
    (Path(tmp)/'fifo_implementation.inc').write_text(source[source.index('namespace mscclpp {'):])
    source=(REPO/'ep/src/proxy.cpp').read_text()
    begin=source.index('void Proxy::notify_gpu_completion(');end=source.index('void Proxy::post_gpu_command(',begin)
    (Path(tmp)/'fifo_notify.inc').write_text(source[begin:end].replace('  if (acked_wrs_.empty())','  (void)my_tail;\n  if (acked_wrs_.empty())',1))
    for trace in (False,True):
        fifo=Path(tmp)/('fifo_stage_'+str(trace));extra=['-DUCCL_GPU_STAGE_TRACE'] if trace else []
        subprocess.run(flags+['-I'+tmp]+extra+[str(REPO/'ep/tests/fifo_stage_test.cpp'),'-o',str(fifo)],check=True)
        subprocess.run([str(fifo)],check=True)
    subprocess.run(flags+[str(REPO/'ep/tests/verbs_diag_test.cpp'),'-libverbs','-o',str(target)],check=True)
    subprocess.run([str(target)],check=True)
    subprocess.run(flags+['-DUSE_DMABUF','-I'+str(REPO/'ep/tests/urma_support'),'-include',str(REPO/'ep/tests/urma_support/ep_shim.hpp'),'-fsyntax-only',str(REPO/'ep/src/verbs_write_micro.cpp')],check=True)
    native=Path(tmp)/'notify_test'
    subprocess.run(flags+['-DUSE_DMABUF','-I'+str(REPO/'ep/tests/urma_support'),'-include',str(REPO/'ep/tests/urma_support/ep_shim.hpp'),str(REPO/'ep/tests/verbs_notify_test.cpp'),'-libverbs','-o',str(native)],check=True)
    subprocess.run([str(native)],check=True,timeout=30)
    print('Verbs native micro host syntax passed; no MetaX/hardware validation')
