"""Full Normal GPU site coverage + CPU chains; timings across domains never summed."""
import argparse
import collections
import json
from pathlib import Path
import statistics
from summarize_normal_ab import read,rows,write,LABELS
from summarize_normal_notify import passed,api_samples
from timeline_support import timeline_rows

def summarize(root,output):
    sites=read(Path(__file__).with_name('gpu_stage_sites.json'));names=sites['additional'];gpu=[];cpu=[];timeline=[];coverage=[];base=[];groups=collections.defaultdict(list);seen=collections.defaultdict(set);kinds=collections.defaultdict(set);identities=collections.defaultdict(set)
    for path in sorted(root.glob('*/node*/setup.json')):
        node=path.parent;rank=int(node.name[-1]);setup=read(path);c=setup['fingerprint'];host=read(node/'host.json');manifest=read(node/'build_manifest.json');diag=c.get('gpu_stages',False)
        if setup['error'] or c['mode']!='normal' or c['backend']!='urma' or not c['api_only'] or c['normal_completion']!='all' or c['dispatch_trial']:raise ValueError('wrong stage setup')
        flags=dict(manifest['flags']);compiled=flags.pop('UCCL_GPU_STAGE_TRACE','0')
        if compiled!=('1' if diag else '0') or c['stage_profile']!=diag or c.get('timeline',False)!=diag:raise ValueError('instrumentation/build mismatch')
        identities[rank].add(json.dumps(dict(sources=manifest['sources'],sdk=manifest['sdk'],flags=flags,boot=host['boot_id'],kernel=c['kernel'],libraries=c['libraries'],torch=c['torch']),sort_keys=True))
        seen[node.parent.name].add(rank);kinds['diag' if diag else 'base'].add(node.parent.name)
        case=node/'normal';passed(case);stats=api_samples(case,c)
        if (c['warmups'],c['samples'])!=((5,5) if diag else (50,50)):raise ValueError('wrong sample counts')
        if not diag:
            for w in rows(case/'phases.jsonl'):
                if any(m['calls'] for r in w['records'] for m in r['metrics'].values()):raise ValueError('baseline counters active')
            for s in stats:base.append(dict(node=node.name,job=c['job'],**s));groups[(node.name,s['label'])].append(s['mean_us'])
            continue
        if read(node/'gpu_stage_sites.json')!=sites:raise ValueError('GPU site manifest changed')
        windows=rows(case/'gpu_stages.jsonl')
        if len(windows)!=25 or {(w['label'],w['index']) for w in windows}!={(l,i) for l in LABELS for i in range(5)}:raise ValueError('missing GPU windows')
        active=collections.Counter()
        for w in windows:
            if w['clock_domain']!='per_thread_clock64_delta' or w['nominal_clock_khz']<=0:raise ValueError('invalid clock metadata')
            for r in w['records']:
                stage=r['stage'];active[stage]+=r['calls']
                if str(stage) not in names or r['sample_lane'] not in (0,1) or r['calls']<=0 or not 0<=r['max_cycles']<=r['sum_cycles']:raise ValueError('invalid GPU metric')
                gpu.append(dict(node=node.name,label=w['label'],index=w['index'],used=w['index']>0,name=names[str(stage)],ring=r['ring'],sample_lane=r['sample_lane'],calls=r['calls'],sum_cycles=r['sum_cycles'],max_cycles=r['max_cycles'],estimated_max_us=r['max_cycles']*1000/w['nominal_clock_khz'],estimated_mean_us=r['sum_cycles']*1000/w['nominal_clock_khz']/r['calls'],clock_note='nominal clock estimate; inclusive representative lane scopes'))
        if not all(active[k] for k in (40,41,42,46,48)):raise ValueError('missing GPU operator/kernel coverage')
        t,v=timeline_rows(case/'urma_timeline.jsonl',node.name,LABELS,range(5));timeline.extend(t);coverage.extend(v)
        publications=sum(e['stage']=='gpu_publish_clock' for w in rows(case/'urma_timeline.jsonl') for e in w['events'])
        if not publications:raise ValueError('no GPU publication/CPU collection correlation')
        for w in rows(case/'phases.jsonl'):
            totals=collections.defaultdict(collections.Counter)
            for record in w['records']:
                for name,m in record['metrics'].items():
                    for k in ('calls','ns','items'):totals[name][k]+=m[k]
            for name,m in totals.items():cpu.append(dict(node=node.name,label=w['label'],metric=name,calls=m['calls'],items=m['items'],inclusive_thread_sum_us=m['ns']/1000))
    if len(seen)!=4 or any(v!={0,1} for v in seen.values()) or len(kinds['base'])!=3 or len(kinds['diag'])!=1 or any(len(identities[r])!=1 for r in (0,1)):raise ValueError('need matched source/SDK/boot, three base jobs plus one trace job, both nodes')
    aggregate=[]
    for (node,label),values in sorted(groups.items()):
        if len(values)!=3:raise ValueError('missing base repetitions')
        aggregate.append(dict(node=node,label=label,median_run_mean_us=statistics.median(values),min_run_mean_us=min(values),max_run_mean_us=max(values)))
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('normal_runs.csv',base),('normal_aggregate.csv',aggregate),('gpu_stage_scopes.csv',gpu),('cpu_scopes.csv',cpu),('request_timeline.csv',timeline),('timeline_coverage.csv',coverage)]:write(output/name,data)
    print('URMA_STAGE_SUMMARY_PASSED output='+str(output));return gpu,timeline

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args();summarize(a.root,a.output)
