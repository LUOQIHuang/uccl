"""Audit adjacent Normal BF16 payload/notify completion observations, offline.

Uses validated trace files and strict same-ring, consecutive-command,
endpoint and payload/token-count checks. Completion gaps are observations,
not a hardware atomic latency measurement or an additive API decomposition.
"""
import argparse
import collections
import json
from pathlib import Path
import statistics
import summarize_matched as matched

LABEL='dispatch_bf16_chunk32'
TOKEN_BYTES=7168*2+16


def pairs(window):
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    heartbeat=collections.defaultdict(list)
    for e in window['cpu']['events']:
        groups[e['proxy'],e['wr_id']][e['stage']].append(e)
        if e['stage']=='poll_heartbeat':heartbeat[e['proxy']].append(e['ns'])
    rows=[]
    for (proxy,wid),write in list(groups.items()):
        size=max((e['value'] for e in write.get('payload_bytes',[])),default=0)
        if size<4096:continue
        atomic=groups.get((proxy,wid+1),{})
        if wid>>32!=(wid+1)>>32 or 'atomic_value' not in atomic:
            raise ValueError('payload lacks adjacent same-ring FADD')
        if len(atomic['atomic_value'])!=1 or size!=atomic['atomic_value'][0]['value']*TOKEN_BYTES:
            raise ValueError('notify token count does not match BF16 payload')
        for stage in ('post_begin','post_end','cqe'):
            if len(write.get(stage,[]))!=1 or len(atomic.get(stage,[]))!=1:
                raise ValueError('pair requires one physical post/completion per operation')
        if write['post_end'][0]['qp']!=atomic['post_end'][0]['qp']:
            raise ValueError('notify pair endpoint mismatch')
        wp,ap=write['post_end'][0]['ns'],atomic['post_end'][0]['ns']
        wc,ac=write['cqe'][0]['ns'],atomic['cqe'][0]['ns']
        if wp<windows_origin(window) or ap<wp or wc<wp or ac<ap:
            raise ValueError('invalid pair clock ordering')
        rows.append(dict(index=window['index'],proxy=proxy,ring=wid>>32,
            write_wr_id=wid,atomic_wr_id=wid+1,endpoint=write['post_end'][0]['qp'],
            payload_bytes=size,notify_tokens=atomic['atomic_value'][0]['value'],
            write_post_end_ns=wp,atomic_post_end_ns=ap,write_cqe_ns=wc,atomic_cqe_ns=ac,
            post_end_gap_us=(ap-wp)/1000,completion_gap_us=(ac-wc)/1000,
            atomic_posted_before_write_cqe=ap<wc,atomic_post_lead_us=(wc-ap)/1000,
            write_post_to_cqe_us=(wc-wp)/1000,atomic_post_to_cqe_us=(ac-ap)/1000,
            heartbeats_between_cqes=sum(wc<t<ac for t in heartbeat[proxy])))
    return rows


def windows_origin(window):return window['cpu']['origin_ns']


def quantile(values,p):
    v=sorted(values);pos=(len(v)-1)*p;lo=int(pos)
    return v[lo]+(v[min(lo+1,len(v)-1)]-v[lo])*(pos-lo)


def audit(verbs_trace,urma_trace,current_trace,output):
    rows=[];contexts=[]
    for campaign,trace in [('verbs_historical',verbs_trace),('urma_historical',urma_trace),('urma_boundaries',current_trace)]:
        for rank in (0,1):
            data=matched.base.case_data(trace,rank,False,reference_events='on')
            contexts.append(dict(campaign=campaign,node=rank,module_sha=data['runtime']['module_sha'],identity=data['identity']))
            selected=[]
            for w in matched.base.rows(data['case']/'event_trace.jsonl'):
                if not w['index'] or w['label']!=LABEL:continue
                rr=pairs(w)
                if len(rr)!=132:raise ValueError('expected 132 payload/notify pairs per window')
                selected.extend(dict(campaign=campaign,node=rank,**r) for r in rr)
            if len(selected)!=1188:raise ValueError('expected nine complete BF16 windows')
            rows.extend(selected)
    summary=[]
    for campaign in ('verbs_historical','urma_historical','urma_boundaries'):
        for rank in (0,1):
            rr=[r for r in rows if r['campaign']==campaign and r['node']==rank]
            gap=[r['completion_gap_us'] for r in rr]
            summary.append(dict(campaign=campaign,node=rank,pairs=len(rr),
                completion_gap_median_us=statistics.median(gap),completion_gap_p95_us=quantile(gap,.95),
                post_end_gap_median_us=statistics.median(r['post_end_gap_us'] for r in rr),
                atomic_preposted=sum(r['atomic_posted_before_write_cqe'] for r in rr),
                post_lead_median_us=statistics.median(r['atomic_post_lead_us'] for r in rr),
                with_heartbeat_between_cqes=sum(r['heartbeats_between_cqes']>0 for r in rr)))
    output.mkdir(parents=True,exist_ok=False)
    matched.base.write(output/'notify_pairs.csv',rows)
    matched.base.write(output/'notify_pair_summary.csv',summary)
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,
        notes='Normal BF16 4096 tokens, hidden7168+16-byte metadata. All 132 payloads/window require adjacent wr_id+1 FADD on same ring/endpoint and exact payload_bytes=notify_tokens*14352. Do not select pairs by completion gap sign or preposted status. Completion times are software observations; endpoint FIFO order is not hardware completion timing. Historical captures were previously accepted matched paced/on; current boundary BF16 separately passed paced/on/post, while FP8 did not. This audit validates raw trace provenance/coverage but does not rerun those performance controls. Kernel/device/SDK differ across backends. Adjacent CQE gap includes endpoint dependencies, queue/service, remote access and software polling, not pure hardware atomic latency or independent contribution to total API duration.'),indent=2))
    print('NOTIFY_COMPLETION_AUDIT_PASSED pairs='+str(len(rows))+' '+str(output))
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('verbs-trace','urma-trace','current-trace','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();audit(a.verbs_trace,a.urma_trace,a.current_trace,a.output)
