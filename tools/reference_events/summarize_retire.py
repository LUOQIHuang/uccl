"""One inactive ABBA: node1 ready-credit retirement, fences/window unchanged."""
import argparse,json,statistics
from pathlib import Path
import summarize_matched as matched
from summarize_submit import decision


def summarize(root,job,output):
    identities={};nodes=[];paired=[];context=[]
    for repeat in (1,2):
        for plan,option in [('ordered','ordered'),('ready','peer-ready')]:
            location=root/('%s_r%02d_%s'%(job,repeat,plan));raw={}
            for rank in (0,1):
                data=matched.base.case_data(location,rank,False,reference_events='paced');runtime=data['runtime'];identity=dict(data['identity'])
                status=matched.base.read(data['case']/'status.json')
                if data['c'].get('job')!=location.name or data['c'].get('normal_retire_plan')!=option or runtime.get('normal_fifo_retire_version')!=1 or not status['checks'].get('normal_fifo_retire'):raise ValueError('missing/wrong retirement capability/plan')
                if identity['backend']!='urma' or identity['normal_window_plan']!='default' or identity['normal_submit_schedule']!='lane-mixed' or identity['reference_profile']!='legacy' or identity['normal_fadd_fence_plan']!='all-on' or identity['normal_fadd_fence']!='on':raise ValueError('changed fence/window/schedule/profile')
                identity.pop('normal_retire_plan');identity.pop('normal_fifo_retire')
                signature=(matched.base.sig(identity),runtime['module_sha'])
                if identities.setdefault(rank,signature)!=signature:raise ValueError('retirement A/B module/source/boot/runtime changed')
                if repeat==1 and plan=='ordered':context.append(dict(node=rank,**identity,module_sha=runtime['module_sha']))
                nodes.extend(dict(repeat=repeat,plan=plan,node=rank,label=label,**stats) for label,stats in sorted(data['means'].items()))
                raw[rank]={(r['label'],r['index']):r['us'] for r in matched.base.rows(data['case']/'api_samples.jsonl') if r['used']}
            if raw[0].keys()!=raw[1].keys():raise ValueError('unpaired samples')
            paired.extend(dict(repeat=repeat,plan=plan,label=label,index=i,paired_max_us=max(v,raw[1][label,i])) for (label,i),v in sorted(raw[0].items()))
    rounds=[];decisions=[]
    for label in sorted(matched.base.ALL_LABELS):
        selected=[]
        for repeat in (1,2):
            values={p:[r['paired_max_us'] for r in paired if r['repeat']==repeat and r['plan']==p and r['label']==label] for p in ('ordered','ready')}
            a,b=[statistics.mean(values[p]) for p in ('ordered','ready')]
            cvs=[r['cv_pct'] for r in nodes if r['repeat']==repeat and r['label']==label]+[statistics.pstdev(v)/statistics.mean(v)*100 for v in values.values()]
            row=dict(repeat=repeat,label=label,A_us=a,B_us=b,B_change_pct=(b/a-1)*100,max_cv_pct=max(cvs));rounds.append(row);selected.append(row)
        decisions.append(dict(label=label,decision=decision([r['B_change_pct'] for r in selected],all(r['max_cv_pct']<=5 for r in selected))))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('per_node',nodes),('paired_calls',paired),('rounds',rounds),('decision',decisions)]:matched.base.write(output/(name+'.csv'),rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,note='ABBA, only node1 software retirement policy changes. Hardware completion is still required, ordinary completed credits can retire past an unfinished ordinary command, no crossing quiet/barrier, outstanding budget remains8, all fences retained. No active tracing, no cross-host clock subtraction, no automatic default change. Negative excludes useful net effect for this node/workload, not every queue policy.'),indent=2))
    print('RETIRE_SUMMARY_PASSED '+str(output));return decisions


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--job',required=True);a=p.parse_args();summarize(a.root,a.job,a.output)
