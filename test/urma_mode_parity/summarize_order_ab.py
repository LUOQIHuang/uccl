"""Compare LL WRITE order variants without pooling ranks or instrumented runs."""
import argparse
import csv
import json
from pathlib import Path
import statistics
from phase_support import distribution


def summarize(roots, output):
    groups, runs = {}, []
    seen = set()
    for root in roots:
        for path in sorted(root.rglob('sample_summary.jsonl')):
            if path.resolve() in seen: continue
            seen.add(path.resolve())
            case = path.parent
            setup = json.loads((case.parent/'setup.json').read_text())
            config = setup['fingerprint']
            status = json.loads((case/'status.json').read_text())
            if setup.get('error') or status['rc'] or not status['checks'] or not all(status['checks'].values()):
                raise ValueError('failed case: '+str(case))
            if config.get('api_only') and config.get('ll_write_order')=='default' and case.name=='normal':
                continue  # separate fixed-policy Normal control, not an order pair
            if not config.get('api_only') or config.get('ll_write_order') not in ('strong','relax'):
                raise ValueError('not an API-only order A/B case: '+str(case))
            machine = json.loads((case.parent/'host.json').read_text())
            raw = [json.loads(s) for s in (case/'api_samples.jsonl').read_text().splitlines()]
            labels = {r['label'] for r in raw}
            expected={'ll_no_hook_dispatch_combine','ll_immediate_hook_dispatch_combine'} if case.name=='low_latency' else {'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
            if labels!=expected: raise ValueError('incomplete workload labels: '+str(case))
            for label in sorted(labels):
                rows = [r for r in raw if r['label']==label]
                if len(rows)!=config['samples'] or [r['index'] for r in rows]!=list(range(config['samples'])) or [r['used'] for r in rows]!=[False]+[True]*(config['samples']-1):
                    raise ValueError('incomplete raw samples: '+str(case))
                if any(not 0 < r['us'] < float('inf') for r in rows): raise ValueError('invalid raw sample')
                stats = distribution([r['us'] for r in rows if r['used']])
                mode = rows[0]['mode']
                row = dict(case=str(case),job=config['job'],host=machine['host'],node=case.parent.name,
                    boot_id=machine['boot_id'],mode=mode,label=label,order=config['ll_write_order'],
                    instrumented=config['stage_profile'],**stats)
                runs.append(row)
                if config['stage_profile']: continue
                key = (machine['host'],case.parent.name,machine['boot_id'],mode,label,config['kernel'],
                    config['build_hash'],config.get('diagnostics_hash',''),str(config['shape']),
                    config['warmups'],config['samples'],config['ll_hook_workload'],
                    json.dumps(config.get('libraries',{}),sort_keys=True),config.get('torch',''))
                groups.setdefault(key,{'strong':[],'relax':[]})[row['order']].append(stats['mean_us'])
    comparison=[]
    for key,values in groups.items():
        strong,relax=values['strong'],values['relax']
        if not strong or not relax: raise ValueError('both order baselines required: '+str(key[:5]))
        a,b=statistics.median(strong),statistics.median(relax)
        comparison.append(dict(host=key[0],node=key[1],boot_id=key[2],mode=key[3],label=key[4],kernel=key[5],
            build_hash=key[6],diagnostics_hash=key[7],shape=key[8],strong_runs=len(strong),relax_runs=len(relax),
            strong_median_run_mean_us=a,relax_median_run_mean_us=b,relax_change_pct=(b/a-1)*100,
            strong_min_run_mean_us=min(strong),strong_max_run_mean_us=max(strong),
            relax_min_run_mean_us=min(relax),relax_max_run_mean_us=max(relax)))
    if not comparison: raise ValueError('no baseline pairs found')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('order_runs.csv',runs),('order_comparison.csv',comparison)]:
        with (output/name).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(data[0])); writer.writeheader(); writer.writerows(data)
    print('ORDER_SUMMARY_PASSED pairs='+str(len(comparison))+' output='+str(output))
    return runs,comparison


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('roots',nargs='+',type=Path); p.add_argument('--output',required=True,type=Path)
    a=p.parse_args(); summarize(a.roots,a.output)
