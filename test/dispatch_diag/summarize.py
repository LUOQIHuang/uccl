"""Validate baseline/Dispatch trace/post controls and compare two kernel environments."""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from dispatch_checks import validate_gpu,LABELS,SITES

ALL_LABELS=LABELS|{'layout','combine_chunk12','combine_chunk16'}
REQUIRED_CHECKS={'done','mode','exit_ack','no_errors','samples','correctness','profiles','measurement_boundary'}
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def sig(x):return json.dumps(x,sort_keys=True)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,data):
    with p.open('w',newline='') as f:
        if data:
            w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)

def case_data(root,rank,trace,compiled_off=False,reference_events=None):
    node=root/f'node{rank}';c=read(node/'setup.json')
    if c['error']:raise ValueError('failed setup '+str(node))
    c=c['fingerprint'];backend=c.get('backend','verbs')
    if read(node/'dispatch_sites.json')!=SITES:raise ValueError('different recorded Dispatch sites')
    case=node/'normal' if backend=='urma' else node/'r01_baseline/normal'
    status=read(case/'status.json');runtime=read(case/'runtime.json')
    if status['rc'] or not REQUIRED_CHECKS.issubset(status['checks']) or not all(status['checks'].values()):raise ValueError('failed/incomplete case '+str(case))
    if not c.get('dispatch_focus') or c['mode']!='normal' or c.get('gpu_stages',False)!=trace or c.get('timeline') or c.get('stage_profile') or c.get('cpu_diagnostics') or c.get('capture_layout') or c.get('dispatch_trial') or c.get('save_traces'):raise ValueError('wrong focused configuration')
    if (trace and compiled_off) or bool(c.get('dispatch_compiled_off'))!=compiled_off:raise ValueError('wrong compiled-off configuration')
    if runtime.get('dispatch_trace_version')!=1 or runtime['queue_backend']!='fifo' or runtime['gpu_stage_version']!=(2 if trace or compiled_off else 0):raise ValueError('wrong runtime capability')
    if c.get('reference_events')!=reference_events or runtime.get('reference_events_version',0)!=int(bool(reference_events)):raise ValueError('wrong reference event configuration')
    shape_ok=c['normal_tokens']==4096 if backend=='verbs' else c['shape']==[4096,128,7168,256,8]
    if not shape_ok:raise ValueError('formal comparison requires fixed 4096 shape')
    if (c['warmups'],c['samples'])!=((5,10) if trace or reference_events=='on' else (50,50)):raise ValueError('wrong formal samples')
    if backend=='urma':
        if not c['api_only'] or c['normal_completion']!='all' or c['normal_write_order']!='default' or c['ll_write_order']!='default' or runtime.get('normal_write_order')!='relax':raise ValueError('changed URMA semantics')
    elif c['variant']!='current' or not c['no_profile'] or c['repeats']!=1:raise ValueError('wrong verbs configuration')
    if trace:
        if c['gpu_stage_mode']!='dispatch' or not status['checks'].get('dispatch_coverage'):raise ValueError('missing focused coverage acceptance')
        validate_gpu(case,10,'dispatch',write_report=False)
    elif (case/'gpu_stages.jsonl').exists():raise ValueError('baseline was instrumented')
    if any((case/f).exists() for f in ('verbs_timeline.jsonl','urma_timeline.jsonl','write_layout.jsonl')) or any(case.glob('gpu_trace_*.json')):raise ValueError('unexpected CPU/layout/profiler tracing')
    if (case/'phases.jsonl').exists() and any(rec['metrics'] for w in rows(case/'phases.jsonl') for rec in w['records']):raise ValueError('CPU counters active')
    manifest=read(node/('build_manifest.json' if backend=='urma' else 'module_manifest.json'))
    flags=dict(manifest['flags']);compiled=flags.pop('UCCL_GPU_STAGE_TRACE','0')
    roles=flags.pop('UCCL_GPU_STAGE_ROLES_ONLY','0')
    if flags.pop('UCCL_REFERENCE_EVENTS','0')!=str(int(bool(reference_events))):raise ValueError('wrong event compile flag')
    if roles not in ('0','1') or roles!=str(int(bool(c.get('gpu_stage_roles_only')))) or (roles=='1' and not compiled_off):raise ValueError('wrong sparse probe build or unvalidated sparse capture')
    if compiled!=('1' if trace or compiled_off else '0') or manifest['module_sha']!=runtime['module_sha']:raise ValueError('module/compile flag mismatch')
    if reference_events=='on':
        import sys
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'reference_events'))
        from event_runtime import validate,correctness_passed
        if not status['checks'].get('reference_events') or not status['checks'].get('reference_correctness') or not correctness_passed(case) or not validate(case,10):raise ValueError('unvalidated reference events/correctness')
    elif (case/'event_trace.jsonl').exists():raise ValueError('unexpected reference events')
    raw=rows(case/'api_samples.jsonl');saved=rows(case/'sample_summary.jsonl');means={}
    if len(raw)!=5*c['samples'] or {r['label'] for r in raw}!=ALL_LABELS or len(saved)!=5 or {r['label'] for r in saved}!=ALL_LABELS:raise ValueError('incomplete Normal samples')
    for label in ALL_LABELS:
        rr=[r for r in raw if r['label']==label]
        if [r['index'] for r in rr]!=list(range(c['samples'])) or any(r['used']!=(r['index']>0) or r['mode']!='normal' or not math.isfinite(r['us']) or r['us']<=0 for r in rr):raise ValueError('invalid sampling boundary/value')
        values=[r['us'] for r in rr if r['used']];mean=statistics.mean(values)
        if not math.isclose(next(r for r in saved if r['label']==label)['mean_us'],mean,rel_tol=1e-9):raise ValueError('saved/raw mismatch')
        means[label]=dict(mean_us=mean,cv_pct=statistics.pstdev(values)/mean*100)
    host=read(node/'host.json');gpu=(case/'gpu_before.txt').read_text()
    versions=re.findall(r'(?:Kernel Mode Driver Version|MACA Version)\s*:\s*([^|\n]+)',gpu)
    if len(versions)!=2:raise ValueError('missing GPU version snapshot')
    libraries=runtime.get('selected_runtime_hashes',{}) if backend=='urma' else {k:v for k,v in runtime['selected_library_hashes'].items() if k.startswith('libmcruntime.so')}
    libraries={k:v for k,v in libraries.items() if k.startswith('libmcruntime.so')}
    if not libraries:raise ValueError('actual MACA runtime unrecorded')
    bench=node/('normal.py' if backend=='urma' else 'benchmark/test_internode.py')
    identity=dict(backend=backend,host=host['host'],boot=host['boot_id'],kernel=c['kernel'],torch=runtime['torch'],sources=manifest['sources'],flags=flags,sdk=manifest.get('sdk',{}),libraries=c['libraries'],maca=libraries,gpu_versions=[v.strip() for v in versions],benchmark_sha=sha(bench),diagnostics=c.get('diagnostics_hash',c.get('diagnostics')))
    for k in ('ifname','gpu','gid_index','hca','device'):identity[k]=c.get(k)
    return dict(node=node,case=case,c=c,identity=identity,runtime=runtime,means=means)

