"""Validate raw micro samples and compare independent runs without pooling nodes."""
import argparse
import json
import math
from pathlib import Path
import statistics
from micro_support import points
from phase_support import distribution
from summarize_normal_ab import write,read,rows


def summarize(roots,output,expected_repeats=3):
    paths=sorted({p.resolve() for root in roots for p in root.rglob('micro_summary.jsonl')})
    if not paths:raise ValueError('no micro results found')
    # A failed micro job with no summary must not disappear from aggregation.
    for root in roots:
        for setup in root.rglob('setup.json'):
            config=read(setup)['fingerprint']
            if 'threads' in config and not (setup.parent/'micro/micro_summary.jsonl').is_file():
                raise ValueError('incomplete micro job: '+str(setup.parent))
    runs=[];groups={};geometries=[]
    for path in paths:
        case=path.parent;node=case.parent
        setup=read(node/'setup.json');config=setup['fingerprint'];machine=read(node/'host.json')
        status=read(case/'status.json')
        if setup.get('error') or status['rc'] or not status['checks'] or not all(status['checks'].values()):
            raise ValueError('failed micro job: '+str(node))
        if config['normal_completion']!='all':raise ValueError('micro completion must be all')
        plan=read(case/'plan.json');expected=points(plan['geometries'])
        if plan['points']!=expected or len(expected)!=12:raise ValueError('invalid micro plan')
        summary=rows(path);raw=rows(case/'micro_samples.jsonl')
        layouts=rows(case/'micro_layout_verified.jsonl')
        labels={p['label'] for p in expected}
        if len(summary)!=12 or len(layouts)!=12 or {r['label'] for r in layouts}!=labels or {r['label'] for r in summary}!=labels or {r['label'] for r in raw}!=labels:
            raise ValueError('incomplete micro labels')
        identity=dict(host=machine['host'],node=node.name,boot_id=machine['boot_id'],job=config['job'])
        for g in plan['geometries']:geometries.append(dict(identity,**g))
        for point in expected:
            label=point['label'];g=point['geometry'];selected=[r for r in raw if r['label']==label]
            layout=next(r for r in layouts if r['label']==label)
            if (layout['dropped'] or layout['modulus']!=4096 or
                sum(r['count'] for r in layout['writes'])!=config['threads']*point['messages'] or
                any((r['bytes'],r['src_mod'],r['dst_mod'])!=(g['bytes'],g['src_mod'],g['dst_mod']) for r in layout['writes'])):
                raise ValueError('actual WRITE geometry mismatch: '+label)
            if len(selected)!=config['samples'] or [r['index'] for r in selected]!=list(range(config['samples'])) or [r['used'] for r in selected]!=[False]+[True]*(config['samples']-1):
                raise ValueError('incomplete micro raw samples: '+str(case)+' '+label)
            payload=config['threads']*point['messages']*g['bytes']
            ops=config['threads']*point['messages']*(2 if point['fadd'] else 1)
            for r in selected:
                if (not math.isfinite(r['us']) or r['us']<=0 or r['span_ns']/1000!=r['us'] or
                    r['write_bytes']!=payload or r['cqes']!=ops or r['retired']!=ops or
                    len(r['threads'])!=config['threads'] or
                    any(r[k]!=point[p] for k,p in [('window','window'),('messages','messages'),('fadd','fadd'),('gpu_observed','visible')]) or r['bytes']!=g['bytes']):
                    raise ValueError('invalid timing/geometry/completion counts: '+label)
            stats=distribution([r['us'] for r in selected if r['used']])
            saved=next(r for r in summary if r['label']==label)
            if not saved['validation'] or saved['samples']!=config['samples'] or saved['warmups']!=config['warmups'] or any(not math.isclose(saved[k],v,rel_tol=1e-9,abs_tol=1e-9) for k,v in stats.items()):
                raise ValueError('summary differs from raw samples: '+label)
            row=dict(identity,label=label,bytes=g['bytes'],src_mod=g['src_mod'],dst_mod=g['dst_mod'],
                window=point['window'],messages=point['messages'],fadd=point['fadd'],gpu_observed=point['visible'],
                threads=config['threads'],tx_GBps_at_mean=payload/stats['mean_us']/1000,**stats)
            for k in ('build','post','poll','observe'):
                row[k+'_thread_sum_us']=statistics.mean(r[k+'_ns_sum']/1000 for r in selected if r['used'])
            runs.append(row)
            key=(machine['host'],node.name,machine['boot_id'],config['kernel'],config['build_hash'],
                config['diagnostics_hash'],json.dumps(config['libraries'],sort_keys=True),config['torch'],
                config['threads'],config['warmups'],config['samples'],label,g['bytes'],g['src_mod'],g['dst_mod'])
            groups.setdefault(key,[]).append(row)
    aggregates=[];comparisons=[];lookup={}
    for key,values in sorted(groups.items()):
        if len(values)!=expected_repeats:raise ValueError('expected '+str(expected_repeats)+' independent micro runs; found '+str(len(values))+' '+str(key[:3])+': '+key[11])
        r=values[0];means=[v['mean_us'] for v in values]
        row={k:r[k] for k in ('host','node','boot_id','label','bytes','src_mod','dst_mod','window','messages','fadd','gpu_observed','threads')}
        row.update(kernel=key[3],build_hash=key[4],diagnostics_hash=key[5],libraries=key[6],torch=key[7],
            warmups=key[9],samples=key[10],independent_runs=len(values),median_run_mean_us=statistics.median(means),
            min_run_mean_us=min(means),max_run_mean_us=max(means),
            tx_GBps_at_median_run_mean=r['threads']*r['messages']*r['bytes']/statistics.median(means)/1000)
        aggregates.append(row);lookup[key[:11]+(r['label'],)+key[12:]]=row
    for key,a in lookup.items():
        label=a['label']
        if label.endswith('write_only'):other=label[:-len('write_only')]+'write_fadd';kind='fadd_vs_write_only'
        elif label.endswith('step_cqe_completed'):other=label[:-len('cqe_completed')]+'gpu_observed';kind='gpu_observed_round_vs_cqe_round'
        else:continue
        b=lookup.get(key[:11]+(other,)+key[12:])
        if b is None:raise ValueError('missing comparison point: '+other)
        comparisons.append(dict(host=a['host'],node=a['node'],boot_id=a['boot_id'],label=label,comparison=kind,
            bytes=a['bytes'],src_mod=a['src_mod'],dst_mod=a['dst_mod'],window=a['window'],threads=a['threads'],
            first_median_run_mean_us=a['median_run_mean_us'],second_median_run_mean_us=b['median_run_mean_us'],
            change_pct=(b['median_run_mean_us']/a['median_run_mean_us']-1)*100,
            difference_us=b['median_run_mean_us']-a['median_run_mean_us']))
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('micro_runs.csv',runs),('micro_aggregate.csv',aggregates),('micro_comparison.csv',comparisons),('micro_geometries.csv',geometries)]:write(output/name,data)
    print(f'MICRO_SUMMARY_PASSED jobs={len(paths)} output={output}')
    return runs,aggregates,comparisons


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('roots',nargs='+',type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--expected-repeats',type=int,default=3)
    a=p.parse_args()
    if a.expected_repeats<1:p.error('expected-repeats must be positive')
    summarize(a.roots,a.output,a.expected_repeats)
