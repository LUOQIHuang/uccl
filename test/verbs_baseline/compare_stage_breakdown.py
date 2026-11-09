"""Compare separately validated URMA/verbs Normal experiments across kernels."""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
import subprocess
import sys
import common
from summarize import write
from summarize_stage_breakdown import summarize as verbs_summary

def read(p):return json.loads(p.read_text())
def csv_rows(p):
    with p.open(newline='') as stream:return list(csv.DictReader(stream))

def context(verbs,urma):
    out=[]
    for rank in (0,1):
        vn=next(p.parent for p in verbs.glob(f'*/node{rank}/setup.json') if read(p)['fingerprint'].get('gpu_stages'))
        un=next(p.parent for p in urma.glob(f'*/node{rank}/setup.json') if read(p)['fingerprint'].get('gpu_stages'))
        vc=read(vn/'setup.json')['fingerprint'];uc=read(un/'setup.json')['fingerprint']
        vm=read(vn/'module_manifest.json');um=read(un/'build_manifest.json')
        vh=read(vn/'host.json');uh=read(un/'host.json')
        if vh['host']!=uh['host'] or vc['gpu']!=uc['gpu'] or vc['torch']!=uc['torch'] or vc['ifname']!=uc['ifname']:raise ValueError('different host/GPU/Torch/control interface')
        if uc['shape']!=[4096,128,7168,256,8] or not uc['phase_suite'] or not uc['api_only'] or uc['save_traces'] or uc['ll_write_order']!='default' or uc['normal_completion']!='all':raise ValueError('different URMA workload/mode')
        if common.sha(vn/'benchmark/test_internode.py')!=common.sha(un/'normal.py'):raise ValueError('different prepared Normal benchmark')
        compiled=lambda m:{p for p in m['sources'] if Path(p).suffix in ('.h','.hpp','.cuh','.cc','.cpp','.cu') or p=='ep/setup.py'}
        if compiled(vm)!=compiled(um):raise ValueError('different compiled source file sets')
        shared=vm['sources'].keys() & um['sources'].keys()
        changed=sorted(p for p in shared if vm['sources'][p]!=um['sources'][p])
        # The first URMA stage release predates verbs payload/address annotations.
        # They execute only during diagnostics. Record this explicit exception.
        if set(changed)-{'ep/include/verbs_stage_diag.hpp'}:raise ValueError('shared compiled sources changed: '+str(changed))
        if changed and (um['sources'][changed[0]],vm['sources'][changed[0]])!=('25620b7c4883940cf3550be152f2a3ca9a2523768a8c417f4a2b36ad6265540a','b7784cfde6f79109eb92685be8aa0bf1518e2aa33798b5fc7cf85dfdefd6f64c'):
            raise ValueError('unrecognized verbs tracing source change')
        vf=dict(vm['flags']);uf=dict(um['flags'])
        for key in ('USE_URMA','UCCL_GPU_STAGE_TRACE'):vf.pop(key,None);uf.pop(key,None)
        if vf!=uf:raise ValueError('different core build flags')
        if read(vn/'gpu_stage_sites.json')!=read(un/'gpu_stage_sites.json'):raise ValueError('different GPU sites')
        vr=read(vn/'runtime.json');ur=read(un/'normal/runtime.json')
        if vr['queue_backend']!=ur['queue_backend'] or vr['gpu_stage_version']!=ur['gpu_stage_version']:raise ValueError('different queue/GPU trace implementation')
        out.append(dict(node=f'node{rank}',host=vh['host'],verbs_kernel=vc['kernel'],urma_kernel=uc['kernel'],verbs_boot=vh['boot_id'],urma_boot=uh['boot_id'],verbs_libraries=vc['libraries'],urma_libraries=uc['libraries'],source_differences=changed,queue_backend=vr['queue_backend'],gpu_stage_version=vr['gpu_stage_version'],note='Separate kernel environments; comparison includes kernel/driver/provider effects. Prepared Normal benchmark checked; old URMA results do not preserve Buffer/utils source copies, so full Python provenance is not verified.'))
    return out

