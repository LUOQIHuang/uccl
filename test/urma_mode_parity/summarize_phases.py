#!/usr/bin/env python3
"""Summarize independent runs, instrumentation overhead and inclusive CPU stages."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def read(path): return json.loads(path.read_text())
def rows(path): return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
def write(path,data,fields):
    with path.open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields); writer.writeheader(); writer.writerows(data)


def summarize(roots,output):
    paths=sorted({p.resolve() for root in roots for p in root.rglob('sample_summary.jsonl')})
    if not paths: raise ValueError('no phase-suite samples found')
    runs=[]; stages=[]; groups={}
    for path in paths:
        case=path.parent; run=case.parent
        config=read(run/'setup.json')['fingerprint']; machine=read(run/'host.json'); host=machine['host']
        if config.get('dispatch_trial'):raise ValueError('dispatch trial requires summarize_dispatch_trial.py')
        status=read(case/'status.json')
        if status['rc']!=0 or not all(status['checks'].values()):
            raise ValueError('failed/incomplete case: '+str(case))
        identity=dict(case=str(case),host=host,job=config['job'],ll_write_order=config.get('ll_write_order','default'),api_only=config.get('api_only',False),normal_completion=config.get('normal_completion','all'),capture_layout=config.get('capture_layout',False))
        for row in rows(path):
            runs.append(dict(**identity,**row))
            # Never pool sample iterations or both ranks as independent runs.
            key=(host,row['mode'],row['label'],str(config['shape']),config['kernel'],
                 config['build_hash'],config['ll_hook_workload'],row['warmups'],row['measured'],
                 config.get('diagnostics_hash',''),machine.get('boot_id','unknown'),
                 json.dumps(config.get('libraries',{}),sort_keys=True),config.get('torch','unknown'),
                 config.get('ll_write_order','default'),config.get('api_only',False),config.get('normal_completion','all'),config.get('capture_layout',False))
            groups.setdefault(key,{False:[],True:[]})[row['enabled']].append(row['mean_us'])
        for window in rows(case/'phases.jsonl'):
            totals={}
            for record in window['records']:
                for name,value in record['metrics'].items():
                    t=totals.setdefault(name,dict(calls=0,ns=0,items=0,histogram=[0]*32))
                    for k in ('calls','ns','items'): t[k]+=value[k]
                    t['histogram']=[a+b for a,b in zip(t['histogram'],value['histogram'])]
            for name,value in totals.items():
                count=sum(value['histogram']); limit=count*.95; cumulative=0; p95=''
                if count:
                    for i,n in enumerate(value['histogram']):
                        cumulative+=n
                        if cumulative>=limit: p95=2**(i+1); break
                stages.append(dict(**identity,label=window['label'],mode=window['mode'],stage=name,
                    iterations=window['iterations'],calls=value['calls'],total_ms=value['ns']/1e6,
                    items=value['items'],avg_us=value['ns']/value['calls']/1000 if value['calls'] else 0,
                    sampled_p95_upper_us=p95))
    comparisons=[]
    for key,values in sorted(groups.items()):
        off,on=values[False],values[True]
        baseline=statistics.median(off) if off else None
        instrumented=statistics.median(on) if on else None
        comparisons.append(dict(host=key[0],mode=key[1],label=key[2],shape=key[3],kernel=key[4],
            build_hash=key[5],hook_workload=key[6],warmups=key[7],measured=key[8],
            diagnostics_hash=key[9],boot_id=key[10],libraries=key[11],torch=key[12],
            ll_write_order=key[13],api_only=key[14],
            normal_completion=key[15],
            capture_layout=key[16],
            baseline_runs=len(off),instrumented_runs=len(on),
            baseline_median_us=baseline,instrumented_median_us=instrumented,
            baseline_run_min_us=min(off) if off else '',baseline_run_max_us=max(off) if off else '',
            instrumented_run_min_us=min(on) if on else '',instrumented_run_max_us=max(on) if on else '',
            instrumentation_change_pct=(instrumented/baseline-1)*100 if baseline and instrumented else ''))
    output.mkdir(parents=True,exist_ok=True)
    write(output/'runs.csv',runs,list(runs[0]))
    write(output/'overhead.csv',comparisons,list(comparisons[0]))
    write(output/'stages.csv',stages,list(stages[0]) if stages else ['case','stage'])
    print(f'PHASE_SUMMARY_PASSED cases={len(paths)} output={output}')
    return runs,comparisons,stages


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('roots',type=Path,nargs='+'); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); summarize(a.roots,a.output)
