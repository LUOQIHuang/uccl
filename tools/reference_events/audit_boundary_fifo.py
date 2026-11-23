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
    covered=full=atomic=payload=atomic_no_cqe=payload_no_cqe=0;initial=None
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
            if head=='payload':
                payload+=duration
                completed=groups[proxy,pending[0]].get('cqe',[])
                if not completed:raise ValueError('payload head without observed completion')
                cqe=max(e['ns'] for e in completed)
                payload_no_cqe+=max(0,min(b,end,cqe)-max(a,start))
    if covered!=end-start:raise ValueError('uncovered FIFO observation interval')
    return dict(pending_at_publication=initial,published_to_poll_us=covered/1000,
        full_us=full/1000,full_pct=100*full/covered,
        atomic_head_full_pct=100*atomic/covered,payload_head_full_pct=100*payload/covered,
        atomic_head_no_observed_cqe_pct=100*atomic_no_cqe/covered,
        payload_head_no_observed_cqe_pct=100*payload_no_cqe/covered)


def observe_interval(segments,groups,proxy,start,end):
    signed=(end-start)/1000
    # The WRITE may be read before the GPU has also published its tail; the
    # sampled GPU host clock adds uncertainty too. Never turn this into zero
    # queueing or abort all otherwise complete channels.
    if end<=start:
        return dict(published_to_poll_signed_us=signed,interval_observed=False,
            interval_status='nonpositive_or_overlapping_boundary',pending_at_publication='',
            published_to_poll_us='',full_us='',full_pct='',atomic_head_full_pct='',
            payload_head_full_pct='',atomic_head_no_observed_cqe_pct='',payload_head_no_observed_cqe_pct='')
    return dict(published_to_poll_signed_us=signed,interval_observed=True,
        interval_status='positive_covered_interval',**occupancy(segments,groups,proxy,start,end))


def audit(control,trace,post,output,fence_plan='default',positions='last'):
    if positions not in ('last','both'):raise ValueError('unknown FIFO focus positions')
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
                for ordinal,event in (((6,11),(11,4)) if positions=='both' else ((11,4),)):
                    polled,wid=payloads[ordinal-1];published=gpu[channel][event][0]['host_ns']
                    rows.append(dict(node=rank,label=window['label'],index=window['index'],channel=channel,proxy=proxy,ring=ring,wr_id=wid,
                        payload_ordinal=ordinal,publication_ns=published,poll_ns=polled,
                        **observe_interval(rings[proxy,ring],groups,proxy,published,polled)))
    output.mkdir(parents=True,exist_ok=False)
    boundaries.matched.base.write(output/'last_write_fifo.csv',[r for r in rows if r['payload_ordinal']==11])
    if positions=='both':boundaries.matched.base.write(output/'chunk6_write_fifo.csv',[r for r in rows if r['payload_ordinal']==6])
    medians=[]
    for rank in (0,1):
        for label in labels:
            for ordinal in ((6,11) if positions=='both' else (11,)):
                rr=[r for r in rows if r['node']==rank and r['label']==label and r['payload_ordinal']==ordinal]
                observed=[r for r in rr if r['interval_observed']]
                metrics={k:statistics.median(r[k] for r in observed) if observed else '' for k in ('published_to_poll_us','full_pct','atomic_head_full_pct','payload_head_full_pct','atomic_head_no_observed_cqe_pct','payload_head_no_observed_cqe_pct')}
                medians.append(dict(node=rank,label=label,payload_ordinal=ordinal,observations=len(rr),
                    observed_intervals=len(observed),censored_intervals=len(rr)-len(observed),
                    published_to_poll_signed_median_us=statistics.median(r['published_to_poll_signed_us'] for r in rr),
                    min_full_pct=min(r['full_pct'] for r in observed) if observed else '',**metrics))
    boundaries.matched.base.write(output/'fifo_medians.csv',medians)
    (output/'context.json').write_text(json.dumps(dict(accepted_labels=labels,limit=8,fence_plan=fence_plan,positions=positions,
        notes='All collect/ack_slot events are replayed, including local commands, not just completed network business chains. Reject missing, duplicate, out-of-order, over-limit or unretired ownership. Publication is GPU event4 for last payload or event11 for chunk6, after payload/tail publication; correspondence uses one remote peer, channel and issue ordinal. Nonpositive signed intervals are censored, not zero queueing; overlap and sampled clock uncertainty can both cause them. Percentages apply ONLY to positive fully covered intervals; report observed/censored counts before comparing backends. They describe software pending ownership, not hardware occupancy or a counterfactual speedup. Boundary timestamps include observer overhead. Per-workload controls are required on both nodes; global usable=False does not discard a different workload that passed.'),indent=2))
    print('BOUNDARY_FIFO_AUDIT_PASSED labels='+','.join(labels)+' '+str(output))
    return medians


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('control','trace','post','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--fence-plan',choices=('default','all-on','peer-off','peer-tail-off'),default='default')
    p.add_argument('--positions',choices=('last','both'),default='last')
    args=p.parse_args();audit(args.control,args.trace,args.post,args.output,args.fence_plan,args.positions)
