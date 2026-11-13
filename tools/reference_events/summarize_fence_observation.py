"""Compare sparse fence-on/off observations, retaining controls and failed gates."""
import argparse
import csv
import json
from pathlib import Path
import statistics
import audit_boundary_fifo as fifo
import audit_notify_submission as submission

base=fifo.boundaries.matched.base
LABEL='dispatch_bf16_chunk32'


def evidence_gate(control_changes,trace_change,representative):
    differential=abs(trace_change-statistics.mean(control_changes))
    return dict(control_gain_reproduced=all(x<=-2 for x in control_changes),
                differential_probe_effect_pp=differential,
                eligible_for_mechanism_review=representative and all(x<=-2 for x in control_changes) and differential<=1)


def summarize(root,job,output):
    cases={};identities={};api=[];metrics=[];quality={}
    for plan,option in (('all_on','all-on'),('peer_off','peer-off')):
        for step in ('paced','trace','post'):
            raw={}
            for rank in (0,1):
                case=base.case_data(root/(job+'_'+plan+'_'+step),rank,False,reference_events='on' if step=='trace' else 'paced')
                if case['c'].get('normal_fadd_fence_plan')!=option or case['identity']['backend']!='urma':raise ValueError('wrong fence experiment plan/backend')
                identity=dict(case['identity']);identity.pop('normal_fadd_fence_plan');identity.pop('normal_fadd_fence')
                signature=(base.sig(identity),case['runtime']['module_sha'])
                if identities.setdefault(rank,signature)!=signature:raise ValueError('A/B source/module/boot/runtime/workload changed')
                cases[plan,step,rank]=case
                raw[rank]={r['index']:r['us'] for r in base.rows(case['case']/'api_samples.jsonl') if r['label']==LABEL and r['used']}
            if raw[0].keys()!=raw[1].keys():raise ValueError('unpaired BF16 samples')
            values=[max(raw[0][i],raw[1][i]) for i in sorted(raw[0])]
            api.append(dict(plan=plan,step=step,paired_max_mean_us=statistics.mean(values)))
    output.mkdir(parents=True,exist_ok=False)
    for plan,option in (('all_on','all-on'),('peer_off','peer-off')):
        roots=[root/(job+'_'+plan+'_'+step) for step in ('paced','trace','post')]
        controls=fifo.boundaries.summarize(*roots,output/plan,fence_plan=option)
        capture=list(csv.DictReader((output/plan/'capture_quality.csv').open(newline='')))
        quality[plan]=all(r['representative'] for r in controls if r['label']==LABEL) and all(int(r['gpu_before_origin'])==0 and int(r['scheduler_error'])==0 for r in capture if r['label']==LABEL)
        # Export raw request metrics even if perturbation invalidates inference.
        for rank in (0,1):
            values=[];pairs=[]
            for window in base.rows(cases[plan,'trace',rank]['case']/'event_trace.jsonl'):
                if window['label']!=LABEL or not window['index']:continue
                part=submission.split(window)
                if len(part)!=132:raise ValueError('expected 132 verified BF16 pairs/window')
                values.extend(part);pairs.extend(submission.notify.pairs(window))
            if len(values)!=1188:raise ValueError('expected nine complete BF16 windows')
            for metric in values[0]:
                metrics.append(dict(plan=plan,node=rank,metric=metric,statistic='mean',us=statistics.mean(r[metric] for r in values)))
            for metric in ('completion_gap_us','write_post_to_cqe_us','atomic_post_to_cqe_us'):
                metrics.append(dict(plan=plan,node=rank,metric=metric,statistic='median',us=statistics.median(r[metric] for r in pairs)))
        if quality[plan]:fifo.audit(*roots,output/(plan+'_fifo'),fence_plan=option)
    indexed={(r['plan'],r['step']):r['paired_max_mean_us'] for r in api}
    changes={step:(indexed['peer_off',step]/indexed['all_on',step]-1)*100 for step in ('paced','trace','post')}
    gate=evidence_gate([changes['paced'],changes['post']],changes['trace'],all(quality.values()))
    base.write(output/'bf16_api.csv',api);base.write(output/'request_intervals.csv',metrics)
    (output/'decision.json').write_text(json.dumps(dict(quality=quality,peer_off_change_pct=changes,**gate,
        note='Eligibility permits reviewing whether previously observed 3.43% benefit co-occurs with reduced notification submission/FIFO waits; it is not proof of a unique root or additive critical path. Per-plan BF16 controls must pass 5% drift/impact/CV and clock-offset checks. Both inactive comparisons must improve >=2%; active-vs-inactive A/B effect must differ <=1 percentage point. These are predeclared engineering gates, not significance tests. Invalid captures remain exported; no automatic rerun. FIFO export is per-plan gated, BF16 must be selected when interpreting it. Old ABBA is the primary net-performance evidence; this six-case sequence is a mechanism follow-up, not a new ABBA.'),indent=2))
    print('FENCE_OBSERVATION_SUMMARY_PASSED eligible='+str(gate['eligible_for_mechanism_review']))
    return gate


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('root','output'):p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--job',required=True);a=p.parse_args();summarize(a.root,a.job,a.output)
