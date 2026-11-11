"""Compare an inactive paced control with active trace under identical provenance."""
import argparse
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('reference_summary',REPO/'test/reference_events/summarize.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)
base=reference.base


def summarize(control,trace,output):
    rows=[];context=[];cases=[]
    for rank in (0,1):
        p=base.case_data(control,rank,False,reference_events='paced')
        t=base.case_data(trace,rank,False,reference_events='on')
        if base.sig(p['identity'])!=base.sig(t['identity']) or p['runtime']['module_sha']!=t['runtime']['module_sha']:
            raise ValueError('paced/on source, diagnostics, runtime, boot or module changed; do not relabel historical results')
        context.append(dict(node=rank,**t['identity'],module_sha=t['runtime']['module_sha']))
        cases.append((rank,t['case']))
        for label in sorted(base.ALL_LABELS):
            pv,tv=p['means'][label],t['means'][label]
            change=(tv['mean_us']/pv['mean_us']-1)*100
            rows.append(dict(node=rank,label=label,paced_us=pv['mean_us'],trace_us=tv['mean_us'],change_pct=change,
                paced_cv_pct=pv['cv_pct'],trace_cv_pct=tv['cv_pct'],
                representative=abs(change)<=5 and max(pv['cv_pct'],tv['cv_pct'])<=5))
    output.mkdir(parents=True,exist_ok=False)
    base.write(output/'matched_controls.csv',rows)
    export_events(cases,output)
    (output/'context.json').write_text(json.dumps(dict(nodes=context,
        analyzer_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        note='Paced and active trace use the same explicit synchronization/barrier and Event creation pattern, with 5 warmups and 10 samples. Active trace additionally resets/reads records and runs a host clock/CPU timeline. This is a two-case gate without a new post control, not proof of zero internal perturbation or an additive critical path.'),indent=2))
    usable=all(r['representative'] for r in rows if r['label'].startswith('dispatch_'))
    print('REFERENCE_MATCHED_SUMMARY_PASSED usable='+str(usable)+' output='+str(output))
    return rows


def export_events(cases,output):
    gpu=[];cpu=[];quality=[]
    stages=['poll','decoded','prepare','post_begin','post_end','cqe','ack_slot']
    intervals=[stages[i-1]+'_to_'+stages[i]+'_us' for i in range(1,len(stages))]
    for rank,case in cases:
        for window in base.rows(case/'event_trace.jsonl'):
            if window['index']==0:continue
            key=dict(node=rank,label=window['label'],index=window['index'])
            g=window['gpu'];c=window['cpu'];negative=0
            for record in g['records']:
                negative+=record['host_ns']<c['origin_ns']
                gpu.append(dict(**key,**record,host_offset_us=(record['host_ns']-c['origin_ns'])/1000,
                    clock_read_cycles=record['device_after']-record['device_before']))
            groups=collections.defaultdict(lambda:collections.defaultdict(list))
            for e in c['events']:groups[e['proxy'],e['wr_id']][e['stage']].append(e)
            matched=posted=physical=0
            for (proxy,wrid),events in groups.items():
                if 'post_begin' not in events:continue
                posted+=1;physical+=len(events['post_begin'])
                if not all(s in events for s in stages):continue
                ns=[min(e['ns'] for e in events[s]) for s in stages]
                ns[4]=max(e['ns'] for e in events['post_end']);ns[5]=max(e['ns'] for e in events['cqe'])
                if ns!=sorted(ns):raise ValueError('non-monotonic complete CPU request')
                matched+=1
                payload=max((e['value'] for e in events.get('payload_bytes',[])),default=0)
                operation='payload_write' if payload>=4096 else 'small_write' if payload else 'atomic' if 'atomic_value' in events else 'other'
                row=dict(**key,proxy=proxy,wr_id=wrid,payload_bytes=payload,operation=operation,
                    native_opcode=events['post_begin'][0]['value'],endpoint=events['post_begin'][0]['qp'],
                    physical_posts=len(events['post_begin']),endpoint_count=len({e['qp'] for e in events['post_begin']}))
                for i,name in enumerate(intervals,1):row[name]=(ns[i]-ns[i-1])/1000
                cpu.append(row)
            quality.append(dict(**key,matched=matched,unique_posted=posted,physical_posts=physical,
                match_fraction=matched/posted if posted else 0,gpu_before_origin=negative,
                gpu_dropped=g['dropped'],cpu_dropped=c['dropped'],**g['clock_stats']))
    summary=[];operation_rows=[]
    keys=sorted({(r['node'],r['label']) for r in cpu})
    for rank,label in keys:
        selected=[r for r in cpu if (r['node'],r['label'])==(rank,label)]
        summary.append(dict(node=rank,label=label,requests=len(selected),windows=len({r['index'] for r in selected}),
            **{name:statistics.median(r[name] for r in selected) for name in intervals}))
        for op in sorted({r['operation'] for r in selected}):
            selected_op=[r for r in selected if r['operation']==op]
            operation_rows.append(dict(node=rank,label=label,operation=op,requests=len(selected_op),
                median_post_end_to_cqe_us=statistics.median(r['post_end_to_cqe_us'] for r in selected_op)))
    for name,data in [('gpu_events.csv',gpu),('cpu_requests.csv',cpu),('capture_quality.csv',quality),
                      ('cpu_stage_medians.csv',summary),('cpu_operation_medians.csv',operation_rows)]:
        base.write(output/name,data)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('control','trace','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();summarize(args.control,args.trace,args.output)
