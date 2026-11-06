"""Validate split suite raw results; keep roles, affinities and repetitions separate."""
import argparse
import json
import math
from pathlib import Path
import statistics
from phase_support import distribution
from split_support import points,roles
from summarize_normal_ab import read,rows,write


def summarize(roots,output):
    setups=sorted({p.resolve() for root in roots for p in root.rglob('setup.json')})
    runs=[];groups={}
    for setup_path in setups:
        setup=read(setup_path);c=setup['fingerprint']
        if c.get('suite')!='split':continue
        node=setup_path.parent;case=node/'micro';host=read(node/'host.json');request=read(case/'request.json')
        status=read(case/'status.json')
        if setup.get('error') or status['rc'] or not status['checks'] or not all(status['checks'].values()):raise ValueError('failed job: '+str(node))
        if c['normal_completion']!='all':raise ValueError('split requires all completions')
        plan=read(case/'plan.json');expected=points(plan['geometries'],c['threads'])
        if plan['points']!=expected:raise ValueError('split plan mismatch')
        raw=rows(case/'micro_samples.jsonl');saved=rows(case/'micro_summary.jsonl');layouts=rows(case/'micro_layout_verified.jsonl')
        labels={p['label'] for p in expected}
        if {r['label'] for r in raw}!=labels or len(saved)!=len(labels) or {r['label'] for r in saved}!=labels or len(layouts)!=len(labels) or {r['label'] for r in layouts}!=labels:
            raise ValueError('incomplete split points: '+str(case))
        placements=read(case/'affinity_pair.json');placement=read(node/'affinity.json')
        if placements[str(request['rank'])]!=placement or request['placement']!=placement:raise ValueError('affinity provenance mismatch')
        for p in expected:
            g=p['geometry'];send,receive=roles(p,request['rank']);label=p['label']
            selected=[r for r in raw if r['label']==label];n=c['samples']
            if len(selected)!=n or [r['index'] for r in selected]!=list(range(n)) or [r['used'] for r in selected]!=[False]+[True]*(n-1):raise ValueError('raw sample boundary mismatch')
            count=c['threads']*p['messages'] if send else 0;ops=count*(2 if p['fadd'] else 1)
            for r in selected:
                if (not 0<r['us']<float('inf') or r['span_ns']/1000!=r['us'] or r['write_bytes']!=count*g['bytes'] or
                    r['cqes']!=ops or r['retired']!=ops or r['send']!=send or r['receive']!=receive or
                    any(r[k]!=p[k] for k in ('local','direction','observation','window','messages','fadd')) or r['bytes']!=g['bytes']):
                    raise ValueError('invalid split sample: '+label)
                if p['local'] and not 0<r['gpu_us']<float('inf'):raise ValueError('invalid GPU event sample')
            layout=next(r for r in layouts if r['label']==label)
            if layout['dropped'] or layout['modulus']!=4096 or sum(r['count'] for r in layout['writes'])!=count or any((r['bytes'],r['src_mod'],r['dst_mod'])!=(g['bytes'],g['src_mod'],g['dst_mod']) for r in layout['writes']):raise ValueError('actual WRITE geometry mismatch')
            stats=distribution([r['us'] for r in selected if r['used']]);saved_row=next(r for r in saved if r['label']==label)
            if not saved_row['validation'] or saved_row['warmups']!=c['warmups'] or saved_row['samples']!=n or any(not math.isclose(saved_row[k],v,rel_tol=1e-9,abs_tol=1e-9) for k,v in stats.items()):raise ValueError('summary/raw mismatch')
            metric='local_gpu_scan' if p['local'] else ('inactive_receiver' if not send else 'network_host_span')
            gpu=distribution([r['gpu_us'] for r in selected if r['used']]) if p['local'] else None
            if gpu and any(not math.isclose(saved_row['gpu_scan'][k],v,rel_tol=1e-9,abs_tol=1e-9) for k,v in gpu.items()):raise ValueError('GPU event summary/raw mismatch')
            row=dict(host=host['host'],node=node.name,job=c['job'],label=label,threads=c['threads'],bytes=g['bytes'],src_mod=g['src_mod'],dst_mod=g['dst_mod'],direction=p['direction'],observation=p['observation'],send=send,metric=metric,tx_GBps=count*g['bytes']/stats['mean_us']/1000 if send else '',gpu_mean_us=gpu['mean_us'] if gpu else '',**stats)
            runs.append(row)
            key=(host['host'],node.name,host['boot_id'],c['kernel'],c['build_hash'],c['diagnostics_hash'],json.dumps(c['libraries'],sort_keys=True),c['torch'],c['threads'],c['warmups'],n,json.dumps(placements,sort_keys=True),label,g['bytes'],g['src_mod'],g['dst_mod'])
            groups.setdefault(key,[]).append(row)
    if not groups:raise ValueError('no split runs')
    aggregate=[]
    for key,values in sorted(groups.items()):
        if len(values)!=3:raise ValueError('expected three independent repeats: '+str(key[:3])+key[12])
        r=values[0];v=[r['mean_us'] for r in values];row={k:r[k] for k in ('host','node','label','threads','bytes','src_mod','dst_mod','direction','observation','send','metric')}
        row.update(boot_id=key[2],kernel=key[3],build_hash=key[4],diagnostics_hash=key[5],libraries=key[6],torch=key[7],warmups=key[9],samples=key[10],affinities=key[11],independent_runs=3,median_run_mean_us=statistics.median(v),min_run_mean_us=min(v),max_run_mean_us=max(v),gpu_median_run_mean_us=statistics.median(r['gpu_mean_us'] for r in values) if r['metric']=='local_gpu_scan' else '',tx_GBps=128*r['bytes']/statistics.median(v)/1000 if r['send'] and r['observation']=='cqe' and '_write_' in r['label'] else '')
        aggregate.append(row)
    # Requiring both thread configurations prevents a failed late job from vanishing.
    for host,node in {(r['host'],r['node']) for r in aggregate}:
        entries=[r for r in aggregate if r['host']==host and r['node']==node]
        if {r['threads'] for r in entries}!={1,4} or len(entries)!=20:raise ValueError('missing split thread configuration/geometry')
    output.mkdir(parents=True,exist_ok=True);write(output/'split_runs.csv',runs);write(output/'split_aggregate.csv',aggregate)
    print('SPLIT_SUMMARY_PASSED nodes='+str(len({r['node'] for r in runs}))+' output='+str(output))
    return runs,aggregate


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('roots',nargs='+',type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();summarize(a.roots,a.output)
