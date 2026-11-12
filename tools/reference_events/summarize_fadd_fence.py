"""Validate a two-pair, node1-only Normal data FADD fence intervention."""
import argparse
import json
from pathlib import Path
import statistics
import summarize_matched as matched
from summarize_submit import decision


def summarize(root,job,output):
    identities={};nodes=[];paired=[];contexts=[]
    plans={'all_on':'all-on','peer_off':'peer-off'}
    for repeat in (1,2):
        for plan,option in plans.items():
            job_root=root/('%s_r%02d_%s'%(job,repeat,plan));raw={}
            for rank in (0,1):
                data=matched.base.case_data(job_root,rank,False,reference_events='paced')
                c,runtime=data['c'],data['runtime'];expected='off' if option=='peer-off' and rank==1 else 'on'
                if c.get('job')!=job_root.name or c.get('normal_fadd_fence_plan')!=option or runtime.get('normal_fadd_fence_version')!=1 or runtime.get('normal_fadd_fence')!=expected:
                    raise ValueError('missing/wrong FADD fence plan or runtime capability')
                identity=dict(data['identity'])
                if identity['backend']!='urma' or identity.get('normal_window_plan')!='default' or identity.get('normal_submit_schedule')!='lane-mixed' or identity.get('reference_profile')!='legacy':
                    raise ValueError('FADD fence experiment also changed backend/window/submission/profile')
                identity.pop('normal_fadd_fence_plan');identity.pop('normal_fadd_fence')
                signature=(matched.base.sig(identity),runtime['module_sha'])
                if identities.setdefault(rank,signature)!=signature:raise ValueError('FADD fence A/B source/module/boot/runtime/workload changed')
                if repeat==1 and plan=='all_on':contexts.append(dict(node=rank,**identity,module_sha=runtime['module_sha']))
                for label,stats in sorted(data['means'].items()):nodes.append(dict(repeat=repeat,plan=plan,node=rank,label=label,**stats))
                raw[rank]={(r['label'],r['index']):r['us'] for r in matched.base.rows(data['case']/'api_samples.jsonl') if r['used']}
            if raw[0].keys()!=raw[1].keys():raise ValueError('unpaired peer samples')
            for (label,index),a in sorted(raw[0].items()):
                b=raw[1][label,index];paired.append(dict(repeat=repeat,plan=plan,label=label,index=index,node0_us=a,node1_us=b,paired_max_us=max(a,b)))
    rounds=[];decisions=[]
    for label in sorted(matched.base.ALL_LABELS):
        selected=[]
        for repeat in (1,2):
            values={p:[r['paired_max_us'] for r in paired if r['repeat']==repeat and r['label']==label and r['plan']==p] for p in plans}
            a,b=[statistics.mean(values[p]) for p in plans]
            cvs=[r['cv_pct'] for r in nodes if r['repeat']==repeat and r['label']==label]
            cvs.extend(statistics.pstdev(v)/statistics.mean(v)*100 for v in values.values())
            row=dict(repeat=repeat,label=label,all_on_max_mean_us=a,peer_off_max_mean_us=b,peer_off_change_pct=(b/a-1)*100,max_cv_pct=max(cvs))
            rounds.append(row);selected.append(row)
        changes=[r['peer_off_change_pct'] for r in selected]
        decisions.append(dict(label=label,r01_change_pct=changes[0],r02_change_pct=changes[1],decision=decision(changes,all(r['max_cv_pct']<=5 for r in selected))))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('per_node',nodes),('paired_calls',paired),('rounds',rounds),('decision',decisions)]:matched.base.write(output/(name+'.csv'),rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,
        note='ABBA: all-on(202 on,204 on), peer-off(202 on,204 off), peer-off, all-on. Only Normal data-kind ATOMIC fence changes, covering tail notifications and data feedback; FADD stays STRONG, barrier/recovery fence and LL unchanged, WRITE RELAX, window8, completion all, lane-mixed. No active tracing. Primary BF16 metric is mean per-sample max of two local durations, not a global makespan. Gain does not alone isolate tail notification, identify all backend regression, establish universal correctness or justify a production default change.'),indent=2))
    print('FADD_FENCE_SUMMARY_PASSED '+str(output));return decisions


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--job',required=True);a=p.parse_args();summarize(a.root,a.job,a.output)
