"""Locate the current RDMA/URMA difference across Normal call boundaries."""

import argparse
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median

from summarize_receive_gap import BACKENDS, PHASES, control_row
from summarize_same_kernel_gap import KERNEL, LABEL, base, shared_signature

STREAM_FIELDS = ('cached_notify_us', 'notify_to_dispatch_us', 'dispatch_comm_us')
FIELDS = (*STREAM_FIELDS, 'outside_marked_signed_us', 'outside_dispatch_us', 'api_us')


def windows(case, raw):
    marks = base.rows(case/'stream_timing.jsonl')
    api = {r['index']: r['us'] for r in raw if r['label']==LABEL}
    if [row['index'] for row in marks] != sorted(api):
        raise ValueError('missing stream timing windows')
    for row in marks:
        index, duration = row['index'], row['us']
        if row['label']!=LABEL or duration!=api[index]:
            raise ValueError('stream timing and API samples disagree')
        intervals = {name: row[name] for name in STREAM_FIELDS}
        if not all(isfinite(v) and v>=0 for v in intervals.values()) or intervals['dispatch_comm_us']<=0:
            raise ValueError('invalid communication-stream marker durations')
        if index:
            yield dict(index=index, **intervals, outside_marked_signed_us=duration-sum(intervals.values()),
                outside_dispatch_us=duration-intervals['dispatch_comm_us'], api_us=duration)


def summarize(root, output):
    overlay = base.read(root/'overlay_node0.json')
    if overlay!=base.read(root/'overlay_node1.json'):
        raise ValueError('different stream marker source trees')
    expected_sources = {key:value for key,value in overlay['sources'].items() if key.startswith('ep/')}
    context, samples, controls = [], [], []
    shared = {}
    rank0_last = True
    for prefix,backend in BACKENDS.items():
        raw_us = {}
        for rank in (0,1):
            previous = None
            for phase in PHASES:
                name = f'{prefix}_{phase}'
                probe = rank==0 and phase=='probe'
                data = base.case_data(root/name,rank,False,reference_events='paced',stream_probe=probe)
                identity = data['identity']
                if any(identity['sources'].get(key)!=value for key,value in expected_sources.items()):
                    raise ValueError('wrong stream marker module source')
                if (data['c']['job']!=name or identity['kernel']!=KERNEL or identity['backend']!=backend
                        or identity['reference_profile']!='legacy' or identity['normal_window_plan']!='default'
                        or identity['normal_retire_plan']!='ordered'):
                    raise ValueError(f'wrong runtime for {name}, rank {rank}')
                if backend=='urma' and (identity['normal_fadd_fence_plan']!='peer-tail-off'
                        or identity['normal_submit_schedule']!='lane-mixed'):
                    raise ValueError('expected URMA with only 204 tail-off')
                if backend=='verbs' and not base.read(data['case']/'status.json')['checks'].get('dmabuf'):
                    raise ValueError('GPU DMA-BUF not verified')
                local = (identity,data['runtime']['module_sha'])
                if previous is not None and local!=previous:
                    raise ValueError('module, boot or runtime changed across controls')
                previous = local
                common = shared_signature(identity)
                if shared.setdefault(rank,common)!=common:
                    raise ValueError(f'host, boot, runtime or workload differs: {name}, rank {rank}')
                raw = base.rows(data['case']/'api_samples.jsonl')
                raw_us[rank,phase] = {r['index']:r['us'] for r in raw if r['used'] and r['label']==LABEL}
                if probe:
                    samples.extend(dict(backend=backend,**row) for row in windows(data['case'],raw))
                context.append(dict(case=name,rank=rank,module_sha=local[1],**identity))
            controls.append(control_row(backend,rank,{p:list(raw_us[rank,p].values()) for p in PHASES}))
        calls = {p: [(raw_us[0,p][i],raw_us[1,p][i]) for i in range(1,10)] for p in PHASES}
        rank0_last = rank0_last and all(a>=b for pairs in calls.values() for a,b in pairs)
        controls.append(control_row(backend,'paired_max',{p:[max(pair) for pair in calls[p]] for p in PHASES}))

    v,u = (next(r for r in controls if r['backend']==backend and r['node']=='paired_max')
           for backend in ('verbs','urma'))
    old_gap = ((u['pre_us']+u['post_us'])-(v['pre_us']+v['post_us']))/2
    probe_gap = u['probe_us']-v['probe_us']
    controls_ok = all(row['representative'] for row in controls)
    gap_shift = probe_gap-old_gap
    # 100 us bounds differential observer bias to roughly one sixth of the
    # 620–660 us gap. Both gates remain separate in the output.
    decision = dict(controls_passed=controls_ok, baseline_gap_us=old_gap, probe_gap_us=probe_gap,
        gap_change_us=gap_shift, gap_change_limit_us=100, rank0_last=rank0_last,
        usable=controls_ok and rank0_last and old_gap>0 and probe_gap>0 and abs(gap_shift)<=100,
        note='Events bracket the actual cached_notify and main Dispatch launch calls on one communication stream. '
             'Outer API timing uses the caller stream and residuals remain signed.')
    comparisons = []
    for field in FIELDS:
        r,u = ([x[field] for x in samples if x['backend']==backend] for backend in ('verbs','urma'))
        comparisons.append(dict(boundary=field,verbs_mean_us=mean(r),urma_mean_us=mean(u),delta_us=mean(u)-mean(r),
            verbs_median_us=median(r),urma_median_us=median(u)))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('controls',controls),('stream_windows',samples),('comparison',comparisons)]:
        base.write(output/f'{name}.csv',rows)
    for name, data in [('decision',decision),('context',context)]:
        (output/f'{name}.json').write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(decision,indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    summarize(args.root,args.output)
