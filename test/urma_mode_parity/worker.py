"""Execute the real benchmark with explicit extension loading and mode checks."""
import faulthandler
import glob
import json
import os
from pathlib import Path
import runpy
import sys
import subprocess
import time
from datetime import timedelta
import support
from phase_runtime import Measurements

faulthandler.enable()
case=Path(os.environ['PARITY_CASE'])
mode=os.environ['PARITY_MODE']
bench=support.REPO/'ep/bench'
sys.path.insert(0,str(bench))
ep,info=support.load_extension(Path(os.environ['PARITY_MODULE']))
assert info['module_sha']==os.environ['PARITY_MODULE_SHA'], 'module changed after preflight'
(case/'runtime.json').write_text(json.dumps(info,indent=2))
import torch
import torch.distributed as dist
import utils
phase_suite=os.environ.get('PARITY_PHASE_SUITE')=='1'
stage_profile=os.environ.get('PARITY_STAGE_PROFILE')=='1'
api_only=os.environ.get('PARITY_API_ONLY')=='1'
if phase_suite and not all(hasattr(ep,n) for n in ('urma_phase_enable','urma_phase_snapshot')):
    raise RuntimeError('PHASE_DIAGNOSTICS_REQUIRES_REBUILD')
if hasattr(ep,'urma_phase_enable'): ep.urma_phase_enable(False)
measurements=Measurements(torch,dist,ep,case,mode,stage_profile,
    int(os.environ.get('PARITY_WARMUPS','50')),int(os.environ.get('PARITY_SAMPLES','50')),
    os.environ.get('PARITY_CAPTURE_LAYOUT')=='1',os.environ.get('PARITY_DISPATCH_TRIAL')=='1',
    int(os.environ.get('PARITY_DISPATCH_DELAY_US','0')),os.environ.get('PARITY_TIMELINE')=='1')
measurements.dispatch_focus=os.environ.get('PARITY_DISPATCH_FOCUS')=='1'
if os.environ.get('PARITY_GPU_STAGES')=='1':
    import buffer as stage_buffer
    stage_proxies=[];original_buffer_init=stage_buffer.Buffer.__init__
    def stage_buffer_init(self,*args,**kwargs):
        original_buffer_init(self,*args,**kwargs);stage_proxies[:]=self.proxies
    stage_buffer.Buffer.__init__=stage_buffer_init
    def stage_rings():
        addresses=list(dict.fromkeys(int(a) for p in stage_proxies for a in p.get_d2h_channel_addrs()))
        if not addresses:raise RuntimeError('GPU_STAGE_RINGS_MISSING')
        return addresses
    measurements.gpu_rings=stage_rings
    measurements.gpu_stage_mode=os.environ.get("PARITY_GPU_STAGE_MODE","all")
print('PHASE_CONFIGURATION '+json.dumps(dict(suite=phase_suite,instrumented=stage_profile,
    warmups=measurements.warmups,samples=measurements.samples,
    ll_hook_workload=os.environ.get('PARITY_LL_HOOK_WORKLOAD','gemm'))),flush=True)

def lazy_init(local_rank,num_local_ranks):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend='nccl',timeout=timedelta(seconds=180))
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'cuda:{local_rank}')
    return dist.get_rank(),dist.get_world_size(),dist.new_group(list(range(dist.get_world_size())))
utils.init_dist_under_torchrun=lazy_init

# No benchmark-owned glob sweeps of pre-existing shared barriers. The driver
# performs the device/stale-barrier idle gate before launching this worker.
original_glob=glob.glob
def scoped_glob(pattern,*args,**kwargs):
    if isinstance(pattern,str) and pattern.startswith('/dev/shm/uccl_barrier_*'): return []
    return original_glob(pattern,*args,**kwargs)
glob.glob=scoped_glob

