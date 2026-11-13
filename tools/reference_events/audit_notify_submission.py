"""Offline split of an already observed adjacent WRITE/FADD submission gap."""
import argparse
import collections
import json
from pathlib import Path
import statistics
import audit_notify_completion as notify


def split(window):
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    for event in window['cpu']['events']:
        groups[event['proxy'],event['wr_id']][event['stage']].append(event['ns'])
    result=[]
    for pair in notify.pairs(window):
        atomic=groups[pair['proxy'],pair['atomic_wr_id']]
        stages=('poll','decoded','prepare','post_begin','post_end')
        if any(len(atomic[s])!=1 for s in stages):
            raise ValueError('submission split requires one observation per FADD stage')
        times=[pair['write_post_end_ns']]+[atomic[s][0] for s in stages]
        names=('write_post_end_to_atomic_poll','atomic_poll_to_decoded',
               'atomic_decoded_to_prepare','atomic_prepare_to_post_begin','atomic_post_begin_to_post_end')
        values={name+'_us':(b-a)/1000 for name,a,b in zip(names,times,times[1:])}
        if any(times[i+1]<times[i] for i in range(1,len(times)-1)):
            raise ValueError('invalid FADD observation order')
        # The first signed gap can be negative when both commands were read
        # before the WRITE post completed. Preserve it instead of clipping.
        values['post_end_gap_us']=pair['post_end_gap_us']
        assert abs(sum(values[n+'_us'] for n in names)-pair['post_end_gap_us'])<1e-6
        result.append(values)
    return result


def audit(roots,output):
    rows=[];context=[]
    for campaign,root in roots:
        for rank in (0,1):
            data=notify.matched.base.case_data(root,rank,False,reference_events='on')
            selected=[]
            for window in notify.matched.base.rows(data['case']/'event_trace.jsonl'):
                if not window['index'] or window['label']!=notify.LABEL:continue
                values=split(window)
                if len(values)!=132:raise ValueError('expected 132 verified pairs/window')
                selected.extend(values)
            if len(selected)!=1188:raise ValueError('expected nine complete BF16 windows')
            for metric in selected[0]:
                values=[r[metric] for r in selected]
                rows.append(dict(campaign=campaign,node=rank,metric=metric,observations=len(values),
                                 mean_us=statistics.mean(values),median_us=statistics.median(values)))
            context.append(dict(campaign=campaign,node=rank,identity=data['identity'],
                                event_trace_sha=notify.matched.base.sha(data['case']/'event_trace.jsonl')))
    output.mkdir(parents=True,exist_ok=False)
    notify.matched.base.write(output/'submission_summary.csv',rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,note=
        'Existing fence-on BF16 traces only. Same proxy/ring, consecutive WR, same endpoint and payload/token checks are reused. poll is CPU command-read observation, not a hardware poll or GPU publication time. The signed first interval includes unknown upstream publication/queue/service waits; it does not isolate CPU computation. Per-pair intervals and their means add; separate medians do not. No cross-host subtraction; new fence-off experiment has no active tracing, so this audit cannot identify which interval changed when fence was disabled.'),indent=2))
    print('NOTIFY_SUBMISSION_AUDIT_PASSED '+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('verbs-trace','urma-trace','current-trace','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();audit([('verbs_historical',a.verbs_trace),('urma_historical',a.urma_trace),('urma_boundaries',a.current_trace)],a.output)
