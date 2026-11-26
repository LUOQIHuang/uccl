"""GPU-only boundaries with controls; keep observations separate from attribution."""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
import summarize_matched as matched
from summarize_boundaries import spans

LABEL='dispatch_bf16_chunk32'
TAGS=('batch_pre','paced','trace','post','batch_post')


def endpoints(window):
    records=window['gpu']['records']
    by=collections.defaultdict(list)
    for r in records:by[r['event']].append(r['host_ns'])
    first=min(by[0]);write=min(by[1]);visible=max(by[2]);done=max(by[9])
    ordered=first<=write<=visible<=done
    # Partition one LOCAL timeline envelope. These are not additive operation
    # costs and never match this node's outgoing WRITE to a peer's receiver.
    result=dict(first_token_to_first_write_us=(write-first)/1000,
        first_write_to_last_inbound_visible_us=(visible-write)/1000,
        last_inbound_visible_to_receiver_done_us=(done-visible)/1000,
        envelope_us=(done-first)/1000,api_uncovered_signed_us=window['us']-(done-first)/1000,
        sender_first_write_to_last_ready_us=(max(by[3])-write)/1000,
        sender_last_ready_to_last_publish_us=(max(by[4])-max(by[3]))/1000,
        ordered=ordered,max_clock_gap_us=window['gpu']['clock_stats']['max_gap_ns']/1000)
    result['clock_ok']=window['gpu']['clock_stats'].get('scheduler_error',0)==0
    return result


def summarize(root,job,backend,output):
    cases={};controls=[];paired=[];windows=[];channels=[];context=[];api=[]
    for rank in (0,1):
        identity=None;module=None
        for tag in TAGS:
            cadence='on' if tag=='trace' else ('batch' if tag.startswith('batch') else 'paced')
            path=root/backend/('%s_%s_%s'%(job,backend,tag))
            d=matched.base.case_data(path,rank,False,reference_events=cadence,reference_gpu_only=True)
            if d['identity']['backend']!=backend or d['identity']['reference_profile']!='boundaries':raise ValueError('wrong backend/profile')
            if backend=='urma' and d['identity'].get('normal_fadd_fence_plan')!='peer-tail-off':raise ValueError('URMA must use current tail-off baseline')
            signature=matched.base.sig(d['identity'])
            if identity is not None and (identity!=signature or module!=d['runtime']['module_sha']):raise ValueError('controls changed source/module/boot/runtime')
            identity=signature;module=d['runtime']['module_sha'];cases[rank,tag]=d
            for row in matched.base.rows(d['case']/'api_samples.jsonl'):
                if row['used'] and row['label']==LABEL:api.append(dict(node=rank,tag=tag,index=row['index'],us=row['us']))
        context.append(dict(node=rank,**cases[rank,'trace']['identity'],module_sha=module))
        means={tag:cases[rank,tag]['means'][LABEL] for tag in TAGS}
        a,t,b=[means[k]['mean_us'] for k in ('paced','trace','post')]
        controls.append(dict(node=rank,pre_us=a,trace_us=t,post_us=b,
            trace_change_pct=100*(t/((a+b)/2)-1),paced_drift_pct=100*(b/a-1),
            paced_max_cv_pct=max(means[k]['cv_pct'] for k in ('paced','trace','post')),
            batch_pre_us=means['batch_pre']['mean_us'],batch_post_us=means['batch_post']['mean_us'],
            batch_drift_pct=100*(means['batch_post']['mean_us']/means['batch_pre']['mean_us']-1),
            batch_max_cv_pct=max(means[k]['cv_pct'] for k in ('batch_pre','batch_post'))))
        for w in matched.base.rows(cases[rank,'trace']['case']/'event_trace.jsonl'):
            if w['label']!=LABEL or not w['index']:continue
            if w.get('cpu_events') is not False or w['cpu'] is not None:raise ValueError('CPU tracing was active')
            windows.append(dict(node=rank,index=w['index'],**endpoints(w)))
            channels.extend(dict(node=rank,index=w['index'],**s) for s in spans(w))
    for tag in TAGS:
        raw={rank:{r['index']:r['us'] for r in api if r['node']==rank and r['tag']==tag} for rank in (0,1)}
        if set(raw[0])!=set(range(1,10)) or raw[0].keys()!=raw[1].keys():raise ValueError('unpaired samples')
        for i in sorted(raw[0]):paired.append(dict(tag=tag,index=i,us=max(raw[0][i],raw[1][i])))
    paired_means={tag:statistics.mean(r['us'] for r in paired if r['tag']==tag) for tag in TAGS}
    paired_cv={tag:100*statistics.pstdev(r['us'] for r in paired if r['tag']==tag)/paired_means[tag] for tag in TAGS}
    a,t,b=[paired_means[k] for k in ('paced','trace','post')]
    paired_gate=abs(100*(t/((a+b)/2)-1))<=5 and abs(100*(b/a-1))<=5 and max(paired_cv.values())<=5 and abs(100*(paired_means['batch_post']/paired_means['batch_pre']-1))<=3
    gate=paired_gate and all(abs(r['trace_change_pct'])<=5 and abs(r['paced_drift_pct'])<=5 and r['paced_max_cv_pct']<=5 and abs(r['batch_drift_pct'])<=3 and r['batch_max_cv_pct']<=5 for r in controls)
    clock_ok=all(w['clock_ok'] and w['ordered'] for w in windows)
    grouped=collections.defaultdict(list)
    for row in channels:grouped[row['node'],row['span']].append(row['us'])
    channel_summary=[dict(node=n,span=s,observations=len(v),median_us=statistics.median(v),min_us=min(v),max_us=max(v)) for (n,s),v in sorted(grouped.items())]
    envelope_summary=[]
    fields=[k for k in windows[0] if k.endswith('_us')]
    for rank in (0,1):
        selected=[w for w in windows if w['node']==rank]
        for field in fields:
            values=[w[field] for w in selected]
            envelope_summary.append(dict(node=rank,span=field,n=len(values),mean_us=statistics.mean(values),median_us=statistics.median(values)))
    output.mkdir(parents=True,exist_ok=False)
    for name,data in [('api_controls',controls),('paired_api',paired),('envelopes',windows),('envelope_summary',envelope_summary),('channel_spans',channels),('channel_summary',channel_summary)]:matched.base.write(output/(name+'.csv'),data)
    result=dict(backend=backend,nodes=context,usable=gate and clock_ok,performance_gate=gate,clock_and_order_gate=clock_ok,
        paired_means_us=paired_means,paired_cv_pct=paired_cv,batch_reference_us=(paired_means['batch_pre']+paired_means['batch_post'])/2,
        paced_reference_us=(a+b)/2,production_gap_attribution=False,
        note='GPU-only 12-point observations; no CPU request trace. Coarse spans partition a local timeline envelope, not independent operation costs. Cross-host timestamps are never subtracted. Sender spans overlap receiver work. Last-tail successful check is not the full waiting time. Sampled-clock gaps are observed precision indicators, not a calibrated hard error bound. One backend cannot establish the URMA/verbs excess. Compare two accepted campaigns and batch-vs-paced gaps before attributing production behavior. Failed gates: keep observations, no automatic retry or optimization conclusion.')
    (output/'context.json').write_text(json.dumps(result,indent=2))
    print('GPU_GAP_SUMMARY_PASSED usable='+str(result['usable'])+' '+str(output))
    return result


