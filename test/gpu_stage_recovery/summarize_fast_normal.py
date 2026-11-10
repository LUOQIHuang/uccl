"""Compare one uninstrumented run with CPU-only scopes, retaining overhead."""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
LABELS={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def summarize(root,job,output):
    api=[];scopes=[];identities=collections.defaultdict(list)
    for rank in (0,1):
        means={};cvs={}
        for kind in ('baseline','cpu'):
            node=root/(job+'_'+kind)/f'node{rank}';c=read(node/'setup.json')
            if c['error']:raise ValueError('setup failed')
            c=c['fingerprint'];verbs=c.get('variant')=='current'
            case=node/(('cpu_diagnostic' if kind=='cpu' else 'r01_baseline')+'/normal') if verbs else node/'normal'
            status=read(case/'status.json')
            if status['rc'] or not status['checks'] or not all(status['checks'].values()):raise ValueError('failed case')
            runtime=read(case/'runtime.json');manifest=read(node/('module_manifest.json' if verbs else 'build_manifest.json'))
            if runtime.get('gpu_stage_version',0)!=0 or manifest['flags'].get('UCCL_GPU_STAGE_TRACE','0')!='0' or c.get('timeline') or c.get('capture_layout') or c.get('gpu_stages'):raise ValueError('not light CPU/base experiment')
            if c['mode']!='normal' or c['warmups']!=50 or c['samples']!=50 or (c['cpu_diagnostics'] if verbs else c['stage_profile'])!=(kind=='cpu'):raise ValueError('sampling/phase mismatch')
            if verbs:
                if kind=='cpu' and not c.get('cpu_light'):raise ValueError('layout tracing active')
                if read(case/'config.json')['shape']!=dict(tokens=4096,hidden=7168,experts=256,topk=8):raise ValueError('shape mismatch')
            elif c['shape']!=[4096,128,7168,256,8] or not c['api_only']:raise ValueError('shape/profile mismatch')
            identities[rank].append((manifest['sources'],manifest['flags'],runtime['module_sha'],read(node/'host.json')['boot_id'],c['kernel'],c['torch']))
            raw=rows(case/'api_samples.jsonl')
            if len(raw)!=250 or {r['label'] for r in raw}!=LABELS:raise ValueError('missing API samples')
            means[kind]={};cvs[kind]={}
            for label in LABELS:
                rr=[r for r in raw if r['label']==label]
                if [r['index'] for r in rr]!=list(range(50)) or any(r['used']!=(r['index']>0) or not 0<r['us']<float('inf') for r in rr):raise ValueError('sample boundary/value mismatch')
                values=[r['us'] for r in rr if r['used']]
                means[kind][label]=statistics.mean(values)
                cvs[kind][label]=statistics.pstdev(values)/means[kind][label]*100
            if kind=='cpu':
                for w in rows(case/('verbs_phases.jsonl' if verbs else 'phases.jsonl')):
                    totals=collections.defaultdict(collections.Counter)
                    for record in w['records']:
                        for name,m in record['metrics'].items():
                            for k in ('calls','ns','items'):totals[name][k]+=m[k]
                    for name,m in totals.items():scopes.append(dict(node=rank,label=w['label'],metric=name,calls_per_api=m['calls']/50,items_per_api=m['items']/50,inclusive_thread_us_per_api=m['ns']/50000,mean_scope_us=m['ns']/1000/m['calls'] if m['calls'] else ''))
        if identities[rank][0]!=identities[rank][1]:raise ValueError('base/CPU identity or boot changed')
        for label in LABELS:
            a=means['baseline'][label];b=means['cpu'][label];overhead=(b/a-1)*100
            api.append(dict(node=rank,label=label,baseline_us=a,cpu_diagnostic_us=b,baseline_cv_pct=cvs['baseline'][label],cpu_cv_pct=cvs['cpu'][label],diagnostic_delta_pct=overhead,perturbed=abs(overhead)>5))
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('api_overhead.csv',api),('cpu_scopes.csv',scopes)]:
        with (output/name).open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=sorted({k for r in data for k in r}));writer.writeheader();writer.writerows(data)
    print('FAST_NORMAL_SUMMARY_PASSED '+str(output))
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--job',required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.job,a.output)