def summarize(baseline,trace,post,output):
    api=[];scopes=[];role_rows=[];contexts=[]
    for rank in (0,1):
        b,t,p=[case_data(root,rank,on) for root,on in [(baseline,False),(trace,True),(post,False)]]
        if not sig(b['identity'])==sig(t['identity'])==sig(p['identity']) or b['runtime']['module_sha']!=p['runtime']['module_sha']:raise ValueError('build/source/runtime/boot changed between controls and trace')
        contexts.append(dict(node=rank,**t['identity']))
        for label in sorted(LABELS):
            bv,tv,pv=[x['means'][label]['mean_us'] for x in (b,t,p)];reference=(bv+pv)/2
            drift=(pv/bv-1)*100;perturb=(tv/reference-1)*100
            api.append(dict(node=rank,label=label,baseline_us=bv,trace_us=tv,post_us=pv,baseline_cv_pct=b['means'][label]['cv_pct'],trace_cv_pct=t['means'][label]['cv_pct'],post_cv_pct=p['means'][label]['cv_pct'],baseline_drift_pct=drift,diagnostic_change_pct=perturb,representative=abs(drift)<=5 and abs(perturb)<=5))
        windows=rows(t['case']/'gpu_stages.jsonl');by=collections.defaultdict(dict)
        for w in windows:
            if w['index']==0:continue
            for r in w['records']:
                key=(w['label'],r['block'],r['warp'],r['sample_lane'],r['stage']);by[key][w['index']]=dict(total_us=r['sum_cycles']*1000/w['nominal_clock_khz'],calls=r['calls'],max_call_us=r['max_cycles']*1000/w['nominal_clock_khz'])
        stage_groups=collections.defaultdict(list)
        for (label,block,warp,lane,stage),samples in sorted(by.items()):
            # Include zero-call windows for conditionally executed scopes.
            values=[samples.get(i,{}).get('total_us',0) for i in range(1,10)]
            scopes.append(dict(node=rank,label=label,block=block,warp=warp,lane=lane,stage=stage,name=SITES['sites'][str(stage)],observed_windows=len(samples),windows=9,median_thread_total_us=statistics.median(values),min_thread_total_us=min(values),max_thread_total_us=max(values),median_calls=statistics.median([samples.get(i,{}).get('calls',0) for i in range(1,10)])))
            stage_groups[label,stage].append(values)
        for (label,stage),values in sorted(stage_groups.items()):
            maxima=[max(v[i] for v in values) for i in range(9)]
            role_rows.append(dict(node=rank,label=label,stage=stage,name=SITES['sites'][str(stage)],median_max_thread_us=statistics.median(maxima),min_max_thread_us=min(maxima),max_max_thread_us=max(maxima)))
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('api_controls.csv',api),('scopes_by_thread.csv',scopes),('scope_maxima.csv',role_rows)]:write(output/name,data)
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,sites=SITES,note='Scopes are inclusive/overlapping per-thread clock deltas, not additive critical-path stages. Nominal-clock microseconds are estimates. Conditional absence is zero recorded calls, not proof a dependency is unnecessary.'),indent=2))
    print('DISPATCH_DIAG_SUMMARY_PASSED '+str(output));return api,role_rows