def compare(verbs,urma,output):
    output.mkdir(parents=True,exist_ok=True)
    verbs_summary(verbs,output/'verbs')
    subprocess.run([sys.executable,str(common.HERE.parent/'urma_mode_parity/summarize_stage_breakdown.py'),str(urma),'--output',str(output/'urma')],check=True)
    metadata=context(verbs,urma)
    (output/'comparison_context.json').write_text(json.dumps(metadata,indent=2))
    api={}
    for backend in ('verbs','urma'):
        api[backend]={(r['node'],r['label']):r for r in csv_rows(output/backend/'normal_aggregate.csv')}
    if api['verbs'].keys()!=api['urma'].keys():raise ValueError('different timing points')
    timing=[]
    for key,v in sorted(api['verbs'].items()):
        u=api['urma'][key];a=float(v['median_run_mean_us']);b=float(u['median_run_mean_us'])
        timing.append(dict(node=key[0],label=key[1],verbs_us=a,urma_us=b,urma_minus_verbs_us=b-a,urma_over_verbs=b/a,urma_slower_pct=(b/a-1)*100,verbs_min_run_us=v['min_run_mean_us'],verbs_max_run_us=v['max_run_mean_us'],urma_min_run_us=u['min_run_mean_us'],urma_max_run_us=u['max_run_mean_us']))
    write(output/'normal_comparison.csv',timing)
    gpu=collections.defaultdict(lambda:collections.defaultdict(list))
    request=collections.defaultdict(lambda:collections.defaultdict(list))
    cpu=collections.defaultdict(dict)
    for backend in ('verbs','urma'):
        for r in csv_rows(output/backend/'gpu_stage_scopes.csv'):
            if int(r['index'])==0:continue
            gpu[(r['node'],r['label'],r['name'],r['sample_lane'])][backend].append(r)
        for r in csv_rows(output/backend/'request_timeline.csv'):
            if int(r['index'])==0:continue
            for name,value in r.items():
                if value and name.endswith('_us') and name!='collect_offset_us':request[(r['node'],r['label'],name)][backend].append(float(value))
        for r in csv_rows(output/backend/'cpu_scopes.csv'):cpu[(r['node'],r['label'],r['metric'])][backend]=r
    gpu_out=[];request_out=[];cpu_out=[]
    for key,backends in sorted(gpu.items()):
        row=dict(node=key[0],label=key[1],name=key[2],sample_lane=key[3])
        for backend,rr in backends.items():
            calls=sum(int(r['calls']) for r in rr)
            row[backend+'_calls']=calls
            row[backend+'_estimated_mean_us']=sum(float(r['estimated_mean_us'])*int(r['calls']) for r in rr)/calls
            row[backend+'_estimated_max_us']=max(float(r['estimated_max_us']) for r in rr)
        gpu_out.append(row)
    for key,backends in sorted(request.items()):
        row=dict(node=key[0],label=key[1],metric=key[2])
        for backend,values in backends.items():
            row.update({backend+'_requests':len(values),backend+'_median_us':statistics.median(values),backend+'_p95_us':common.phase_support.distribution(values)['p95_us']})
        request_out.append(row)
    for key,backends in sorted(cpu.items()):
        row=dict(node=key[0],label=key[1],metric=key[2])
        for backend,r in backends.items():
            row[backend+'_calls']=r['calls'];row[backend+'_inclusive_thread_sum_us_per_api']=float(r['inclusive_thread_sum_us'])/5
        cpu_out.append(row)
    write(output/'gpu_scope_comparison.csv',gpu_out)
    write(output/'request_latency_comparison.csv',request_out)
    write(output/'cpu_scope_comparison.csv',cpu_out)
    print('STAGE_COMPARISON_PASSED output='+str(output))
    return timing

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--verbs',type=Path,required=True);p.add_argument('--urma',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();compare(a.verbs,a.urma,a.output)