def record(name,data):
    print(name+' '+json.dumps(data),flush=True)
    with (case/'metrics.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(kind=name,**data))+'\n')

original_api=utils.bench
def measured_api(fn,*args,**kwargs):
    if phase_suite:
        if args or any(k not in ('num_warmups','num_tests','post_fn') for k in kwargs):
            raise RuntimeError('unexpected benchmark arguments in phase suite')
        result=measurements.api(fn,'layout' if mode=='normal' else 'll_no_hook_dispatch_combine',kwargs.get('post_fn'))
    else:
        result=original_api(fn,*args,**kwargs)
    record('MODE_PARITY_API',dict(mode=mode,avg_us=float(result[0])*1e6,min_us=float(result[1])*1e6,max_us=float(result[2])*1e6))
    return result
utils.bench=measured_api

original_profile=utils.bench_kineto
count=0
normal_plan=[('dispatch_fp8',32),('dispatch_bf16',32),('combine',12),('combine',16)]
def measured_profile(fn,kernel_names,*args,**kwargs):
    global count
    index=count; count+=1
    label=(normal_plan[index][0]+'_chunk'+str(normal_plan[index][1])) if mode=='normal' else ('ll_no_hook' if index==0 else 'll_hook')
    if os.environ.get('PARITY_SAVE_TRACES')=='1': kwargs['trace_path']=str(case/f'gpu_trace_{index}.json')
    profile=lambda:original_profile(fn,kernel_names,*args,**kwargs)
    if api_only:
        # Nonzero Normal notify keeps the benchmark's best-config selection
        # alive. These placeholders are never exported as GPU measurements.
        result=(1e-12,1e-12) if mode=='normal' else ((0.0,0.0) if index==0 else ([0.0,0.0],[0.0,0.0]))
        print('MODE_PARITY_GPU_PROFILE_SKIPPED '+label,flush=True)
    else:
        result=measurements.window('profile_'+label,profile,60) if phase_suite else profile()
    data=dict(mode=mode,index=index,kernel_names=kernel_names,kernel_seconds=None if api_only else result,gpu_profiled=not api_only)
    if mode=='normal':
        op,chunk=normal_plan[index]
        avg,minimum,maximum=(measurements.api(fn,label) if phase_suite else
                             original_api(fn,num_warmups=5,num_tests=15))
        data.update(op=op,rdma=chunk,nvl=4,sm=24,api_us=float(avg)*1e6,api_min_us=float(minimum)*1e6,api_max_us=float(maximum)*1e6)
        if api_only: result=(avg,1e-12)
    record('MODE_PARITY_API_ONLY_POINT' if api_only else 'MODE_PARITY_PROFILE',data)
    if phase_suite and mode=='low_latency' and index==1 and os.environ.get('PARITY_LL_HOOK_WORKLOAD')=='immediate':
        avg,minimum,maximum=measurements.api(fn,'ll_immediate_hook_dispatch_combine')
        record('MODE_PARITY_HOOK_API',dict(avg_us=avg*1e6,min_us=minimum*1e6,max_us=maximum*1e6))
    if api_only and index==(3 if mode=='normal' else 1):
        # Capture BEFORE the benchmark returns and tears down GPU registrations,
        # outside all timed windows, to distinguish runtime from teardown AEs.
        begin=time.time_ns()
        try:
            snapshot=subprocess.run(['journalctl','-b','-k','-n','400','--no-pager'],capture_output=True,text=True,timeout=20)
            (case/'kernel_after_measurements.txt').write_text(snapshot.stdout+snapshot.stderr)
            rc=snapshot.returncode
        except Exception as exc:
            (case/'kernel_after_measurements.txt').write_text(str(exc)); rc=-1
        (case/'measurement_boundary.json').write_text(json.dumps(dict(begin_realtime_ns=begin,end_realtime_ns=time.time_ns(),rc=rc,note='after all timing windows, before benchmark teardown')))
        dist.barrier()  # Neither peer tears down before BOTH snapshots finish.
    return result
utils.bench_kineto=measured_profile

sys.argv=[str(case.parent/(mode+'.py')),'--num-processes=1','--num-tokens='+os.environ['PARITY_TOKENS'],'--hidden='+os.environ['PARITY_HIDDEN'],'--num-experts='+os.environ['PARITY_EXPERTS'],'--num-topk='+os.environ['PARITY_TOPK'],'--pressure-test-mode=0']
if mode=='normal': sys.argv+=['--num-topk-groups=2','--test-ll-compatibility']
runpy.run_path(sys.argv[0],run_name='__main__')
assert count==(4 if mode=='normal' else 2),count
print('MODE_PARITY_BENCH_DONE',flush=True)
