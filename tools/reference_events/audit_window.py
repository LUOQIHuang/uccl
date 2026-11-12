"""Locate long-tail payload requests by same-ring issue position, offline only."""
import argparse
import collections
import json
from pathlib import Path
import statistics
import tempfile
import summarize_matched as matched


def quantile(values,p):
    values=sorted(values);position=(len(values)-1)*p;low=int(position)
    return values[low]+(values[min(low+1,len(values)-1)]-values[low])*(position-low)


def requests(window):
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    for event in window['cpu']['events']:groups[event['proxy'],event['wr_id']][event['stage']].append(event)
    rows=[]
    for (proxy,wid),events in groups.items():
        if wid==2**64-1 or not all(s in events for s in ('poll','post_begin','post_end','cqe','ack_slot')):continue
        rows.append(dict(proxy=proxy,wr_id=wid,ring=wid>>32,
            polled=min(e['ns'] for e in events['poll']),post_begin=min(e['ns'] for e in events['post_begin']),
            post_end=max(e['ns'] for e in events['post_end']),cqe=max(e['ns'] for e in events['cqe']),
            ack=max(e['ns'] for e in events['ack_slot']),
            payload_bytes=max((e['value'] for e in events.get('payload_bytes',[])),default=0),
            atomic='atomic_value' in events))
    by_ring=collections.defaultdict(list)
    for row in rows:by_ring[row['proxy'],row['ring']].append(row)
    writes=[]
    for rr in by_ring.values():
        selected=sorted((r for r in rr if r['payload_bytes']>=4096),key=lambda r:r['post_begin'])
        for position,row in enumerate(selected,1):
            older=[r for r in rr if r['post_begin']<row['post_begin'] and r['cqe']>row['post_begin']]
            pending=[r for r in rr if r['polled']<row['polled'] and r['ack']>row['polled']]
            writes.append(dict(index=window['index'],proxy=row['proxy'],ring=row['ring'],wr_id=row['wr_id'],
                payload_position=position,payload_bytes=row['payload_bytes'],
                post_to_cqe_us=(row['cqe']-row['post_end'])/1000,
                prior_observed_pending=len(pending),prior_uncompleted=len(older),
                prior_uncompleted_atomics=sum(r['atomic'] for r in older)))
    return writes


def audit(verbs_control,verbs_trace,urma_control,urma_trace,output):
    positions=[];tail_rows=[];summaries=[]
    with tempfile.TemporaryDirectory() as temporary:
        for backend,control,trace in [('verbs',verbs_control,verbs_trace),('urma',urma_control,urma_trace)]:
            accepted=matched.summarize(control,trace,Path(temporary)/backend)
            if not all(r['representative'] for r in accepted if r['label'].startswith('dispatch_')):raise ValueError('unrepresentative Dispatch capture')
            for rank in (0,1):
                case=trace/f'node{rank}'/('normal' if backend=='urma' else 'r01_baseline/normal')
                selected=[row for w in matched.base.rows(case/'event_trace.jsonl') if w['index']>0 and w['label']=='dispatch_bf16_chunk32' for row in requests(w)]
                if len(selected)!=1188:raise ValueError('unexpected BF16 payload coverage')
                threshold=quantile([r['post_to_cqe_us'] for r in selected],.95)
                tail=[r for r in selected if r['post_to_cqe_us']>=threshold]
                tail_rows.extend(dict(backend=backend,node=rank,**r) for r in tail)
                summaries.append(dict(backend=backend,node=rank,requests=len(selected),tail_requests=len(tail),
                    p95_us=threshold,tail_positions=dict(collections.Counter(r['payload_position'] for r in tail)),
                    observed_pending_counts=dict(collections.Counter(r['prior_observed_pending'] for r in selected))))
                grouped=collections.defaultdict(list)
                for r in selected:grouped[r['payload_position']].append(r)
                for pos,rr in sorted(grouped.items()):
                    values=[r['post_to_cqe_us'] for r in rr]
                    positions.append(dict(backend=backend,node=rank,payload_position=pos,requests=len(rr),
                        p50_us=statistics.median(values),p95_us=quantile(values,.95),
                        median_prior_pending=statistics.median(r['prior_observed_pending'] for r in rr),
                        median_prior_uncompleted_atomics=statistics.median(r['prior_uncompleted_atomics'] for r in rr)))
    output.mkdir(parents=True,exist_ok=False)
    matched.base.write(output/'payload_positions.csv',positions)
    matched.base.write(output/'top5_payload_requests.csv',tail_rows)
    (output/'context.json').write_text(json.dumps(dict(summaries=summaries,
        note='BF16, 4096 tokens, nine valid windows per node. Position means issue order among payload WRs within each (Proxy, GPU ring, window), not a global WR number. Pending is reconstructed only from complete observed business chains and is a lower bound, not hardware queue occupancy. Earlier CQE timestamps are software observations. A concentration at one position motivates an intervention; it does not prove queue or fence causality.'),indent=2))
    print('WINDOW_AUDIT_PASSED '+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('verbs-control','verbs-trace','urma-control','urma-trace','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();audit(a.verbs_control,a.verbs_trace,a.urma_control,a.urma_trace,a.output)