def compare(urma,verbs,output):
    u=matched.base.read(urma/'context.json');v=matched.base.read(verbs/'context.json')
    if u['backend']!='urma' or v['backend']!='verbs' or not u['usable'] or not v['usable']:raise ValueError('both backend campaigns must pass')
    for rank in (0,1):
        a=u['nodes'][rank];b=v['nodes'][rank]
        for key in ('torch','maca','gpu_versions'):
            if a[key]!=b[key]:raise ValueError('GPU/runtime changed across backend '+key)
        common=set(a['sources'])&set(b['sources'])
        if not {'ep/src/internode.cu','ep/include/uccl_ibgda.cuh'}<=common or any(a['sources'][k]!=b['sources'][k] for k in common):raise ValueError('compiled source changed across backend')
    gap={key:100*(u[key]/v[key]-1) for key in ('batch_reference_us','paced_reference_us')}
    cadence_ok=abs(gap['batch_reference_us']-gap['paced_reference_us'])<=3
    def rows(p):return list(csv.DictReader(p.open()))
    ur={(r['node'],r['span']):r for r in rows(urma/'envelope_summary.csv')}
    vr={(r['node'],r['span']):r for r in rows(verbs/'envelope_summary.csv')}
    if ur.keys()!=vr.keys():raise ValueError('different GPU spans')
    result=[dict(node=k[0],span=k[1],urma_mean_us=float(ur[k]['mean_us']),verbs_mean_us=float(vr[k]['mean_us']),difference_us=float(ur[k]['mean_us'])-float(vr[k]['mean_us'])) for k in sorted(ur)]
    output.mkdir(parents=True,exist_ok=False);matched.base.write(output/'envelope_comparison.csv',result)
    (output/'context.json').write_text(json.dumps(dict(batch_gap_pct=gap['batch_reference_us'],paced_gap_pct=gap['paced_reference_us'],cadence_gap_consistent=cadence_ok,
        note='Kernel/transport/device stack differs by design; this is a backend-stack comparison, not isolated API causality. Difference in local envelope partitions locates candidate time regions, not exclusive hardware costs. Even with cadence agreement, this does not prove every observer span transfers to continuous execution or that the largest region is wholly recoverable. Review signed residuals, clock gaps and per-call tails before accepting attribution.'),indent=2))
    print('GPU_GAP_COMPARISON_WRITTEN cadence_gap_consistent='+str(cadence_ok))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    s=sub.add_parser('summarize');s.add_argument('--root',type=Path,required=True);s.add_argument('--job',default='normal_gap_gpu_01');s.add_argument('--backend',choices=('urma','verbs'),required=True);s.add_argument('--output',type=Path,required=True)
    c=sub.add_parser('compare');c.add_argument('--urma',type=Path,required=True);c.add_argument('--verbs',type=Path,required=True);c.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.action=='summarize':summarize(a.root,a.job,a.backend,a.output)
    else:compare(a.urma,a.verbs,a.output)
