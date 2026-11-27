"""Compare native Normal Dispatch with full and half reverse payload load."""

import argparse
import json
from pathlib import Path
from statistics import mean, pstdev

from summarize_same_kernel_gap import KERNEL, LABEL, base, shared_signature

BACKENDS = {'rdma': 'verbs', 'urma': 'urma'}
PHASES = ('pre', 'half', 'post')
PREFIX = 'NORMAL_REVERSE_LOAD '


def routing(case, rank, phase):
    tag = 'half_reverse' if phase=='half' else 'both'
    records = [json.loads(line[len(PREFIX):]) for line in (case/'bench.log').read_text(errors='replace').splitlines()
               if line.startswith(PREFIX)]
    if len(records)!=1:
        raise ValueError('expected one recorded Normal routing input')
    row = records[0]
    if (row['load']!=tag or row['rank']!=rank or not 0<row['remote_before']<=4096
            or not 0<row['local']<=4096 or not 0<=row['eligible']<=min(row['remote_before'],row['local'])):
        raise ValueError('invalid Normal routing counters')
    dropped = row['eligible']//2 if phase=='half' and rank==0 else 0
    if row['remote_after']!=row['remote_before']-dropped:
        raise ValueError('unexpected remote token count')
    if phase=='half' and rank==0 and not 0.4<=row['remote_after']/row['remote_before']<=0.6:
        raise ValueError('reverse payload was not approximately halved')
    if (row['active_sha']!=row['original_sha'])!=bool(dropped):
        raise ValueError('routing fingerprint and workload intervention disagree')
    return row


def timings(backend, node, phases):
    pre, half, post = (mean(phases[p]) for p in PHASES)
    baseline = (pre+post)/2
    drift = 100*(post-pre)/baseline
    cv = max(100*pstdev(v)/mean(v) for v in phases.values())
    return dict(backend=backend,node=node,pre_us=pre,half_us=half,post_us=post,full_mean_us=baseline,
        half_minus_full_us=half-baseline,baseline_drift_pct=drift,max_cv_pct=cv,
        stable=abs(drift)<=5 and cv<=5)


def summarize(root, output):
    signatures, full_routes, active_routes = {}, {}, {}
    controls, geometry = [], []
    for prefix,backend in BACKENDS.items():
        samples = {}
        for rank in (0,1):
            local = None
            for phase in PHASES:
                name = f'{prefix}_{phase}'
                data = base.case_data(root/name,rank,False,reference_events='paced')
                ident = data['identity']
                if (data['c']['job']!=name or ident['kernel']!=KERNEL or ident['backend']!=backend
                        or ident['reference_profile']!='legacy' or ident['normal_window_plan']!='default'
                        or ident['normal_retire_plan']!='ordered'):
                    raise ValueError(f'wrong runtime for {name}, rank {rank}')
                if backend=='urma' and (ident['normal_fadd_fence_plan']!='peer-tail-off'
                        or ident['normal_submit_schedule']!='lane-mixed'):
                    raise ValueError('expected URMA with only 204 tail-off')
                if backend=='verbs' and not base.read(data['case']/'status.json')['checks'].get('dmabuf'):
                    raise ValueError('GPU DMA-BUF not verified')
                current = (ident,data['runtime']['module_sha'])
                if local is not None and current!=local:
                    raise ValueError('source, module, runtime or boot changed')
                local = current
                shared = shared_signature(ident)
                if signatures.setdefault(rank,shared)!=shared:
                    raise ValueError('cross-backend workload, boot or runtime changed')
                row = routing(data['case'],rank,phase)
                route = {k:row[k] for k in ('original_sha','remote_before','local','eligible')}
                if full_routes.setdefault(rank,route)!=route:
                    raise ValueError('unmodified routing differs across rounds or backends')
                if active_routes.setdefault((rank,phase),row)!=row:
                    raise ValueError('actual routing differs across backends')
                geometry.append(dict(backend=backend,phase=phase,**row))
                samples[rank,phase] = {r['index']:r['us'] for r in base.rows(data['case']/'api_samples.jsonl')
                                       if r['label']==LABEL and r['used']}
            controls.append(timings(backend,rank,{p:list(samples[rank,p].values()) for p in PHASES}))
        controls.append(timings(backend,'paired_max',
            {p:[max(samples[0,p][i],samples[1,p][i]) for i in range(1,10)] for p in PHASES}))

    comparisons = []
    for node in (0,1,'paired_max'):
        r,u = (next(row for row in controls if row['backend']==backend and row['node']==node)
               for backend in ('verbs','urma'))
        full_gap, half_gap = (u[key]-r[key] for key in ('full_mean_us','half_us'))
        comparisons.append(dict(node=node,full_gap_us=full_gap,half_gap_us=half_gap,reduced_gap_us=full_gap-half_gap,
                                full_pre_gap_us=u['pre_us']-r['pre_us'],full_post_gap_us=u['post_us']-r['post_us']))
    decision = dict(controls_stable=all(row['stable'] for row in controls),paired_max=comparisons[-1],
        note='Each rank keeps its local routing. Only 202-to-204 payload routes are reduced. '
             'The counterfactual changes receiver work on 204 and any contention or feedback it causes. '
             'Gap change is workload sensitivity, not a measured device-stage cost or a production improvement.')
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('controls',controls),('routing',geometry),('comparison',comparisons)]:
        base.write(output/f'{name}.csv',rows)
    (output/'decision.json').write_text(json.dumps(decision,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(decision,indent=2))


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    summarize(args.root,args.output)
