"""Paired, real-Normal causal trial. Injected delay stays inside both timing clocks."""
import argparse
import json
import math
from pathlib import Path
import re
import statistics
from phase_support import distribution
from summarize_normal_ab import read,rows,write,LABELS


def summarize(root,output):
    tasks={};runs=[];diagnostics=[];groups={};provenance=set()
    for path in sorted(root.glob('*/node*/setup.json')):
        setup=read(path);c=setup['fingerprint'];node=path.parent;case=node/'normal'
        if not c.get('dispatch_trial'):continue
        if setup.get('error'):raise ValueError('setup failed: '+str(node))
        status=read(case/'status.json')
        if status['rc'] or not status['checks'] or not all(status['checks'].values()):raise ValueError('failed case: '+str(case))
        if c['mode']!='normal' or c['backend']!='urma' or not c['api_only'] or c['normal_completion']!='all' or c['delay_node']!=1:raise ValueError('unexpected trial protocol')
        raw=rows(case/'dispatch_trial_samples.jsonl');api=rows(case/'api_samples.jsonl')
        machine=read(node/'host.json');rank=int(node.name[-1]);n=c['samples'];on=c['stage_profile'];delay=c['dispatch_delay_us']
        if delay not in (0,200) or rank not in (0,1):raise ValueError('invalid node/delay')
        if {r['label'] for r in raw}!=LABELS or len(raw)!=5*n or len(api)!=len(raw):raise ValueError('missing samples')
        trace_rows=rows(case/'dispatch_trial_trace.jsonl') if on else []
        if on and len(trace_rows)!=2:raise ValueError('missing first-sample dispatch traces')
        for label in sorted(LABELS):
            rr=[r for r in raw if r['label']==label];ar=[r for r in api if r['label']==label]
            if len(rr)!=n or [r['index'] for r in rr]!=list(range(n)) or [r['used'] for r in rr]!=[False]+[True]*(n-1):raise ValueError('sample indices mismatch')
            for r,a in zip(rr,ar):
                if any(r[k]!=a[k] for k in ('label','mode','index','used','us')) or r['mode']!='normal' or not all(0<r[k]<float('inf') for k in ('us','host_us')):raise ValueError('invalid raw timing')
                if label.startswith('dispatch_'):
                    t=r['trial'];local_delay=delay if rank==1 else 0
                    if r['requested_delay_us']!=local_delay or t['delay_us']!=local_delay or t['dropped'] or {f['proxy'] for f in t['first_payload']}!={0,1,2,3} or len(t['first_payload'])!=4:raise ValueError('injection not exercised by four proxies')
                    for f in t['first_payload']:
                        if f['ready_ns']<t['origin_ns'] or f['released_ns']-f['ready_ns']<local_delay*1000:raise ValueError('invalid actual injected delay')
                    detailed=on and r['index']==0
                    if bool(t['submits'])!=detailed:raise ValueError('unexpected detailed tracing')
                    if detailed:
                        if next(x for x in trace_rows if x['label']==label)!=r:raise ValueError('trace/sample mismatch')
                        for f in t['first_payload']:
                            if not any(s['proxy']==f['proxy'] and s['bytes']>=4096 and s['ns']>=f['released_ns'] for s in t['submits']):raise ValueError('missing actual payload SDK submission')
                        diagnostics.append(dict(job=c['job'],node=node.name,label=label,delay_us=local_delay,
                            sdk_calls=len(t['submits']),write_requests=sum(s['writes'] for s in t['submits']),
                            write_bytes=sum(s['bytes'] for s in t['submits']),fadds=sum(s['fadds'] for s in t['submits']),
                            first_submit_us=(min(s['ns'] for s in t['submits'])-t['origin_ns'])/1000,
                            last_submit_us=(max(s['ns'] for s in t['submits'])-t['origin_ns'])/1000,
                            min_actual_hold_us=min(f['released_ns']-f['ready_ns'] for f in t['first_payload'])/1000,
                            max_actual_hold_us=max(f['released_ns']-f['ready_ns'] for f in t['first_payload'])/1000))
                elif r['trial'] is not None or r['requested_delay_us']:raise ValueError('control call unexpectedly delayed')
        key=(node.parent.name,rank)
        if key in tasks:raise ValueError('duplicate node task')
        tasks[key]=(c,raw,machine)
    if not tasks:raise ValueError('no trial tasks')
    for job in sorted({k[0] for k in tasks}):
        if (job,0) not in tasks or (job,1) not in tasks:raise ValueError('both node0/node1 are required: '+job)
        c,a,h0=tasks[job,0];c1,b,h1=tasks[job,1]
        if c!=c1:raise ValueError('two-node fingerprint mismatch')
        on=c['stage_profile'];delay=c['dispatch_delay_us']
        if on:continue
        if c['capture_layout'] or c['samples']!=50 or c['warmups']!=20:raise ValueError('formal trial must use no capture, 20/50')
        provenance.add(json.dumps(dict(kernel=c['kernel'],build=c['build_hash'],diagnostics=c['diagnostics_hash'],libraries=c['libraries'],torch=c['torch'],shape=c['shape'],boot0=h0['boot_id'],boot1=h1['boot_id'],affinity0=h0['parent_affinity'],affinity1=h1['parent_affinity']),sort_keys=True))
        match=re.search(r'_r(0[123])_d(0|200)$',job)
        if not match or int(match[2])!=delay:raise ValueError('unexpected formal job tag')
        for label in sorted(LABELS):
            aa=[r for r in a if r['label']==label and r['used']];bb=[r for r in b if r['label']==label and r['used']]
            record=dict(job=job,round=match[1],label=label,delay_us=delay,
                node0_mean_us=statistics.mean(r['us'] for r in aa),node1_mean_us=statistics.mean(r['us'] for r in bb),
                **distribution([max(x['us'],y['us']) for x,y in zip(aa,bb)]))
            record['paired_host_mean_us']=statistics.mean(max(x['host_us'],y['host_us']) for x,y in zip(aa,bb))
            runs.append(record);groups.setdefault((label,delay),[]).append(record)
    if len(provenance)!=1:raise ValueError('formal provenance/boot/affinity mismatch')
    comparison=[]
    for label in sorted(LABELS):
        a,b=groups.get((label,0),[]),groups.get((label,200),[])
        if {r['round'] for r in a}!={'01','02','03'} or len(a)!=3 or {r['round'] for r in b}!={'01','02','03'} or len(b)!=3:raise ValueError('need all three paired rounds: '+label)
        def median(rr,k):return statistics.median(r[k] for r in rr)
        base,candidate=median(a,'mean_us'),median(b,'mean_us');host0,host1=median(a,'paired_host_mean_us'),median(b,'paired_host_mean_us')
        comparison.append(dict(label=label,baseline_pair_max_us=base,delay_pair_max_us=candidate,change_pct=(candidate/base-1)*100,
            baseline_host_pair_max_us=host0,delay_host_pair_max_us=host1,host_change_pct=(host1/host0-1)*100,
            baseline_node0_us=median(a,'node0_mean_us'),delay_node0_us=median(b,'node0_mean_us'),
            baseline_node1_us=median(a,'node1_mean_us'),delay_node1_us=median(b,'node1_mean_us'),
            baseline_run_min_us=min(r['mean_us'] for r in a),baseline_run_max_us=max(r['mean_us'] for r in a),
            delay_run_min_us=min(r['mean_us'] for r in b),delay_run_max_us=max(r['mean_us'] for r in b)))
    if len(diagnostics)!=8 or {r['delay_us'] for r in diagnostics}!={0,200}:raise ValueError('need both diagnostic jobs and both nodes')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('trial_runs.csv',runs),('trial_comparison.csv',comparison),('trial_diagnostics.csv',diagnostics)]:write(output/name,data)
    print('DISPATCH_TRIAL_SUMMARY_PASSED output='+str(output));return runs,comparison,diagnostics


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();summarize(a.root,a.output)
