"""Validate both nodes, independent repetitions, native counts and local request timelines."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import common
from notify_support import points
from summarize import write

def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def passed(case):
    s=read(case/'status.json')
    if s['rc'] or not s['checks'] or not all(s['checks'].values()):raise ValueError('failed/incomplete '+str(case))

def timeline_rows(path,node):
    windows=rows(path);out=[];coverage=[]
    if len(windows)!=2 or {w['label'] for w in windows}!={'dispatch_fp8_chunk32','dispatch_bf16_chunk32'}:raise ValueError('need FP8/BF16 timelines')
    stages=['collect','prepare','post_begin','post_end','cqe','ack_slot']
    for w in windows:
        if w['dropped'] or w['index']!=0:raise ValueError('truncated/unexpected timeline window')
        by=collections.defaultdict(lambda:collections.defaultdict(list));tails=collections.defaultdict(list)
        for e in w['events']:
            if e['ns']<w['origin_ns'] or e['proxy']<0:raise ValueError('timeline identity/clock mismatch')
            by[(e['proxy'],e['wr_id'])][e['stage']].append(e)
            if e['stage']=='tail_published':tails[(e['proxy'],e['wr_id']>>32)].append(e)
        matched=posted=0
        for (proxy,wrid),events in by.items():
            if 'post_begin' not in events:continue
            posted+=1
            if not all(k in events for k in stages):continue
            qp={e['qp'] for e in events['post_begin']}
            if len(qp)!=1:continue
            ns=[min(e['ns'] for e in events[k]) for k in stages]
            ns[3]=max(e['ns'] for e in events['post_end']);ns[4]=max(e['ns'] for e in events['cqe'])
            if ns!=sorted(ns):raise ValueError('non-monotonic matched request')
            if any(e['value'] for k in ('post_end','cqe') for e in events[k]):raise ValueError('timeline post/CQE error')
            matched+=1
            row=dict(node=node,label=w['label'],proxy=proxy,wr_id=wrid,qp=next(iter(qp)),opcode=events['post_begin'][0]['value'],physical_posts=len(events['post_begin']),collect_offset_us=(ns[0]-w['origin_ns'])/1000)
            for i in range(1,len(stages)):row[stages[i-1]+'_to_'+stages[i]+'_us']=(ns[i]-ns[i-1])/1000
            row['collect_to_ack_slot_us']=(ns[-1]-ns[0])/1000
            released=[e['ns'] for e in tails[(proxy,wrid>>32)] if e['ns']>=ns[-1] and e['value']>(wrid&0xffffffff)]
            row['ack_slot_to_tail_published_us']=(min(released)-ns[-1])/1000 if released else ''
            out.append(row)
        if not matched:raise ValueError('no complete request chains in '+str(path))
        coverage.append(dict(node=node,label=w['label'],posted_commands=posted,matched_commands=matched,match_fraction=matched/posted,events=len(w['events'])))
    return out,coverage

def summarize(root,output):
    raw_runs=[];timeline=[];coverage=[];seen=collections.defaultdict(set);provenance=collections.defaultdict(set);micro_jobs=set();trace_jobs=set();groups=collections.defaultdict(list)
    for setup_path in sorted(root.glob('*/node*/setup.json')):
        node=setup_path.parent;rank=int(node.name[-1]);setup=read(setup_path);c=setup['fingerprint'];host=read(node/'host.json')
        if setup['error']:raise ValueError('failed setup '+str(node))
        kind='micro' if c.get('suite')=='verbs_notify_micro' else 'timeline'
        seen[(node.parent.name,kind)].add(rank)
        provenance[rank].add(common.digest(dict(identity=c['identity'],kernel=c['kernel'],torch=c['torch'],libraries=c['libraries'],gid=c['gid_index'],hca=c['hca'],boot=host['boot_id'],diagnostics=c['diagnostics'])))
        if kind=='timeline':
            trace_jobs.add(node.parent.name)
            if not c.get('timeline') or not c.get('cpu_diagnostics') or c['mode']!='normal' or c['warmups']!=5 or c['samples']!=5:raise ValueError('wrong timeline configuration')
            case=node/'cpu_diagnostic/normal';passed(case);common.validate_samples(case,'normal',5)
            for w in rows(case/'verbs_phases.jsonl'):
                for rec in w['records']:
                    if any(rec['metrics'].get(k,{}).get('calls',0) for k in ('verbs_post_errors','verbs_completion_errors','verbs_tracking_overflow')):raise ValueError('diagnostic provider/tracking error')
            t,v=timeline_rows(case/'verbs_timeline.jsonl',node.name);timeline.extend(t);coverage.extend(v);continue
        micro_jobs.add(node.parent.name);case=node/'micro';passed(case)
        if c['threads']!=4 or c['warmups']!=20 or c['samples']!=50:raise ValueError('unexpected micro sampling')
        req=read(case/'request.json');plan=read(case/'plan.json');expected=points(plan['geometries'],c['repeat'])
        if len(expected)!=8 or plan['points']!=expected or req['fingerprint']!=c:raise ValueError('micro plan mismatch')
        native=read(case/'verbs_path.json')
        if native.get('notifications') is not True or native.get('control_memory')!='gpu' or native.get('atomic_return_memory')!='host' or native.get('registration')!='dmabuf' or native.get('gid_index')!=c['gid_index'] or native.get('fadd_send_fence') is not False:raise ValueError('native path mismatch')
        placement=read(node/'affinity.json');pair=read(case/'affinity_pair.json')
        if pair[str(rank)]!=placement or req['placement']!=placement or len(placement['workers'])!=4:raise ValueError('affinity mismatch')
        samples=rows(case/'micro_samples.jsonl');saved=rows(case/'micro_summary.jsonl')
        if len(samples)!=400 or len(saved)!=8 or {r['label'] for r in samples}!={p['label'] for p in expected}:raise ValueError('incomplete micro samples')
        for p in expected:
            g=p['geometry'];op=p['operation'];rr=[r for r in samples if r['label']==p['label']]
            if [r['index'] for r in rr]!=list(range(50)) or [r['used'] for r in rr]!=[False]+[True]*49:raise ValueError('sample boundary mismatch')
            for r in rr:
                if r['operation']!=op or r['visible']!=p['visible'] or r['threads']!=4 or not r['send'] or not r['receive'] or r['direction']!='bidir' or any(r[k]!=g[k] for k in ('bytes','src_mod','dst_mod')):raise ValueError('sample geometry/mode mismatch')
                if not math.isfinite(r['us']) or r['us']<=0 or r['us']!=r['span_ns']/1000 or r['cqes']!=(64 if op==2 else 32) or r['retired']!=r['cqes'] or r['write_bytes']!=(0 if op==1 else 32*g['bytes']) or not 0<r['cq_span_ns']<=r['span_ns']:raise ValueError('sample CQE/bytes/timing mismatch')
            stats=common.phase_support.distribution([r['us'] for r in rr if r['used']]);s=next(s for s in saved if s['label']==p['label'])
            if not s['validation'] or any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('saved/raw summary mismatch')
            row=dict(node=node.name,host=host['host'],job=c['job'],label=p['label'],operation=op,visible=p['visible'],bytes=g['bytes'],cq_mean_us=statistics.mean(r['cq_span_ns']/1000 for r in rr if r['used']),tx_GBps=32*g['bytes']/stats['mean_us']/1000 if op!=1 else '',**stats)
            raw_runs.append(row);key=(node.name,p['label'],g['bytes'],g['src_mod'],g['dst_mod'],common.digest(pair),common.digest(native));groups[key].append(row)
    if len(micro_jobs)!=3 or len(trace_jobs)!=1 or len(seen)!=4 or any(v!={0,1} for v in seen.values()) or any(len(provenance[r])!=1 for r in (0,1)):raise ValueError('need three matched micro jobs + one trace job, both nodes, stable build/kernel/boot')
    if any({read(p)['fingerprint']['repeat'] for p in root.glob('*/'+node+'/setup.json') if read(p)['fingerprint'].get('suite')=='verbs_notify_micro'}!={1,2,3} for node in ('node0','node1')):raise ValueError('missing repeat/order configuration')
    aggregate=[]
    for key,rr in sorted(groups.items()):
        if len(rr)!=3 or len({r['job'] for r in rr})!=3:raise ValueError('missing independent repeats')
        r=rr[0];mean=statistics.median(r['mean_us'] for r in rr)
        aggregate.append(dict(node=r['node'],label=r['label'],operation=r['operation'],visible=r['visible'],bytes=r['bytes'],independent_runs=3,median_run_mean_us=mean,min_run_mean_us=min(r['mean_us'] for r in rr),max_run_mean_us=max(r['mean_us'] for r in rr),tx_GBps=32*r['bytes']/mean/1000 if r['operation']!=1 else ''))
    if len(aggregate)!=16:raise ValueError('missing host/shape/mode groups')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('notification_runs.csv',raw_runs),('notification_aggregate.csv',aggregate),('request_timeline.csv',timeline),('timeline_coverage.csv',coverage)]:write(output/name,data)
    print('VERBS_NOTIFICATION_SUMMARY_PASSED output='+str(output));return raw_runs,aggregate,timeline

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();summarize(a.root,a.output)
