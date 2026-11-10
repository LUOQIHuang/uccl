"""Five-job matched notification + light CPU campaign, with historical URMA context."""
import argparse
import collections
import json
import math
from pathlib import Path
import statistics
import common
from notify_support import matched_geometries,points
from summarize import write
from summarize_notifications import summarize as notifications,read,rows,passed
from summarize_fast_normal import summarize as normal

def urma_micro(root):
    groups=collections.defaultdict(list);identities=collections.defaultdict(set);repeats=collections.defaultdict(set);contexts=[]
    for path in sorted(root.glob('*/node*/setup.json')):
        setup=read(path);c=setup['fingerprint']
        if c.get('suite')!='notify':continue
        node=path.parent;rank=node.name;case=node/'micro';passed(case)
        if setup['error'] or (c['threads'],c['warmups'],c['samples'],c['normal_completion'])!=(4,20,50,'all'):raise ValueError('unexpected historical URMA notification config')
        geom=matched_geometries(case/'plan.json');plan=points(geom,c['repeat'])
        raw=rows(case/'micro_samples.jsonl');saved=rows(case/'micro_summary.jsonl');native=read(case/'urma_path.json')
        if len(raw)!=400 or len(saved)!=8 or native.get('channels')!=8 or native.get('control_memory')!='gpu' or native.get('atomic_return_memory')!='host' or native.get('normal_completion')!='all':raise ValueError('historical URMA workload/native path mismatch')
        if (native.get('write_order'),native.get('atomic_order'),native.get('atomic_fence'))!=('relax','strong','per_request'):raise ValueError('historical URMA ordering changed')
        repeats[rank].add(c['repeat']);host=read(node/'host.json')
        identities[rank].add(common.digest(dict(build=c['build_hash'],kernel=c['kernel'],torch=c['torch'],libraries=c['libraries'],boot=host['boot_id'],placement=read(case/'affinity_pair.json'),native=native)))
        contexts.append(dict(node=rank,host=host['host'],kernel=c['kernel'],torch=c['torch'],build=c['build_hash'],native=native,placement=read(case/'affinity_pair.json')))
        for p in plan:
            g=p['geometry'];rr=[r for r in raw if r['label']==p['label']]
            if len(rr)!=50 or [r['index'] for r in rr]!=list(range(50)) or any(r['used']!=(r['index']>0) for r in rr):raise ValueError('URMA sampling boundary mismatch')
            for r in rr:
                if r['bytes']!=g['bytes'] or r['window']!=1 or r['messages']!=8 or r['operation']!=p['operation'] or r['gpu_observed']!=p['visible'] or r['local'] or r['direction']!='bidir' or not r['send'] or not r['receive']:raise ValueError('URMA workload mismatch')
                count=64 if p['operation']==2 else 32
                if r['cqes']!=count or r['retired']!=count or r['write_bytes']!=(0 if p['operation']==1 else 32*g['bytes']) or not math.isfinite(r['us']) or r['us']<=0 or r['us']!=r['span_ns']/1000:raise ValueError('URMA sample counts/timing mismatch')
            stats=common.phase_support.distribution([r['us'] for r in rr if r['used']]);s=next(s for s in saved if s['label']==p['label'])
            if not s['validation'] or (s['src_mod'],s['dst_mod'])!=(g['src_mod'],g['dst_mod']) or any(not math.isclose(s[k],v,rel_tol=1e-9) for k,v in stats.items()):raise ValueError('URMA saved/raw mismatch')
            groups[(rank,p['label'],g['bytes'],g['src_mod'],g['dst_mod'])].append(stats['mean_us'])
    if set(repeats)!={'node0','node1'} or any(v!={1,2,3} for v in repeats.values()) or any(len(v)!=1 for v in identities.values()) or len(groups)!=16 or any(len(v)!=3 for v in groups.values()):raise ValueError('need stable three historical URMA repeats, both nodes')
    return groups,contexts

def summarize(root,job,output,part='all',urma=None):
    expected=set()
    if part!='normal':expected.update(job+'_notify_r0'+str(i) for i in (1,2,3))
    if part!='notify':expected.update((job+'_baseline',job+'_cpu'))
    observed=collections.defaultdict(set);identity=collections.defaultdict(set)
    for p in root.glob('*/node*/setup.json'):
        if p.parent.parent.name not in expected:continue
        s=read(p);c=s['fingerprint'];node=p.parent
        if s['error']:raise ValueError('failed setup')
        if c.get('gpu_stages') or c.get('timeline') or c.get('capture_layout'):raise ValueError('unexpected heavy tracing')
        observed[node.parent.name].add(node.name)
        identity[node.name].add(common.digest(dict(identity=c['identity'],module_sha=read(node/'module_manifest.json')['module_sha'],kernel=c['kernel'],torch=c['torch'],libraries=c['libraries'],gid=c['gid_index'],hca=c['hca'],boot=read(node/'host.json')['boot_id'],diagnostics=c['diagnostics'])))
    if set(observed)!=expected or any(v!={'node0','node1'} for v in observed.values()) or any(len(v)!=1 for v in identity.values()):raise ValueError('need matched jobs on both nodes, stable build/kernel/boot')
    output.mkdir(parents=True,exist_ok=True);agg=[]
    if part!='normal':_,agg,_=notifications(root,output/'notifications',include_timeline=False)
    if part!='notify':normal(root,job,output/'normal')
    if urma and part!='normal':
        historical,contexts=urma_micro(urma);comparison=[];native=[]
        for r in agg:
            plan=read(root/(job+'_notify_r01')/r['node']/'micro/plan.json')
            p=next(p for p in plan['points'] if p['label']==r['label']);g=p['geometry']
            key=(r['node'],r['label'],g['bytes'],g['src_mod'],g['dst_mod'])
            if key not in historical:raise ValueError('URMA/verbs geometry mismatch')
            u=statistics.median(historical[key]);v=r['median_run_mean_us']
            comparison.append(dict(node=r['node'],label=r['label'],bytes=r['bytes'],verbs_us=v,urma_us=u,urma_over_verbs=u/v,urma_slower_pct=(u/v-1)*100,verbs_min_run_us=r['min_run_mean_us'],verbs_max_run_us=r['max_run_mean_us'],urma_min_run_us=min(historical[key]),urma_max_run_us=max(historical[key])))
        for node in ('node0','node1'):
            n=root/(job+'_notify_r01')/node;native.append(dict(node=node,host=read(n/'host.json')['host'],config=read(n/'setup.json')['fingerprint'],native=read(n/'micro/verbs_path.json'),placement=read(n/'micro/affinity_pair.json')))
        for v in native:
            if any(c['host']!=v['host'] or c['torch']!=v['config']['torch'] for c in contexts if c['node']==v['node']):raise ValueError('historical host/Torch mismatch')
        for r in comparison:
            v=next(v for v in native if v['node']==r['node']);u=next(u for u in contexts if u['node']==r['node'])
            r['placement_matches']=v['placement']==u['placement']
        write(output/'notification_comparison.csv',comparison)
        (output/'comparison_context.json').write_text(json.dumps(dict(verbs=native,urma=contexts,note='Historical different kernel/build/driver environments. URMA WRITE relax/FADD strong per-request fence; verbs FADD fence=False. Matched payload/protocol, not identical ordering flags. Mode deltas overlap and are not additive critical-path costs.'),indent=2))
    print('NORMAL_FOCUS_SUMMARY_PASSED '+str(output))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--job',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--part',choices=('all','notify','normal'),default='all');p.add_argument('--urma-results',type=Path)
    a=p.parse_args();summarize(a.root,a.job,a.output,a.part,a.urma_results)
