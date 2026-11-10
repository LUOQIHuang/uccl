"""Validate paired Normal WRITE placement A/B; never mix nodes or diagnostics."""
import argparse
import collections
import csv
import json
import math
import re
from pathlib import Path
import statistics
from phase_support import distribution

LABELS={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
KERNEL='6.6.0-gdr_f00955680_diag'
REQUIRED_CHECKS={'correctness','write_order','native_mode','normal_completion','samples','no_gpu_profiler','measurement_boundary','done','mode','exit_ack','no_errors'}

def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def signature(value):return json.dumps(value,sort_keys=True)
def write(p,data):
    with p.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(data[0]));writer.writeheader();writer.writerows(data)

def summarize(root,job,output,repeats=3):
    if not 1<=repeats<=10:raise ValueError('repeats must be 1..10')
    groups=collections.defaultdict(dict);identities=collections.defaultdict(set);runs=[];contexts=[]
    expected={f'{job}_r{r:02d}_{order}' for r in range(1,repeats+1) for order in ('relax','strong')}
    observed={p.parent.parent.name for p in root.glob(job+'_r*/node*/setup.json')}
    if observed!=expected:raise ValueError('missing or unexpected A/B jobs')
    for r in range(1,repeats+1):
        for order in ('relax','strong'):
            for rank in (0,1):
                node=root/f'{job}_r{r:02d}_{order}'/f'node{rank}';case=node/'normal'
                setup=read(node/'setup.json');c=setup['fingerprint'];status=read(case/'status.json')
                if setup['error'] or status['rc'] or not REQUIRED_CHECKS.issubset(status['checks']) or not all(status['checks'].values()):raise ValueError('failed or incomplete case '+str(case))
                if c['job']!=node.parent.name or c['backend']!='urma' or c['mode']!='normal' or c['kernel']!=KERNEL:raise ValueError('wrong backend/mode/kernel')
                if not c['phase_suite'] or not c['api_only'] or any(c.get(k,False) for k in ('stage_profile','save_traces','capture_layout','timeline','gpu_stages','dispatch_trial')):raise ValueError('not uninstrumented API-only A/B')
                if c['normal_write_order']!=order or c['ll_write_order']!='default' or c['normal_completion']!='all':raise ValueError('wrong ordering/completion policy')
                if c['shape']!=[4096,128,7168,256,8] or (c['warmups'],c['samples'])!=(50,50):raise ValueError('wrong workload/sampling')
                runtime=read(case/'runtime.json');pre=read(node/'preflight.json');manifest=read(node/'build_manifest.json');host=read(node/'host.json')
                for rt in (runtime,pre['runtime']):
                    if rt.get('normal_write_order')!=order or rt.get('ll_write_order')!='strong' or rt.get('normal_completion')!='all' or rt.get('gpu_stage_version',0)!=0:raise ValueError('runtime policy/capability mismatch')
                    if rt['module_sha']!=manifest['module_sha'] or rt['torch']!=c['torch']:raise ValueError('runtime module/Torch mismatch')
                    if not rt.get('selected_runtime_hashes') or rt['selected_runtime_hashes']!=c['runtime_libraries']:raise ValueError('actual MACA runtime libraries changed or unrecorded')
                if manifest['flags'].get('USE_URMA')!='1' or manifest['flags'].get('UCCL_GPU_STAGE_TRACE','0')!='0':raise ValueError('wrong build flags')
                identity={k:v for k,v in c.items() if k not in ('job','normal_write_order')}
                gpu=(case/'gpu_before.txt').read_text()
                versions=re.findall(r'(?:Kernel Mode Driver Version|MACA Version)\s*:\s*([^|\n]+)',gpu)
                if len(versions)!=2:raise ValueError('missing GPU driver/runtime version snapshot')
                identity.update(manifest=manifest,boot=host['boot_id'],host=host['host'],hardware=pre['hardware'],queue_backend=runtime['queue_backend'],gpu_versions=[v.strip() for v in versions])
                identities[rank].add(signature(identity))
                if len(identities[rank])!=1:raise ValueError('A/B changed build, libraries, boot, hardware or workload')
                if runtime['queue_backend']!='fifo':raise ValueError('expected FIFO backend')
                if r==1 and order=='relax':contexts.append(dict(node=rank,identity=identity))
                raw=rows(case/'api_samples.jsonl');saved=rows(case/'sample_summary.jsonl')
                if len(raw)!=250 or {x['label'] for x in raw}!=LABELS or len(saved)!=5 or {x['label'] for x in saved}!=LABELS:raise ValueError('incomplete timing points')
                for label in sorted(LABELS):
                    rr=[x for x in raw if x['label']==label]
                    if len(rr)!=50 or [x['index'] for x in rr]!=list(range(50)) or any(x['used']!=(x['index']>0) or x['mode']!='normal' or not math.isfinite(x['us']) or x['us']<=0 for x in rr):raise ValueError('bad sample boundary/value')
                    stats=distribution([x['us'] for x in rr if x['used']]);s=next(x for x in saved if x['label']==label)
                    if any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('saved/raw summary mismatch')
                    runs.append(dict(node=rank,repeat=r,order=order,label=label,**stats))
                    groups[rank,label].setdefault(order,{})[r]=stats['mean_us']
    comparison=[]
    for (rank,label),g in sorted(groups.items()):
        a=list(g['relax'].values());b=list(g['strong'].values());am=statistics.median(a);bm=statistics.median(b)
        deltas=[(g['strong'][r]/g['relax'][r]-1)*100 for r in range(1,repeats+1)]
        comparison.append(dict(node=rank,label=label,repeats=repeats,relax_us=am,strong_us=bm,strong_minus_relax_us=bm-am,strong_change_pct=(bm/am-1)*100,
            relax_min_run_us=min(a),relax_max_run_us=max(a),strong_min_run_us=min(b),strong_max_run_us=max(b),
            paired_delta_median_pct=statistics.median(deltas),paired_delta_min_pct=min(deltas),paired_delta_max_pct=max(deltas),
            paired_direction_consistent=all(x>0 for x in deltas) or all(x<0 for x in deltas),run_ranges_overlap=max(min(a),min(b))<=min(max(a),max(b))))
    output.mkdir(parents=True,exist_ok=True);write(output/'order_runs.csv',runs);write(output/'order_comparison.csv',comparison)
    (output/'context.json').write_text(json.dumps(dict(contexts=contexts,note='Only Normal WRITE placement differs. Negative strong_change_pct means STRONG faster. Run ranges and paired signs are descriptive, not a significance test. One repeat is a smoke test; no GPU-internal root-cause claim.'),indent=2))
    print('NORMAL_ORDER_SUMMARY_PASSED '+str(output));return runs,comparison

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--job',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--repeats',type=int,default=3)
    a=p.parse_args();summarize(a.root,a.job,a.output,a.repeats)
