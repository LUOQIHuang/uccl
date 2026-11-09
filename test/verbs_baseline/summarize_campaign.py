"""Strict two-node Normal campaign validation; baseline, diagnostics and micro remain separate."""
import argparse
import json
from pathlib import Path
import math
import statistics
import common
import summarize as baseline
from split_support import points,roles


def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
def status(case):
    s=read(case/'status.json')
    if s['rc'] or not s['checks'] or not all(s['checks'].values()):raise ValueError('failed/incomplete case '+str(case))


def summarize(root,output):
    setups=sorted(root.glob('*/node*/setup.json'));micro=[];stages=[];groups={};base_roots=set();seen={};provenance=set()
    for path in setups:
        node=path.parent;setup=read(path)
        if setup['error']:raise ValueError('failed setup '+str(node))
        c=setup['fingerprint'];host=read(node/'host.json');rank=int(node.name[-1]);kind='micro' if c.get('suite')=='verbs_write_micro' else 'cpu' if c.get('cpu_diagnostics') else 'baseline'
        seen.setdefault((node.parent.name,kind),set()).add(rank)
        identity=c['identity'];provenance.add((identity['sources_hash'],json.dumps(identity['flags'],sort_keys=True),c['kernel'],c['torch'],host['boot_id'],c['gid_index'],json.dumps(c['libraries'],sort_keys=True),rank))
        if kind=='baseline':
            if c['mode']!='normal' or c['variant']!='current' or c['repeats']!=3 or c['samples']!=50 or c['warmups']!=50 or c['no_profile'] or c['profile_only']:raise ValueError('need three current Normal baselines and separate GPU profile')
            base_roots.add(node.parent);continue
        if kind=='cpu':
            case=node/'cpu_diagnostic/normal';status(case);common.validate_samples(case,'normal',c['samples'])
            windows=rows(case/'verbs_phases.jsonl');layouts=rows(case/'write_layout.jsonl')
            labels={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
            if {w['label'] for w in windows}!=labels or len(windows)!=5 or {w['label'] for w in layouts}!=labels:raise ValueError('missing CPU diagnostic labels')
            total_write=total_fadd=total_samples=0
            for w in windows:
                metrics={}
                for record in w['records']:
                    for name,m in record['metrics'].items():
                        value=metrics.setdefault(name,dict(ns=0,calls=0,items=0))
                        for key in value:value[key]+=m[key]
                for error in ('verbs_post_errors','verbs_completion_errors','verbs_tracking_overflow'):
                    if metrics.get(error,{}).get('calls',0):raise ValueError('verbs diagnostic error '+error)
                layout=next(x for x in layouts if x['label']==w['label'])
                if layout['dropped']:raise ValueError('truncated WR shapes')
                total_write+=metrics.get('write_requests',{}).get('calls',0);total_fadd+=metrics.get('fadd_requests',{}).get('calls',0)
                total_samples+=metrics.get('submit_to_observed_cqe_sampled',{}).get('calls',0)
                for name,m in metrics.items():stages.append(dict(host=host['host'],node=node.name,label=w['label'],metric=name,iterations=w['iterations'],calls_per_iteration=m['calls']/w['iterations'],items_per_iteration=m['items']/w['iterations'],thread_sum_us_per_iteration=m['ns']/1000/w['iterations']))
            if not total_write or not total_fadd or not total_samples:raise ValueError('expected actual WRITE/FADD and matched completion timing samples')
            continue
        case=node/'micro';status(case);req=read(case/'request.json');plan=read(case/'plan.json')
        expected=[p for p in points(plan['geometries'],c['threads']) if '_write_' in p['label']]
        if len(expected)!=6 or plan['points']!=expected or c['samples']!=50 or c['warmups']!=20:raise ValueError('micro plan/sampling mismatch')
        raw=rows(case/'micro_samples.jsonl');saved=rows(case/'micro_summary.jsonl')
        placement=read(node/'affinity.json');pair=read(case/'affinity_pair.json')
        if pair[str(rank)]!=placement or req['placement']!=placement:raise ValueError('affinity provenance mismatch')
        if len(saved)!=6 or len(raw)!=300 or {r['label'] for r in raw}!={p['label'] for p in expected}:raise ValueError('missing micro samples')
        for p in expected:
            g=p['geometry'];label=p['label'];send,receive=roles(p,rank);rr=[r for r in raw if r['label']==label]
            if [r['index'] for r in rr]!=list(range(50)) or [r['used'] for r in rr]!=[False]+[True]*49:raise ValueError('micro sample boundary')
            for r in rr:
                if (not 0<r['us']<float('inf') or r['span_ns']/1000!=r['us'] or r['cqes']!=(128 if send else 0) or r['retired']!=r['cqes'] or r['write_bytes']!=(128*g['bytes'] if send else 0) or r['send']!=send or r['receive']!=receive or r['direction']!=p['direction'] or any(r[k]!=g[k] for k in ('bytes','src_mod','dst_mod'))):raise ValueError('micro completion/shape mismatch')
            stats=common.phase_support.distribution([r['us'] for r in rr if r['used']]);s=next(x for x in saved if x['label']==label)
            if not s['validation'] or any(not math.isclose(s[k],v,rel_tol=1e-9,abs_tol=1e-9) for k,v in stats.items()):raise ValueError('micro summary/raw mismatch')
            row=dict(host=host['host'],node=node.name,job=c['job'],threads=c['threads'],label=label,direction=p['direction'],send=send,bytes=g['bytes'],src_mod=g['src_mod'],dst_mod=g['dst_mod'],tx_GBps=128*g['bytes']/stats['mean_us']/1000 if send else '',**stats)
            micro.append(row);key=(host['host'],node.name,c['threads'],label,g['bytes'],g['src_mod'],g['dst_mod'],json.dumps(pair,sort_keys=True),c['gid_index'],json.dumps(c['libraries'],sort_keys=True))
            groups.setdefault(key,[]).append(row)
    if not seen or any(ranks!={0,1} for ranks in seen.values()):raise ValueError('need both nodes for every campaign job')
    if len(seen)!=8 or {kind for job,kind in seen}!={'baseline','cpu','micro'}:raise ValueError('need baseline + CPU diagnostic + six micro jobs')
    for rank in (0,1):
        if len({p[:-1] for p in provenance if p[-1]==rank})!=1:raise ValueError('source/build/kernel/boot changed during campaign')
    aggregates=[]
    for key,rr in sorted(groups.items()):
        if len(rr)!=3:raise ValueError('need three micro repeats '+str(key[:4]))
        r=rr[0];mean=statistics.median(x['mean_us'] for x in rr)
        aggregates.append(dict(host=r['host'],node=r['node'],threads=r['threads'],label=r['label'],send=r['send'],bytes=r['bytes'],src_mod=r['src_mod'],dst_mod=r['dst_mod'],gid_index=key[8],independent_runs=3,median_run_mean_us=mean,min_run_mean_us=min(x['mean_us'] for x in rr),max_run_mean_us=max(x['mean_us'] for x in rr),tx_GBps=128*r['bytes']/mean/1000 if r['send'] else '',affinities=key[7]))
    if len(aggregates)!=24:raise ValueError('missing micro host/thread points')
    output.mkdir(parents=True,exist_ok=True);baseline.summarize(sorted(base_roots),output/'baseline')
    for name,data in [('verbs_cpu_stages.csv',stages),('verbs_micro_runs.csv',micro),('verbs_micro_aggregate.csv',aggregates)]:baseline.write(output/name,data)
    print('VERBS_CAMPAIGN_SUMMARY_PASSED output='+str(output));return micro,aggregates,stages


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();summarize(a.root,a.output)
