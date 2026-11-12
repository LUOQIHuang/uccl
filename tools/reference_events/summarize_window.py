"""Validate two reversed pairs changing only node1's Normal credit limit 8->4."""
import argparse
import json
from pathlib import Path
import statistics
import summarize_matched as matched
from summarize_submit import decision


def summarize(root,job,output):
    identities={};nodes=[];paired=[];context=[];backend=None
    for repeat in (1,2):
        for plan in ('default','peer4'):
            case_root=root/('%s_r%02d_%s'%(job,repeat,plan));raw={}
            for rank in (0,1):
                data=matched.base.case_data(case_root,rank,False,reference_events='paced')
                c,rt=data['c'],data['runtime']
                expected=4 if plan=='peer4' and rank==1 else 8
                if c.get('job')!=case_root.name or c.get('normal_window_plan')!=plan or rt.get('normal_max_inflight')!=expected:
                    raise ValueError('missing/wrong window runtime capability or plan')
                identity=dict(data['identity'])
                if identity.get('normal_submit_schedule','lane-mixed')!='lane-mixed':raise ValueError('submission schedule also changed')
                identity.pop('normal_window_plan');identity.pop('normal_max_inflight')
                signature=(matched.base.sig(identity),rt['module_sha'])
                if identities.setdefault(rank,signature)!=signature:raise ValueError('window A/B source/module/boot/library/workload changed')
                current=identity['backend']
                if backend is not None and backend!=current:raise ValueError('mixed backends in one A/B')
                backend=current
                if repeat==1 and plan=='default':context.append(dict(node=rank,**identity,module_sha=rt['module_sha']))
                for label,stats in sorted(data['means'].items()):nodes.append(dict(repeat=repeat,plan=plan,node=rank,label=label,**stats))
                raw[rank]={(r['label'],r['index']):r['us'] for r in matched.base.rows(data['case']/'api_samples.jsonl') if r['used']}
            if raw[0].keys()!=raw[1].keys():raise ValueError('unpaired peer samples')
            for (label,index),a in sorted(raw[0].items()):
                b=raw[1][label,index]
                paired.append(dict(repeat=repeat,plan=plan,label=label,index=index,node0_us=a,node1_us=b,paired_max_us=max(a,b)))
    rounds=[];decisions=[]
    for label in sorted(matched.base.ALL_LABELS):
        rr=[]
        for repeat in (1,2):
            samples={p:[r['paired_max_us'] for r in paired if r['repeat']==repeat and r['label']==label and r['plan']==p] for p in ('default','peer4')}
            a,b=[statistics.mean(samples[p]) for p in ('default','peer4')]
            cvs=[r['cv_pct'] for r in nodes if r['repeat']==repeat and r['label']==label]
            cvs.extend(statistics.pstdev(v)/statistics.mean(v)*100 for v in samples.values())
            row=dict(repeat=repeat,label=label,default_max_mean_us=a,peer4_max_mean_us=b,peer4_change_pct=(b/a-1)*100,max_cv_pct=max(cvs))
            rounds.append(row);rr.append(row)
        changes=[r['peer4_change_pct'] for r in rr]
        decisions.append(dict(label=label,r01_change_pct=changes[0],r02_change_pct=changes[1],decision=decision(changes,all(r['max_cv_pct']<=5 for r in rr))))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('per_node',nodes),('paired_calls',paired),('rounds',rounds),('decision',decisions)]:matched.base.write(output/(name+'.csv'),rows)
    (output/'context.json').write_text(json.dumps(dict(backend=backend,nodes=context,
        note='Fixed ABBA: default(8,8), peer4(8,4), peer4, default. Only node1 pending GPU-command limit changes, not device SQ depth. Two reversed pairs, five warmups/ten samples/drop first. Primary BF16 metric is mean of per-sample max of two local CUDA durations, not a globally timed makespan. >=5% improvement in both pairs with CV<=5% supports a useful credit-limit effect; no automatic production change. Reduced per-request CQE latency alone is not a net improvement and may only move waiting upstream.'),indent=2))
    print('WINDOW_PERFORMANCE_SUMMARY_PASSED '+str(output))
    return decisions


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--job',required=True);a=p.parse_args();summarize(a.root,a.job,a.output)
