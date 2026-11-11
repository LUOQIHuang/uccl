"""Compile-only MACA precision + v2 tracing probe; never launches GPU code."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess

REPO=Path(__file__).resolve().parents[2]
def precision_flags():
    tree=ast.parse((REPO/'ep/setup.py').read_text())
    node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='METAX_PRECISION_UNDEFINES' for t in n.targets))
    return ['-U'+name for name in ast.literal_eval(node.value)]

def probe(output,maca,trace_build=False):
    maca=maca.resolve();compiler=maca/'tools/cu-bridge/bin/cucc'
    if not compiler.is_file():raise RuntimeError('MACA compiler missing: '+str(compiler))
    output.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ)
    env.update(MACA_PATH=str(maca),CUDA_HOME=str(maca/'tools/cu-bridge'),CUDA_PATH=str(maca/'tools/cu-bridge'),CUCC_PATH=str(maca/'tools/cu-bridge'),MACA_CLANG_PATH=str(maca/'mxgpu_llvm/bin'),CUCC_TARGETS='xcore1000')
    env['PATH']=':'.join(str(maca/p) for p in ('tools/cu-bridge/bin','tools/cu-bridge/tools','mxgpu_llvm/bin','bin'))+':'+env.get('PATH','')
    env['LD_LIBRARY_PATH']=str(maca/'lib')+':'+str(maca/'mxgpu_llvm/lib')+':'+env.get('LD_LIBRARY_PATH','')
    gcc=Path(subprocess.check_output(['g++','-print-libgcc-file-name'],env=env,text=True).strip()).parent
    source=output/'precision.cu'
    source.write_text('''#include "ep_configs.cuh"
__global__ void precision_compile_probe(float* out) {
  auto packed=__nv_cvt_float2_to_fp8x2(make_float2(1.0f,2.0f),__NV_SATFINITE,__NV_E4M3);
  out[0]=__half2float(__float2half(1.0f));(void)packed;
}
''')
    # Reproduce the Torch prefix, followed by the production fix's -U flags.
    prefix=['-D'+name[2:] for name in precision_flags()[:4]]
    common=[str(compiler),'-std=c++17','-O3','-fPIC',*prefix,*precision_flags(),'-DUSE_METAX','-D__MC_PLATFORM_MXCC__','-DUSE_MACA','-DNV_ARCH_A100','-DDISABLE_SM90_FEATURES','--maca-path='+str(maca),'--gcc-install-dir='+str(gcc),'-Xdevice','-D__CUDA_ARCH__=800','-rdc=true','-I'+str(REPO/'ep/include')]
    commands=[common+['-c',str(source),'-o',str(output/'precision.o')]]
    for trace in ((False,True) if trace_build else (False,)):
        defines=(['-DUCCL_GPU_STAGE_TRACE'] if trace else [])+(['-DUCCL_GPU_STAGE_ROLES_ONLY'] if trace and os.environ.get('UCCL_GPU_STAGE_ROLES_ONLY')=='1' else [])
        commands.append(common+defines+['-c',str(REPO/'ep/src/gpu_stage_trace.cu'),'-o',str(output/('trace.o' if trace else 'base.o'))])
    if os.environ.get('UCCL_REFERENCE_EVENTS')=='1':
        commands.append(common+['-DUCCL_REFERENCE_EVENTS','-c',str(REPO/'ep/src/reference_event_trace.cu'),'-o',str(output/'reference_events.o')])
    inputs=['ep/setup.py','ep/include/ep_configs.cuh','ep/include/gpu_stage_trace.cuh','ep/src/gpu_stage_trace.cu']
    if os.environ.get('UCCL_REFERENCE_EVENTS')=='1':inputs+=['ep/include/reference_event_trace.cuh','ep/src/reference_event_trace.cu']
    (output/'request.json').write_text(json.dumps(dict(maca=str(maca),commands=commands,sources={p:hashlib.sha256((REPO/p).read_bytes()).hexdigest() for p in inputs}),indent=2))
    for index,command in enumerate(commands):
        with (output/f'compile_{index}.log').open('w') as log:
            result=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT)
        if result.returncode:raise RuntimeError('PRECISION_PROBE_FAILED: '+str(output/f'compile_{index}.log'))
    print('MACA_PRECISION_PROBE_PASSED (compile only; no GPU execution) output='+str(output))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--maca-path',type=Path,default=Path(os.environ.get('MACA_PATH','/opt/maca-3.7.0')))
    p.add_argument('--trace-build',action='store_true')
    a=p.parse_args();probe(a.output.resolve(),a.maca_path,a.trace_build)
