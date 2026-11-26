"""Verify actual sixth-WRITE deferral and measure its effect on peer Dispatch."""

import argparse
import json
from pathlib import Path
from statistics import mean, median, pstdev

from summarize_same_kernel_gap import KERNEL, LABEL, base, drift_pct

CASES = {'a1': 0, 'b1': 300, 'b2': 300, 'a2': 0}
CHANNELS = {(p, r) for p in range(4) for r in range(3)}


def gate_rows(case, raw, name, delay):
    rows = base.rows(case/'chunk_gate.jsonl')
    if [w['index'] for w in rows] != sorted(raw):
        raise ValueError(f'missing gate observations: {name}')
    for window in rows:
        index = window['index']
        if (window['label'] != LABEL or window['payload'] != 6 or window['delay_us'] != delay
                or window['us'] != raw[index]):
            raise ValueError(f'gate settings or API sample mismatch: {name}, sample {index}')
        channels = window['channels']
        if len(channels) != len(CHANNELS) or {(w['proxy'], w['ring']) for w in channels} != CHANNELS:
            raise ValueError(f'missing gate channel: {name}, sample {index}')
        for wr in channels:
            elapsed = wr['end_ns'] - wr['begin_ns']
            if wr['wr_id'] >> 32 != wr['ring'] or elapsed < delay*1000 or (delay==0 and elapsed!=0):
                raise ValueError(f'invalid gate effect: {name}, sample {index}')
            if index:
                yield dict(case=name, index=index, **wr, delay_us=elapsed/1000)


def summarize(root, output):
    overlay = base.read(root/'overlay_node0.json')
    if overlay != base.read(root/'overlay_node1.json'):
        raise ValueError('different chunk-gate source trees')
    expected_sources = {k:v for k,v in overlay['sources'].items() if k.startswith('ep/')}
    identities, rounds = {}, {}
    waits, api = [], []
    for name, delay in CASES.items():
        durations = []
        for rank in (0,1):
            data = base.case_data(root/name, rank, False, reference_events='paced')
            ident = data['identity']
            if (data['c']['job'] != name or ident['kernel'] != KERNEL or ident['backend'] != 'urma'
                    or ident['normal_fadd_fence_plan'] != 'peer-tail-off' or ident['normal_retire_plan'] != 'ordered'
                    or ident['normal_window_plan'] != 'default' or ident['normal_submit_schedule'] != 'lane-mixed'
                    or ident['reference_profile'] != 'legacy'):
                raise ValueError(f'wrong runtime experiment setup: {name}, rank {rank}')
            if any(ident['sources'].get(k)!=v for k,v in expected_sources.items()):
                raise ValueError('wrong chunk-gate module source')
            signature = (ident, data['runtime']['module_sha'])
            if identities.setdefault(rank,signature) != signature:
                raise ValueError('changed module, settings or boot within experiment')
            samples = {row['index']:row['us'] for row in base.rows(data['case']/'api_samples.jsonl') if row['label']==LABEL}
            if rank==1:
                waits.extend(gate_rows(data['case'], samples, name, delay))
            elif (data['case']/'chunk_gate.jsonl').exists():
                raise ValueError('unexpected intervention on 202')
            durations.append([samples[i] for i in range(1,10)])
        slowest = list(map(max,zip(*durations)))
        for i,(a,b) in enumerate(zip(*durations),1):
            api.append(dict(case=name,index=i,node202_us=a,node204_us=b,paired_max_us=max(a,b)))
        effects = [w['delay_us'] for w in waits if w['case']==name]
        rounds[name] = dict(case=name,requested_us=delay,observed_min_us=min(effects),observed_median_us=median(effects),
            observed_max_us=max(effects),node202_us=mean(durations[0]),node204_us=mean(durations[1]),paired_max_us=mean(slowest),
            max_cv_pct=max(100*pstdev(a)/mean(a) for a in (*durations,slowest)))
    metrics = ('node202_us','node204_us','paired_max_us')
    pairs = [dict(pair=i,**{m:rounds[b][m]-rounds[a][m] for m in metrics})
             for i,(a,b) in enumerate((('a1','b1'),('a2','b2')),1)]
    drift = max(drift_pct(rounds[a][m],rounds[b][m]) for (a,b) in (('a1','a2'),('b1','b2')) for m in metrics)
    cv = max(row['max_cv_pct'] for row in rounds.values())
    decision = dict(stable=drift<=3 and cv<=5,max_cv_pct=cv,max_repeat_drift_pct=drift,pairs=pairs,
        mean_delta_us={m:mean(p[m] for p in pairs) for m in metrics},
        note='B adds a non-consuming wait before collecting the sixth payload on each 204 data FIFO. '
             'Deltas are observed sensitivity to the intervention under URMA tail-off, not time recovered or a backend root cause.')
    output.mkdir(parents=True,exist_ok=False)
    for name, rows in [('rounds',list(rounds.values())),('gate_waits',waits),('paired_calls',api)]:
        base.write(output/f'{name}.csv',rows)
    (output/'decision.json').write_text(json.dumps(decision,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(decision,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    summarize(args.root,args.output)
