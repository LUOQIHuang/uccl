"""Validate the four-job verbs Normal GPU/CPU stage experiment."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import common
from summarize import write
from timeline_support import timeline_rows

LABELS={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
SITES=common.HERE.parent/'urma_mode_parity/gpu_stage_sites.json'

def read(p): return json.loads(p.read_text())
def rows(p): return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

def summarize(root,output):
    sites=read(SITES);gpu=[];cpu=[];traces=[];coverage=[];base=[]
    seen=collections.defaultdict(set);kinds=collections.defaultdict(set)
    identities=collections.defaultdict(set);groups=collections.defaultdict(list)
    for path in sorted(root.glob('*/node*/setup.json')):
        node=path.parent;rank=int(node.name[-1]);setup=read(path);c=setup['fingerprint']
        host=read(node/'host.json');manifest=read(node/'module_manifest.json');runtime=read(node/'runtime.json')
        diag=c.get('gpu_stages',False);
        if diag and c.get('gpu_stage_mode','all')!='all':raise ValueError('partial recovery group is not a full stage experiment')
        flags=dict(manifest['flags']);compiled=flags.pop('UCCL_GPU_STAGE_TRACE','0')
        if setup['error'] or c['variant']!='current' or c['mode']!='normal' or not c.get('stage_suite') or not c['no_profile'] or c['profile_only'] or c['repeats']!=1:
            raise ValueError('wrong verbs stage configuration')
        if compiled!=('1' if diag else '0') or c['cpu_diagnostics']!=diag or c['timeline']!=diag or runtime['gpu_stage_version']!=(2 if diag else 0) or runtime['queue_backend']!='fifo':
            raise ValueError('instrumentation/build/backend mismatch')
        if flags.get('USE_URMA')!='0' or flags.get('USE_DMABUF')!='1' or any('/liburma.so' in p for p in runtime['loaded_libraries']):
            raise ValueError('expected pure verbs DMA-BUF build')
        if (c['warmups'],c['samples'])!=((5,5) if diag else (50,50)):
            raise ValueError('wrong sample counts')
        if read(node/'gpu_stage_sites.json')!=sites:raise ValueError('GPU site manifest changed')
        identities[rank].add(common.digest(dict(sources=manifest['sources'],flags=flags,boot=host['boot_id'],kernel=c['kernel'],libraries=c['libraries'],torch=c['torch'],benchmark=c['benchmark'],diagnostics=c['diagnostics'],gid=c['gid_index'],hca=c['hca'],gpu=c['gpu'],ifname=c['ifname'])))
        seen[node.parent.name].add(rank);kinds['diag' if diag else 'base'].add(node.parent.name)
        case=node/('cpu_diagnostic' if diag else 'r01_baseline')/'normal'
        status=read(case/'status.json')
        if status['rc'] or not status['checks'] or not all(status['checks'].values()):raise ValueError('failed case '+str(case))
        config=read(case/'config.json')
        if config['shape']!=dict(tokens=4096,hidden=7168,experts=256,topk=8):raise ValueError('wrong shape')
        common.validate_samples(case,'normal',c['samples'])
        raw=rows(case/'api_samples.jsonl');saved=rows(case/'sample_summary.jsonl')
        if len(saved)!=5 or {s['label'] for s in saved}!=LABELS:raise ValueError('missing summaries')
        for label in sorted(LABELS):
            stats=common.phase_support.distribution([r['us'] for r in raw if r['label']==label and r['used']])
            s=next(s for s in saved if s['label']==label)
            if s['profile'] or s['cpu_diagnostics']!=diag or s['warmups']!=c['warmups'] or s['measured']!=c['samples'] or any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('saved/raw summary mismatch')
            if not diag:
                base.append(dict(node=node.name,job=c['job'],label=label,**stats));groups[(node.name,label)].append(stats['mean_us'])
        if not diag:
            if any((case/n).exists() for n in ('gpu_stages.jsonl','verbs_phases.jsonl','verbs_timeline.jsonl')):raise ValueError('baseline instrumentation active')
            continue
        windows=rows(case/'gpu_stages.jsonl')
        if len(windows)!=25 or {(w['label'],w['index']) for w in windows}!={(l,i) for l in LABELS for i in range(5)}:raise ValueError('missing GPU windows')
        active=collections.Counter()
        for w in windows:
            if w['version']!=2 or w['clock_domain']!='per_thread_clock64_delta' or w['nominal_clock_khz']<=0:raise ValueError('invalid GPU clock metadata')
            for r in w['records']:
                stage=r['stage'];active[stage]+=r['calls']
                if str(stage) not in sites['additional'] or r['sample_lane'] not in (0,1) or r['calls']<=0 or not 0<=r['max_cycles']<=r['sum_cycles']:raise ValueError('invalid GPU metric')
                gpu.append(dict(node=node.name,label=w['label'],index=w['index'],used=w['index']>0,name=sites['additional'][str(stage)],ring=r['ring'],sample_lane=r['sample_lane'],block=r.get('block',''),warp=r.get('warp',''),calls=r['calls'],sum_cycles=r['sum_cycles'],max_cycles=r['max_cycles'],estimated_mean_us=r['sum_cycles']*1000/w['nominal_clock_khz']/r['calls'],estimated_max_us=r['max_cycles']*1000/w['nominal_clock_khz'],clock_note='nominal clock estimate; inclusive representative lane scopes'))
        if not all(active[k] for k in (40,41,42,46,48)):raise ValueError('missing GPU operator/kernel coverage')
        t,v=timeline_rows(case/'verbs_timeline.jsonl',node.name,LABELS,range(5));traces.extend(t);coverage.extend(v)
        windows=rows(case/'verbs_phases.jsonl')
        if len(windows)!=5 or {w['label'] for w in windows}!=LABELS:raise ValueError('missing CPU windows')
        for w in windows:
            totals=collections.defaultdict(collections.Counter)
            for record in w['records']:
                for name,m in record['metrics'].items():
                    if name in ('verbs_post_errors','verbs_completion_errors','verbs_tracking_overflow') and m['calls']:raise ValueError('provider/tracking errors')
                    for k in ('calls','ns','items'):totals[name][k]+=m[k]
            for name,m in totals.items():cpu.append(dict(node=node.name,label=w['label'],metric=name,calls=m['calls'],items=m['items'],inclusive_thread_sum_us=m['ns']/1000))
    if len(seen)!=4 or any(v!={0,1} for v in seen.values()) or len(kinds['base'])!=3 or len(kinds['diag'])!=1 or any(len(identities[r])!=1 for r in (0,1)):raise ValueError('need stable source/boot and two builds, three base jobs plus one trace job, both nodes')
    aggregate=[]
    for (node,label),values in sorted(groups.items()):
        if len(values)!=3:raise ValueError('missing base repetitions')
        aggregate.append(dict(node=node,label=label,median_run_mean_us=statistics.median(values),min_run_mean_us=min(values),max_run_mean_us=max(values)))
    output.mkdir(parents=True,exist_ok=True)
    for name,data in [('normal_runs.csv',base),('normal_aggregate.csv',aggregate),('gpu_stage_scopes.csv',gpu),('cpu_scopes.csv',cpu),('request_timeline.csv',traces),('timeline_coverage.csv',coverage)]:write(output/name,data)
    print('VERBS_STAGE_SUMMARY_PASSED output='+str(output))
    return gpu,traces

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.output)
