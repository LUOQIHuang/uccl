"""Inactive A/B/C-C/B/A comparison: all fence, node1 data-off, node1 tail-off."""
import argparse
import json
from pathlib import Path
import statistics
import summarize_matched as matched


def summarize(root,job,output):
    plans={'all_on':'all-on','peer_off':'peer-off','peer_tail_off':'peer-tail-off'}
    identities={};nodes=[];paired=[];contexts=[]
    for repeat in (1,2):
        for plan,option in plans.items():
            location=root/('%s_r%02d_%s'%(job,repeat,plan));raw={}
            for rank in (0,1):
                data=matched.base.case_data(location,rank,False,reference_events='paced');runtime=data['runtime']
                status=matched.base.read(data['case']/'status.json')
                if data['c'].get('job')!=location.name or data['c'].get('normal_fadd_fence_plan')!=option or runtime.get('normal_atomic_role_version')!=1 or not status['checks'].get('normal_atomic_roles'):raise ValueError('wrong job/plan or unverified role capability')
                identity=dict(data['identity'])
                if identity['backend']!='urma' or identity.get('reference_profile')!='legacy' or identity.get('normal_window_plan')!='default' or identity.get('normal_submit_schedule')!='lane-mixed':raise ValueError('changed transport/profile/window/schedule')
                identity.pop('normal_fadd_fence_plan');identity.pop('normal_fadd_fence')
                signature=(matched.base.sig(identity),runtime['module_sha'])
                if identities.setdefault(rank,signature)!=signature:raise ValueError('A/B/C source/module/boot/runtime changed')
                if repeat==1 and plan=='all_on':contexts.append(dict(node=rank,**identity,module_sha=runtime['module_sha']))
                nodes.extend(dict(repeat=repeat,plan=plan,node=rank,label=label,**stats) for label,stats in sorted(data['means'].items()))
                raw[rank]={(r['label'],r['index']):r['us'] for r in matched.base.rows(data['case']/'api_samples.jsonl') if r['used']}
            if raw[0].keys()!=raw[1].keys():raise ValueError('unpaired samples')
            paired.extend(dict(repeat=repeat,plan=plan,label=label,index=i,paired_max_us=max(value,raw[1][label,i])) for (label,i),value in sorted(raw[0].items()))
    rounds=[]
    for repeat in (1,2):
        for label in sorted(matched.base.ALL_LABELS):
            values={p:[r['paired_max_us'] for r in paired if r['repeat']==repeat and r['label']==label and r['plan']==p] for p in plans}
            means={p:statistics.mean(v) for p,v in values.items()}
            cvs=[r['cv_pct'] for r in nodes if r['repeat']==repeat and r['label']==label]+[statistics.pstdev(v)/statistics.mean(v)*100 for v in values.values()]
            rounds.append(dict(repeat=repeat,label=label,A_us=means['all_on'],B_us=means['peer_off'],C_us=means['peer_tail_off'],B_vs_A_pct=(means['peer_off']/means['all_on']-1)*100,C_vs_A_pct=(means['peer_tail_off']/means['all_on']-1)*100,C_vs_B_pct=(means['peer_tail_off']/means['peer_off']-1)*100,max_cv_pct=max(cvs)))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('per_node',nodes),('paired_calls',paired),('rounds',rounds)]:matched.base.write(output/(name+'.csv'),rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,note='Fixed ABC-CBA, all stages inactive paced, two order-reversed observations per configuration, not six independent repeats. Primary BF16 mean per-sample maximum local duration is not a global makespan. Tail labels originate in GPU call sites; feedback stays fenced in C. Scope includes Dispatch and Combine. Effect/variability and correctness must be reviewed together; no automatic default change or stage attribution.'),indent=2))
    print('FENCE_SCOPE_SUMMARY_PASSED '+str(output));return rounds


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--job',required=True);a=p.parse_args();summarize(a.root,a.job,a.output)
