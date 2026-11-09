"""Strict seven-job URMA Normal notification campaign validation."""
import argparse
import collections
import math
from pathlib import Path
import statistics
import support
from phase_support import distribution
from normal_notify_support import points
from summarize_normal_ab import read,rows,write,LABELS
from timeline_support import timeline_rows


def passed(case):
    s=read(case/'status.json')
    if s['rc'] or not s['checks'] or not all(s['checks'].values()):raise ValueError('failed/incomplete '+str(case))

def api_samples(case,config):
    raw=rows(case/'api_samples.jsonl');saved=rows(case/'sample_summary.jsonl');n=config['samples']
    if len(raw)!=5*n or {r['label'] for r in raw}!=LABELS or len(saved)!=5:raise ValueError('missing Normal samples')
    out=[]
    for label in sorted(LABELS):
        rr=[r for r in raw if r['label']==label]
        if [r['index'] for r in rr]!=list(range(n)) or [r['used'] for r in rr]!=[False]+[True]*(n-1) or any(r['mode']!='normal' or not math.isfinite(r['us']) or r['us']<=0 for r in rr):raise ValueError('Normal sample boundary/value mismatch')
        stats=distribution([r['us'] for r in rr if r['used']]);s=next(s for s in saved if s['label']==label)
        if s['enabled']!=config['stage_profile'] or s['warmups']!=config['warmups'] or s['measured']!=n or any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('Normal saved/raw summary mismatch')
        out.append(dict(label=label,**stats))
    return out

