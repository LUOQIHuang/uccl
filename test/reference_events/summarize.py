"""API perturbation controls plus local-clock reference event/request tables."""
import argparse
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dispatch_diag'))
# A distinct module name avoids importing this summarize.py recursively.
import importlib.util
spec=importlib.util.spec_from_file_location('dispatch_summary',Path(__file__).resolve().parents[1]/'dispatch_diag/summarize.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)


def summarize(baseline,trace,post,output):
    api=[];gpu=[];cpu=[];quality=[];context=[]
    for rank in (0,1):
        b=base.case_data(baseline,rank,False)
        t=base.case_data(trace,rank,False,reference_events='on')
        p=base.case_data(post,rank,False)
        if not base.sig(b['identity'])==base.sig(t['identity'])==base.sig(p['identity']) or b['runtime']['module_sha']!=p['runtime']['module_sha']:raise ValueError('source/runtime/boot/control module changed')
        context.append(dict(node=rank,**t['identity'],trace_module_sha=t['runtime']['module_sha'],base_module_sha=b['runtime']['module_sha']))
        for label in sorted(base.ALL_LABELS):
            bv,tv,pv=[v['means'][label]['mean_us'] for v in (b,t,p)]
            drift=(pv/bv-1)*100;change=(tv/((bv+pv)/2)-1)*100
            cvs=[v['means'][label]['cv_pct'] for v in (b,t,p)]
            api.append(dict(node=rank,label=label,baseline_us=bv,trace_us=tv,post_us=pv,drift_pct=drift,trace_change_pct=change,
                baseline_cv_pct=cvs[0],trace_cv_pct=cvs[1],post_cv_pct=cvs[2],representative=abs(drift)<=5 and abs(change)<=5 and max(cvs)<=5))
        for window in base.rows(t['case']/'event_trace.jsonl'):
            if not window['index']:continue
            g=window['gpu'];c=window['cpu'];key=dict(node=rank,label=window['label'],index=window['index'])
            for r in g['records']:
                gpu.append(dict(**key,**r,host_offset_us=(r['host_ns']-c['origin_ns'])/1000,clock_read_cycles=r['device_after']-r['device_before']))
            groups=collections.defaultdict(lambda:collections.defaultdict(list))
            for e in c['events']:groups[e['proxy'],e['wr_id']][e['stage']].append(e)
            matched=posted=0
            for (proxy,wrid),events in groups.items():
                if 'post_begin' not in events:continue
                posted+=1
                stages=['poll','decoded','prepare','post_begin','post_end','cqe','ack_slot']
                if not all(s in events for s in stages):continue
                ns=[min(e['ns'] for e in events[s]) for s in stages]
                ns[4]=max(e['ns'] for e in events['post_end']);ns[5]=max(e['ns'] for e in events['cqe'])
                if ns!=sorted(ns):raise ValueError('non-monotonic local request chain')
                matched+=1
                row=dict(**key,proxy=proxy,wr_id=wrid,payload_bytes=max((e['value'] for e in events.get('payload_bytes',[])),default=0),
                         native_opcode=events['post_begin'][0]['value'],endpoint=events['post_begin'][0]['qp'],
                         physical_posts=len(events['post_begin']),endpoint_count=len({e['qp'] for e in events['post_begin']}),
                         post_offset_us=(ns[3]-c['origin_ns'])/1000)
                for i in range(1,len(stages)):row[stages[i-1]+'_to_'+stages[i]+'_us']=(ns[i]-ns[i-1])/1000
                cpu.append(row)
            if not matched:raise ValueError('no complete FIFO CPU chains')
            quality.append(dict(**key,matched=matched,posted=posted,match_fraction=matched/posted,**g['clock_stats']))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('api_controls.csv',api),('gpu_events.csv',gpu),('cpu_requests.csv',cpu),('capture_quality.csv',quality)]:base.write(output/name,rows)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,note='CLOCK_MONOTONIC_RAW is local to each host. GPU reads a periodically updated mapped host clock, not an exact instantaneous host timestamp. CPU CQE intervals include transport and polling. Events overlap and are not additive critical-path stages. No one-to-one GPU/CPU request pairing is assumed.'),indent=2))
    usable=all(r['representative'] for r in api if r['label'].startswith('dispatch_'))
    print('REFERENCE_EVENT_SUMMARY_PASSED usable='+str(usable)+' output='+str(output))
    return api


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('baseline','trace','post','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();summarize(a.baseline,a.trace,a.post,a.output)