def compare(verbs,urma,output):
    v=read(verbs/'context.json');u=read(urma/'context.json')
    if v['sites']!=u['sites']:raise ValueError('different GPU sites')
    for a,b in zip(v['nodes'],u['nodes']):
        if a['backend']!='verbs' or b['backend']!='urma':raise ValueError('backend mismatch')
        for key in ('node','host','torch','maca','benchmark_sha'):
            if a[key]!=b[key]:raise ValueError('cross-backend environment/workload mismatch: '+key)
        if a['gpu_versions'][1]!=b['gpu_versions'][1]:raise ValueError('different user-space MACA versions')
        compiled=lambda c:{k:v for k,v in c['sources'].items() if Path(k).suffix in ('.h','.hpp','.cuh','.cpp','.cc','.cu') or k=='ep/setup.py'}
        if compiled(a)!=compiled(b):raise ValueError('different compiled source inputs')
        for flag in ('USE_METAX','USE_DMABUF','MAX_NUM_GPUS','NUM_MAX_NVL_PEERS','PER_EXPERT_BATCHING'):
            if a['flags'].get(flag)!=b['flags'].get(flag):raise ValueError('different core build flags')
    def csv_rows(p):
        with p.open(newline='') as f:return list(csv.DictReader(f))
    controls=[r for root in (verbs,urma) for r in csv_rows(root/'api_controls.csv')]
    if any(r['representative']!='True' for r in controls):raise ValueError('diagnostic perturbation or baseline drift >5%; inspect separately')
    vv={(r['node'],r['label'],r['stage']):r for r in csv_rows(verbs/'scope_maxima.csv')};uu={(r['node'],r['label'],r['stage']):r for r in csv_rows(urma/'scope_maxima.csv')}
    data=[]
    for key in sorted(vv.keys()|uu.keys()):
        a=vv.get(key);b=uu.get(key)
        av=float(a['median_max_thread_us']) if a else 0;bv=float(b['median_max_thread_us']) if b else 0
        data.append(dict(node=key[0],label=key[1],stage=key[2],name=(a or b)['name'],verbs_recorded=a is not None,urma_recorded=b is not None,verbs_max_thread_us=av,urma_max_thread_us=bv,urma_minus_verbs_us=bv-av))
    output.mkdir(parents=True,exist_ok=True);write(output/'scope_comparison.csv',data)
    va={(r['node'],r['label']):r for r in csv_rows(verbs/'api_controls.csv')};ua={(r['node'],r['label']):r for r in csv_rows(urma/'api_controls.csv')}
    api=[]
    if va.keys()!=ua.keys():raise ValueError('different API comparison points')
    for key,a in sorted(va.items()):
        b=ua[key];av=(float(a['baseline_us'])+float(a['post_us']))/2;bv=(float(b['baseline_us'])+float(b['post_us']))/2
        api.append(dict(node=key[0],label=key[1],verbs_control_us=av,urma_control_us=bv,urma_minus_verbs_us=bv-av,urma_slower_pct=(bv/av-1)*100))
    write(output/'api_comparison.csv',api)
    (output/'context.json').write_text(json.dumps(dict(verbs=v,urma=u,note='Different kernel/device/driver stacks remain. Scope differences identify candidates; no cross-machine clock subtraction or sum into end-to-end latency.'),indent=2))
    print('DISPATCH_DIAG_COMPARISON_PASSED '+str(output))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',type=Path);p.add_argument('--trace',type=Path);p.add_argument('--post',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--verbs-summary',type=Path);p.add_argument('--urma-summary',type=Path)
    a=p.parse_args()
    if a.verbs_summary and a.urma_summary:compare(a.verbs_summary,a.urma_summary,a.output)
    elif all((a.baseline,a.trace,a.post)):summarize(a.baseline,a.trace,a.post,a.output)
    else:p.error('supply baseline/trace/post, or both backend summaries')
