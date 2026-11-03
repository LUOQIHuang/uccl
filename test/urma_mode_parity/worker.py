"""Execute the real benchmark with explicit extension loading and mode checks."""
import faulthandler
import glob
import json
import os
from pathlib import Path
import runpy
import sys
from datetime import timedelta
import support

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
    result=original_profile(fn,kernel_names,*args,**kwargs)
    data=dict(mode=mode,index=index,kernel_names=kernel_names,kernel_seconds=result)
    if mode=='normal':
        op,chunk=normal_plan[index]
        avg,minimum,maximum=original_api(fn,num_warmups=5,num_tests=15)
        data.update(op=op,rdma=chunk,nvl=4,sm=24,api_us=float(avg)*1e6,api_min_us=float(minimum)*1e6,api_max_us=float(maximum)*1e6)
    record('MODE_PARITY_PROFILE',data)
    return result
utils.bench_kineto=measured_profile

sys.argv=[str(case.parent/(mode+'.py')),'--num-processes=1','--num-tokens='+os.environ['PARITY_TOKENS'],'--hidden='+os.environ['PARITY_HIDDEN'],'--num-experts='+os.environ['PARITY_EXPERTS'],'--num-topk='+os.environ['PARITY_TOPK'],'--pressure-test-mode=0']
if mode=='normal': sys.argv+=['--num-topk-groups=2','--test-ll-compatibility']
runpy.run_path(sys.argv[0],run_name='__main__')
assert count==(4 if mode=='normal' else 2),count
print('MODE_PARITY_BENCH_DONE',flush=True)
