"""Normal completion A/B: raw sample validation, separate baselines and CQE counts."""
import argparse
import csv
import json
from pathlib import Path
import statistics
from phase_support import distribution

LABELS={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}


def read(path): return json.loads(path.read_text())
def rows(path): return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
def write(path,data):
    with path.open('w',newline='') as stream:
        if not data: return
        writer=csv.DictWriter(stream,fieldnames=sorted({k for row in data for k in row}))
        writer.writeheader();writer.writerows(data)


def summarize(roots,output,expected_repeats=3):
    setups=sorted({p.resolve() for root in roots for p in root.rglob('setup.json')})
    groups={};runs=[];diagnostics=[];pairs=[]
    if not setups: raise ValueError('no Normal A/B jobs found')
    for path in setups:
        setup=read(path);config=setup['fingerprint'];node=path.parent;case=node/'normal'
        if setup.get('error'): raise ValueError('failed setup: '+str(node))
        if config.get('mode')!='normal' or not config.get('api_only') or config.get('ll_write_order','default')!='default':
            raise ValueError('expected Normal-only API-only fixed WRITE ordering: '+str(node))
        policy=config.get('normal_completion')
        if policy not in ('all','ordered','paired'): raise ValueError('missing completion policy')
        status=read(case/'status.json')
        if status['rc'] or not status['checks'] or not all(status['checks'].values()):
            raise ValueError('failed case: '+str(case))
        machine=read(node/'host.json');raw=rows(case/'api_samples.jsonl')
        if {r['label'] for r in raw}!=LABELS: raise ValueError('incomplete measurement labels: '+str(case))
        identity=dict(host=machine['host'],node=node.name,boot_id=machine['boot_id'],policy=policy,
            job=config['job'],instrumented=config['stage_profile'])
        for label in sorted(LABELS):
            selected=[r for r in raw if r['label']==label]
            if len(selected)!=config['samples'] or [r['index'] for r in selected]!=list(range(config['samples'])) or [r['used'] for r in selected]!=[False]+[True]*(config['samples']-1):
                raise ValueError('incomplete raw samples: '+str(case)+' '+label)
            if any(r['mode']!='normal' or not 0<r['us']<float('inf') for r in selected): raise ValueError('invalid raw samples')
            summary=distribution([r['us'] for r in selected if r['used']])
            runs.append(dict(identity,label=label,**summary))
            if config['stage_profile']: continue
            key=(machine['host'],node.name,machine['boot_id'],label,config['kernel'],config['build_hash'],
                config['diagnostics_hash'],str(config['shape']),config['warmups'],config['samples'],
                json.dumps(config.get('libraries',{}),sort_keys=True),config.get('torch',''))
            values=groups.setdefault(key,{'all':[],'ordered':[],'paired':[]})
            values[policy].append(summary['mean_us'])
        if not config['stage_profile']: continue
        total_candidates=total_suppressed=total_recovery=0
        for window in rows(case/'phases.jsonl'):
            if window['label'] not in LABELS: continue
            metrics={}
            for record in window['records']:
                for name,m in record['metrics'].items():
                    value=metrics.setdefault(name,dict(calls=0,items=0,ns=0))
                    for k in value:value[k]+=m[k]
            def calls(name):return metrics.get(name,{}).get('calls',0)
            def items(name):return metrics.get(name,{}).get('items',0)
            n=window['iterations']
            total_candidates+=calls('paired_write_candidates')
            total_suppressed+=calls('write_cqe_suppressed')
            total_recovery+=calls('pair_recovery_markers')
            diagnostics.append(dict(identity,label=window['label'],iterations=n,
                write_wrs_per_iteration=calls('write_requests')/n,write_bytes_per_iteration=items('write_requests')/n,
                fadd_wrs_per_iteration=calls('fadd_requests')/n,candidates_per_iteration=calls('paired_write_candidates')/n,
                suppressed_per_iteration=calls('write_cqe_suppressed')/n,
                physical_cqes_per_iteration=calls('observed_cqes')/n,
                retired_logical_ops_per_iteration=items('retired_logical_ops')/n,
                recovery_markers=calls('pair_recovery_markers'),
                sdk_post_cpu_ms_per_iteration=metrics.get('sdk_post',{}).get('ns',0)/1e6/n))
        if total_recovery:raise ValueError('recovery marker in successful diagnostic: '+str(case))
        if policy=='paired' and (not total_candidates or not total_suppressed):
            raise ValueError('paired optimization not exercised: '+str(case))
        if policy!='paired' and total_suppressed:raise ValueError('unexpected CQE suppression: '+str(case))
    for key,values in groups.items():
        for policy in ('all','paired'):
            if len(values[policy])!=expected_repeats:
                raise ValueError(f'expected {expected_repeats} {policy} runs; found {len(values[policy])}: {key[:4]}')
        if values['ordered'] and len(values['ordered'])!=expected_repeats:raise ValueError('incomplete ordered control')
        a,b=statistics.median(values['all']),statistics.median(values['paired'])
        c=statistics.median(values['ordered']) if values['ordered'] else None
        pairs.append(dict(host=key[0],node=key[1],boot_id=key[2],label=key[3],kernel=key[4],build_hash=key[5],
            diagnostics_hash=key[6],shape=key[7],all_runs=len(values['all']),paired_runs=len(values['paired']),
            all_median_run_mean_us=a,paired_median_run_mean_us=b,paired_change_pct=(b/a-1)*100,
            all_min_run_mean_us=min(values['all']),all_max_run_mean_us=max(values['all']),
            paired_min_run_mean_us=min(values['paired']),paired_max_run_mean_us=max(values['paired']),
            ordered_runs=len(values['ordered']),ordered_median_run_mean_us=c if c else '',
            ordered_change_pct=(c/a-1)*100 if c else '',paired_vs_ordered_pct=(b/c-1)*100 if c else ''))
    if not pairs: raise ValueError('no uninstrumented Normal baseline pairs')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('normal_runs.csv',runs),('normal_comparison.csv',pairs),('normal_cqe_counts.csv',diagnostics)]:write(output/name,data)
    print('NORMAL_SUMMARY_PASSED groups='+str(len(pairs))+' output='+str(output))
    return runs,pairs,diagnostics


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('roots',nargs='+',type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--expected-repeats',type=int,default=3)
    a=p.parse_args()
    if a.expected_repeats<1:p.error('expected-repeats must be positive')
    summarize(a.roots,a.output,a.expected_repeats)
