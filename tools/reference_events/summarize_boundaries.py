"""Validate paced/on/post and export sparse Dispatch boundary observations."""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
import summarize_matched as matched


def channel_records(window):
    result=collections.defaultdict(lambda:collections.defaultdict(list))
    for record in window['gpu']['records']:result[record['block']//2][record['event']].append(record)
    return result


def spans(window):
    rows=[]
    definitions=[('sender_first_to_last_ready',1,3),('last_command_publish',3,4),
        ('chunk6_command_publish',10,11),('final_epoch_check',7,2),
        ('forwarder_after_last_remote',2,8),('last_remote_to_receiver_done',2,9),
        ('forwarder_receiver_finish_gap',8,9)]
    for channel,records in sorted(channel_records(window).items()):
        for name,start,end in definitions:
            if start not in records or end not in records:continue
            a,b=records[start][0],records[end][0]
            same_role=(a['block'],a['warp'])==(b['block'],b['warp'])
            if same_role and b['device_before']<a['device_before']:raise ValueError('non-monotonic same-role boundary')
            rows.append(dict(channel=channel,span=name,us=(b['host_ns']-a['host_ns'])/1000,
                same_role=same_role,device_cycles=b['device_before']-a['device_before'] if same_role else '',
                start_read_cycles=a['device_after']-a['device_before'],end_read_cycles=b['device_after']-b['device_before']))
        if 5 in records and 6 in records:
            b=records[6][0]
            starts=[r for r in records[5] if r['device_before']<=b['device_before']]
            if not starts:raise ValueError('final-tail observation without preceding check')
            starts.sort(key=lambda r:r['device_before'])
            for name,a in [('final_tail_check_span',starts[0]),('final_tail_successful_attempt',starts[-1])]:
                rows.append(dict(channel=channel,span=name,us=(b['host_ns']-a['host_ns'])/1000,
                    same_role=True,device_cycles=b['device_before']-a['device_before'],
                    start_read_cycles=a['device_after']-a['device_before'],end_read_cycles=b['device_after']-b['device_before']))
    return rows


def cpu_focus(window):
    groups=collections.defaultdict(lambda:collections.defaultdict(list));beats=collections.defaultdict(list)
    for e in window['cpu']['events']:
        groups[e['proxy'],e['wr_id']][e['stage']].append(e)
        if e['stage']=='poll_heartbeat':beats[e['proxy']].append(e['ns'])
    payloads=collections.defaultdict(list)
    for (proxy,wid),events in groups.items():
        if wid==2**64-1 or max((e['value'] for e in events.get('payload_bytes',[])),default=0)<4096:continue
        if not all(s in events for s in ('poll','decoded','prepare','post_begin','post_end','cqe','ack_slot')):continue
        channel=proxy+4*(wid>>32) # Normal expert/channel -> proxy=ch%4, ring=ch//4.
        if not 0<=channel<12:raise ValueError('unexpected Normal payload channel mapping')
        ns={s:min(e['ns'] for e in events[s]) for s in ('poll','decoded','prepare','post_begin','post_end','cqe','ack_slot')}
        ns['post_end']=max(e['ns'] for e in events['post_end']);ns['cqe']=max(e['ns'] for e in events['cqe'])
        payloads[channel].append((ns,proxy,wid,len(events['post_begin'])))
    gpu=channel_records(window);rows=[]
    for channel in range(12):
        rr=sorted(payloads[channel],key=lambda r:(r[0]['poll'],r[2]))
        if len(rr)!=11:raise ValueError('formal boundary focus requires 11 complete payload WRs/channel')
        for ordinal,ready,published in ((6,10,11),(11,3,4)):
            ns,proxy,wid,posts=rr[ordinal-1]
            heartbeat=sorted(beats[proxy]);before=[t for t in heartbeat if t<=ns['post_begin']];after=[t for t in heartbeat if t>=ns['cqe']]
            bracket=bool(before and after)
            selected=[t for t in heartbeat if (before[-1] if before else ns['post_begin'])<=t<=(after[0] if after else ns['cqe'])]
            gaps=[(b-a)/1000 for a,b in zip(selected,selected[1:])]
            row=dict(channel=channel,proxy=proxy,wr_id=wid,payload_ordinal=ordinal,
                correspondence='channel+issue-order (one remote peer)',physical_posts=posts,
                gpu_ready_to_cpu_poll_signed_us=(ns['poll']-gpu[channel][ready][0]['host_ns'])/1000 if posts==1 else '',
                gpu_published_to_cpu_post_signed_us=(ns['post_begin']-gpu[channel][published][0]['host_ns'])/1000 if posts==1 else '',
                heartbeat_full_bracket=bracket,heartbeat_max_observed_gap_us=max(gaps) if gaps else '',
                heartbeat_points=len(selected))
            order=['poll','decoded','prepare','post_begin','post_end','cqe','ack_slot']
            for a,b in zip(order,order[1:]):row[a+'_to_'+b+'_us']=(ns[b]-ns[a])/1000
            rows.append(row)
    return rows


def summarize(control,trace,post,output):
    controls=[];cases=[];contexts=[]
    for rank in (0,1):
        b=matched.base.case_data(control,rank,False,reference_events='paced')
        t=matched.base.case_data(trace,rank,False,reference_events='on')
        p=matched.base.case_data(post,rank,False,reference_events='paced')
        if any(x['identity'].get('reference_profile')!='boundaries' or x['identity'].get('normal_window_plan')!='default' or x['identity'].get('normal_submit_schedule','lane-mixed')!='lane-mixed' or x['identity'].get('normal_fadd_fence','on')!='on' for x in (b,t,p)):
            raise ValueError('wrong boundary/default transport profile')
        if not matched.base.sig(b['identity'])==matched.base.sig(t['identity'])==matched.base.sig(p['identity']) or len({x['runtime']['module_sha'] for x in (b,t,p)})!=1:
            raise ValueError('boundary controls changed source/module/boot/runtime/workload')
        contexts.append(dict(node=rank,**t['identity'],module_sha=t['runtime']['module_sha']))
        cases.append((rank,t['case']))
        for label in sorted(matched.base.ALL_LABELS):
            bs,ts,ps=[x['means'][label] for x in (b,t,p)]
            bv,tv,pv=[s['mean_us'] for s in (bs,ts,ps)];drift=(pv/bv-1)*100;change=(tv/((bv+pv)/2)-1)*100
            controls.append(dict(node=rank,label=label,paced_us=bv,trace_us=tv,post_us=pv,drift_pct=drift,trace_change_pct=change,
                max_cv_pct=max(s['cv_pct'] for s in (bs,ts,ps)),representative=abs(drift)<=5 and abs(change)<=5 and max(s['cv_pct'] for s in (bs,ts,ps))<=5))
    output.mkdir(parents=True,exist_ok=False)
    matched.base.write(output/'api_controls.csv',controls)
    matched.export_events(cases,output)
    boundaries=[];focus=[]
    for rank,case in cases:
        for window in matched.base.rows(case/'event_trace.jsonl'):
            if not window['index']:continue
            key=dict(node=rank,label=window['label'],index=window['index'])
            boundaries.extend(dict(**key,**r) for r in spans(window))
            focus.extend(dict(**key,**r) for r in cpu_focus(window))
    medians=[]
    grouped=collections.defaultdict(list)
    for row in boundaries:grouped[row['node'],row['label'],row['span']].append(row['us'])
    for (rank,label,name),values in sorted(grouped.items()):
        medians.append(dict(node=rank,label=label,span=name,observations=len(values),median_us=statistics.median(values),min_us=min(values),max_us=max(values)))
    for name,rows in [('boundary_spans',boundaries),('boundary_medians',medians),('cpu_chunk6_and_last',focus)]:matched.base.write(output/(name+'.csv'),rows)
    representative=all(r['representative'] for r in controls if r['label'].startswith('dispatch_'))
    with (output/'capture_quality.csv').open(newline='') as stream:quality=list(csv.DictReader(stream))
    clock_offsets_ok=all(int(r['gpu_before_origin'])==0 and int(r['scheduler_error'])==0 for r in quality)
    usable=representative and clock_offsets_ok
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,usable=usable,performance_representative=representative,clock_offsets_ok=clock_offsets_ok,
        note='Default Normal, sparse dispatch_boundaries_v1 profile. GPU times are sampled RAW clock estimates; same-role clock64 deltas are cycles without frequency conversion. Tail first/last attempt spans differ: first includes intervening round-robin work, last covers the successful check attempt. Finishing gaps can be signed because roles overlap. CPU focus matches one remote peer by channel and payload issue ordinal, not protocol-carried WR identity. Heartbeats sample every 128 SDK polls; only a fully bracketed small maximum gap can exclude a long unserviced interval, a large gap alone does not prove Proxy inactivity. No cross-host timestamp subtraction or additive critical-path claim. If usable=false, keep raw observations but do not treat them as representative performance.'),indent=2))
    print('BOUNDARY_SUMMARY_PASSED usable='+str(usable)+' '+str(output))
    return controls


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('control','trace','post','output'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();summarize(a.control,a.trace,a.post,a.output)