def summarize(root,output):
    jobs=collections.defaultdict(set);seen=collections.defaultdict(set);provenance=collections.defaultdict(set)
    runs=[];micro=[];stages=[];traces=[];coverage=[];groups=collections.defaultdict(list);fullgroups=collections.defaultdict(list);repeats=collections.defaultdict(set)
    for path in sorted(root.glob('*/node*/setup.json')):
        node=path.parent;rank=int(node.name[-1]);setup=read(path);c=setup['fingerprint'];host=read(node/'host.json')
        if setup['error']:raise ValueError('failed setup '+str(node))
        kind='micro' if c.get('suite')=='notify' else 'timeline' if c.get('timeline') else 'baseline'
        jobs[kind].add(node.parent.name);seen[node.parent.name].add(rank)
        if c['normal_completion']!='all':raise ValueError('expected all completion policy')
        provenance[rank].add(support.digest({k:c[k] for k in ('build_hash','kernel','torch','libraries','diagnostics_hash','device','gpu','ifname')}|dict(boot_id=host['boot_id'])))
        identity=dict(node=node.name,host=host['host'],job=c['job'])
        if kind!='micro':
            if c['backend']!='urma' or c['mode']!='normal' or c['shape']!=[4096,128,7168,256,8] or not c['phase_suite'] or not c['api_only'] or c['save_traces'] or c['dispatch_trial'] or c['ll_write_order']!='default':raise ValueError('unexpected full Normal configuration')
            case=node/'normal';passed(case);stats=api_samples(case,c)
            expected=(5,5) if kind=='timeline' else (50,50)
            if (c['warmups'],c['samples'])!=expected or c['stage_profile']!=(kind=='timeline') or c['capture_layout']!=(kind=='timeline'):raise ValueError('baseline/diagnostic mixing')
            windows=rows(case/'phases.jsonl')
            if len(windows)!=5 or {w['label'] for w in windows}!=LABELS or any(w['enabled']!=c['stage_profile'] or w['iterations']!=c['samples'] for w in windows):raise ValueError('missing phase windows')
            if kind=='baseline':
                for w in windows:
                    if any(m['calls'] for r in w['records'] for m in r['metrics'].values()):raise ValueError('baseline instrumentation active')
                for s in stats:
                    row=dict(identity,**s);runs.append(row);fullgroups[(node.name,s['label'])].append(row)
                continue
            layout=rows(case/'write_layout.jsonl')
            if len(layout)!=5 or {l['label'] for l in layout}!=LABELS or any(l['dropped'] for l in layout):raise ValueError('truncated diagnostic shapes')
            activity=collections.Counter()
            for w in windows:
                totals=collections.defaultdict(collections.Counter)
                for rec in w['records']:
                    for k,m in rec['metrics'].items():
                        for key in ('calls','ns','items'):totals[k][key]+=m[key]
                for k,v in totals.items():
                    activity[k]+=v['calls'];stages.append(dict(identity,label=w['label'],metric=k,calls_per_iteration=v['calls']/w['iterations'],items_per_iteration=v['items']/w['iterations'],thread_sum_us_per_iteration=v['ns']/1000/w['iterations']))
            if not all(activity[k] for k in ('write_requests','fadd_requests','sdk_post','submit_to_observed_cqe_sampled')) or activity['pair_recovery_markers']:raise ValueError('missing WRITE/FADD diagnostics or recovery')
            t,v=timeline_rows(case/'urma_timeline.jsonl',node.name)
            for r in t:r['lane']=r.pop('qp')
            traces.extend(t);coverage.extend(v);continue
        case=node/'micro';passed(case);req=read(case/'request.json');plan=read(case/'plan.json');native=read(case/'urma_path.json')
        if c['threads']!=4 or c['warmups']!=20 or c['samples']!=50 or req['fingerprint']!=c:raise ValueError('micro configuration mismatch')
        repeats[rank].add(c['repeat']);expected=points(plan['geometries'],c['repeat'])
        if len(expected)!=8 or plan['points']!=expected:raise ValueError('micro plan mismatch')
        for k,v in dict(channels=8,normal_completion='all',write_order='relax',atomic_order='strong',atomic_fence='per_request',control_memory='gpu',atomic_return_memory='host',production_transport=True).items():
            if native.get(k)!=v:raise ValueError('unexpected native URMA path '+k)
        placement=read(node/'affinity.json');pair=read(case/'affinity_pair.json')
        if pair[str(rank)]!=placement or req['placement']!=placement or len(set(placement['workers']))!=4:raise ValueError('affinity mismatch')
        raw=rows(case/'micro_samples.jsonl');saved=rows(case/'micro_summary.jsonl');layouts=rows(case/'micro_layout_verified.jsonl')
        if len(raw)!=400 or len(saved)!=8 or len(layouts)!=8 or {r['label'] for r in raw}!={p['label'] for p in expected}:raise ValueError('missing micro samples/layouts')
        for p in expected:
            g=p['geometry'];op=p['operation'];rr=[r for r in raw if r['label']==p['label']]
            if [r['index'] for r in rr]!=list(range(50)) or [r['used'] for r in rr]!=[False]+[True]*49:raise ValueError('micro sample boundaries')
            layout=next(l for l in layouts if l['label']==p['label'])
            if layout['dropped'] or layout['modulus']!=4096 or sum(w['count'] for w in layout['writes'])!=(0 if op==1 else 32) or any(any(w[k]!=g[k] for k in ('bytes','src_mod','dst_mod')) for w in layout['writes']):raise ValueError('actual MVA geometry mismatch')
            for r in rr:
                if r['operation']!=op or r['gpu_observed']!=p['visible'] or r['fadd']!=p['fadd'] or r['window']!=1 or r['messages']!=8 or r['direction']!='bidir' or not r['send'] or not r['receive'] or r['local'] or r['bytes']!=g['bytes']:raise ValueError('micro operation/shape mismatch')
                if not math.isfinite(r['us']) or r['us']<=0 or r['us']!=r['span_ns']/1000 or not 0<r['cq_span_ns']<=r['span_ns'] or r['cqes']!=(64 if op==2 else 32) or r['retired']!=r['cqes'] or r['write_bytes']!=(0 if op==1 else 32*g['bytes']):raise ValueError('micro CQE/bytes/timing mismatch')
            stats=distribution([r['us'] for r in rr if r['used']]);s=next(s for s in saved if s['label']==p['label'])
            if not s['validation'] or any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('micro saved/raw summary mismatch')
            row=dict(identity,label=p['label'],operation=op,gpu_observed=p['visible'],bytes=g['bytes'],cq_mean_us=statistics.mean(r['cq_span_ns']/1000 for r in rr if r['used']),**stats);micro.append(row)
            groups[(node.name,p['label'],g['bytes'],g['src_mod'],g['dst_mod'],support.digest(pair),support.digest(native))].append(row)
    if {k:len(v) for k,v in jobs.items()}!={'micro':3,'baseline':3,'timeline':1} or len(seen)!=7 or any(r!={0,1} for r in seen.values()) or any(len(provenance[r])!=1 or repeats[r]!={1,2,3} for r in (0,1)):raise ValueError('need seven complete stable dual-node jobs')
    def aggregate(values,micro=False):
        out=[]
        for key,rr in sorted(values.items()):
            if len(rr)!=3 or len({r['job'] for r in rr})!=3:raise ValueError('missing independent repeats')
            r=rr[0];mean=statistics.median(r['mean_us'] for r in rr)
            row=dict(node=r['node'],host=r['host'],label=r['label'],independent_runs=3,median_run_mean_us=mean,min_run_mean_us=min(r['mean_us'] for r in rr),max_run_mean_us=max(r['mean_us'] for r in rr),cv_run_means=statistics.pstdev(r['mean_us'] for r in rr)/statistics.mean(r['mean_us'] for r in rr))
            if micro:row.update(operation=r['operation'],gpu_observed=r['gpu_observed'],bytes=r['bytes'],tx_GBps=32*r['bytes']/mean/1000 if r['operation']!=1 else '')
            out.append(row)
        return out
    full=aggregate(fullgroups);native=aggregate(groups,True)
    if len(full)!=10 or len(native)!=16:raise ValueError('missing host/point groups')
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('normal_runs.csv',runs),('normal_aggregate.csv',full),('notification_runs.csv',micro),('notification_aggregate.csv',native),('cpu_stages.csv',stages),('request_timeline.csv',traces),('timeline_coverage.csv',coverage)]:write(output/name,data)
    print('URMA_NOTIFICATION_SUMMARY_PASSED output='+str(output));return runs,native,traces

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();summarize(a.root,a.output)
