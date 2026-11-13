"""Replay complete software FIFO ownership during accepted boundary workloads.

Offline only. collect/ack_slot delimit software pending-deque membership;
they do not report hardware SQ occupancy or hardware completion arrival.
"""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
import tempfile
import summarize_boundaries as boundaries


def replay(window,limit=8):
    groups=collections.defaultdict(lambda:collections.defaultdict(list))
    rings=collections.defaultdict(list)
    for event in window['cpu']['events']:
        groups[event['proxy'],event['wr_id']][event['stage']].append(event)
        if event['stage'] in ('collect','ack_slot'):
            rings[event['proxy'],event['wr_id']>>32].append(event)
    segments={}
    for ring,events in rings.items():
        pending=[];seen=set();previous=window['cpu']['origin_ns'];spans=[]
        for event in sorted(events,key=lambda e:e['ns']):
            now=event['ns']
            if now<previous:raise ValueError('FIFO event precedes origin')
            spans.append((previous,now,tuple(pending)))
            wid=event['wr_id']
            if event['stage']=='collect':
                if wid in seen:raise ValueError('duplicate FIFO collection')
                seen.add(wid);pending.append(wid)
                if len(pending)>limit:raise ValueError('FIFO ownership exceeds configured limit')
            else:
                if not pending or pending.pop(0)!=wid:raise ValueError('missing or out-of-order FIFO retirement')
            previous=now
        if pending:raise ValueError('incomplete FIFO retirement at capture end')
        segments[ring]=spans
    return groups,segments


def kind(events):
    if 'payload_bytes' in events:
        return 'payload' if max(e['value'] for e in events['payload_bytes'])>=4096 else 'metadata'
    return 'atomic' if 'atomic_value' in events else 'local'


def occupancy(segments,groups,proxy,start,end,limit=8):
    if end<=start:raise ValueError('nonpositive publication-to-poll interval')
    covered=full=atomic=payload=atomic_no_cqe=0;initial=None
    for a,b,pending in segments:
        duration=max(0,min(b,end)-max(a,start))
        if not duration:continue
        covered+=duration
        if initial is None:initial=len(pending)
        if len(pending)==limit:
            full+=duration
            head=kind(groups[proxy,pending[0]])
            if head=='atomic':
                atomic+=duration
                completed=groups[proxy,pending[0]].get('cqe',[])
                if not completed:raise ValueError('atomic head without observed completion')
                cqe=max(e['ns'] for e in completed)
                atomic_no_cqe+=max(0,min(b,end,cqe)-max(a,start))
            if head=='payload':payload+=duration
    if covered!=end-start:raise ValueError('uncovered FIFO observation interval')
    return dict(pending_at_publication=initial,published_to_poll_us=covered/1000,
        full_us=full/1000,full_pct=100*full/covered,
        atomic_head_full_pct=100*atomic/covered,payload_head_full_pct=100*payload/covered,
        atomic_head_no_observed_cqe_pct=100*atomic_no_cqe/covered)


def audit(control,trace,post,output,fence_plan='default'):
    with tempfile.TemporaryDirectory() as temporary:
        accepted=boundaries.summarize(control,trace,post,Path(temporary)/'summary',fence_plan=fence_plan)
        with (Path(temporary)/'summary/capture_quality.csv').open(newline='') as stream:
            quality=list(csv.DictReader(stream))
    labels=[]
    for label in sorted({r['label'] for r in accepted if r['label'].startswith('dispatch_')}):
        controls=[r for r in accepted if r['label']==label]
        windows=[r for r in quality if r['label']==label]
        if {r['node'] for r in controls}=={0,1} and all(r['representative'] for r in controls) and windows and all(int(r['gpu_before_origin'])==0 and int(r['scheduler_error'])==0 for r in windows):labels.append(label)
    if not labels:raise ValueError('no workload passed controls on both nodes')
    rows=[]
    for rank in (0,1):
        node=trace/('node'+str(rank))
        case=node/'normal' if (node/'normal').is_dir() else node/'r01_baseline/normal'
        for window in boundaries.matched.base.rows(case/'event_trace.jsonl'):
            if not window['index'] or window['label'] not in labels:continue
            groups,rings=replay(window)
            gpu=boundaries.channel_records(window)
            for channel in range(12):
                proxy,ring=channel%4,channel//4
                payloads=sorted((min(e['ns'] for e in stages['poll']),wid) for (p,wid),stages in groups.items() if p==proxy and wid>>32==ring and kind(stages)=='payload')
                if len(payloads)!=11:raise ValueError('unexpected payload count for channel/ordinal mapping')
                polled,wid=payloads[-1];published=gpu[channel][4][0]['host_ns']
                rows.append(dict(node=rank,label=window['label'],index=window['index'],channel=channel,proxy=proxy,ring=ring,wr_id=wid,
                    publication_ns=published,poll_ns=polled,
                    **occupancy(rings[proxy,ring],groups,proxy,published,polled)))
    output.mkdir(parents=True,exist_ok=False)
    boundaries.matched.base.write(output/'last_write_fifo.csv',rows)
    medians=[]
    for rank in (0,1):
        for label in labels:
            rr=[r for r in rows if r['node']==rank and r['label']==label]
            metrics={k:statistics.median(r[k] for r in rr) for k in ('published_to_poll_us','full_pct','atomic_head_full_pct','payload_head_full_pct','atomic_head_no_observed_cqe_pct')}
            medians.append(dict(node=rank,label=label,observations=len(rr),min_full_pct=min(r['full_pct'] for r in rr),**metrics))
    boundaries.matched.base.write(output/'fifo_medians.csv',medians)
    (output/'context.json').write_text(json.dumps(dict(accepted_labels=labels,limit=8,fence_plan=fence_plan,
        notes='All collect/ack_slot events are replayed, including local commands, not just completed network business chains. Reject missing, duplicate, out-of-order, over-limit or unretired ownership. Publication is GPU event4 after final payload/tail publication; correspondence uses one remote peer, channel and issue ordinal. Percentages describe software pending ownership, not hardware occupancy or a counterfactual speedup. Boundary timestamps include observer overhead. Per-workload controls are required on both nodes; global usable=False does not discard a different workload that passed.'),indent=2))
    print('BOUNDARY_FIFO_AUDIT_PASSED labels='+','.join(labels)+' '+str(output))
    return medians


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','trace','post','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args();audit(args.control,args.trace,args.post,args.output)
