"""Validate one recovery step on both nodes, without pooling it into a baseline."""
import argparse
import json
import math
from pathlib import Path

LABELS={'layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'}
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

def validate(root,node_rank=None):
    identities=[]
    for rank in ((0,1) if node_rank is None else (node_rank,)):
        node=root/f'node{rank}';setup=read(node/'setup.json');c=setup['fingerprint']
        if setup['error']:raise ValueError('setup failed')
        verbs=c.get('variant')=='current';case=node/('cpu_diagnostic/normal' if verbs else 'normal')
        status=read(case/'status.json')
        if status['rc'] or not status['checks'] or not all(status['checks'].values()):raise ValueError('case failed/incomplete')
        n=c['samples'];trace=c.get('gpu_stages',False);mode=c.get('gpu_stage_mode','all') if trace else 'cpu'
        tokens=c['normal_tokens'] if verbs else c['shape'][0]
        manifest=read(node/('module_manifest.json' if verbs else 'build_manifest.json'))
        if c['mode']!='normal' or manifest['flags']['UCCL_GPU_STAGE_TRACE']!=str(int(trace)):raise ValueError('build/config mismatch')
        identities.append((verbs,tokens,mode,n,c['warmups'],c['kernel'],manifest['sources'],manifest['flags']))
        if n!=3 or c['warmups']!=1 or tokens not in (128,4096):raise ValueError('not a short recovery step')
        samples=rows(case/'api_samples.jsonl')
        if len(samples)!=5*n or {(r['label'],r['index']) for r in samples}!={(l,i) for l in LABELS for i in range(n)}:raise ValueError('missing API samples')
        if any(not math.isfinite(r['us']) or r['us']<=0 or r['used']!=(r['index']>0) for r in samples):raise ValueError('invalid API samples')
        windows=rows(case/('verbs_timeline.jsonl' if verbs else 'urma_timeline.jsonl'))
        expected={(l,i) for l in LABELS for i in range(n)} if trace else {(l,0) for l in LABELS if l.startswith('dispatch_')}
        if len(windows)!=len(expected) or {(w['label'],w['index']) for w in windows}!=expected:raise ValueError('missing CPU timeline windows')
        if any(w['dropped'] for w in windows):raise ValueError('truncated CPU timeline')
        phases=rows(case/('verbs_phases.jsonl' if verbs else 'phases.jsonl'))
        if len(phases)!=5 or {w['label'] for w in phases}!=LABELS:raise ValueError('missing CPU phases')
        if not trace:
            if read(case/'runtime.json')['gpu_stage_version']!=0:raise ValueError('CPU fallback used trace build')
            if (case/'gpu_stages.jsonl').exists():raise ValueError('CPU fallback unexpectedly collected GPU scopes')
            continue
        runtime=read(case/'runtime.json')
        if runtime['gpu_stage_version']!=2:raise ValueError('old GPU trace module')
        windows=rows(case/'gpu_stages.jsonl')
        if len(windows)!=5*n or {(w['label'],w['index']) for w in windows}!={(l,i) for l in LABELS for i in range(n)}:raise ValueError('missing GPU windows')
        stages=set()
        for w in windows:
            if w['version']!=2 or w['storage']!='device_private_slots' or w['mode']!=mode:raise ValueError('GPU implementation/mode mismatch')
            if mode=='bypass' and w['records']:raise ValueError('bypass accessed GPU metrics')
            slots=set()
            for r in w['records']:
                key=(r['block'],r['warp'],r['sample_lane'],r['stage'])
                if key in slots:raise ValueError('duplicate private slot')
                slots.add(key);stages.add(r['stage'])
                if not 0<=r['block']<128 or not 0<=r['warp']<32 or r['sample_lane'] not in (0,1) or r['calls']<=0 or not 0<=r['max_cycles']<=r['sum_cycles']:raise ValueError('invalid metric bounds')
                if mode=='access' and r['sum_cycles']:raise ValueError('access mode read clock')
        required={'cpu':set(),'bypass':set(),'access':{41,42,46,48},'clock':{46},'kernels':{46,48},'waits':set(),'operators':{40,41,42},'all':{40,41,42,46,48}}[mode]
        if not required<=stages or (mode=='waits' and not stages):raise ValueError('missing selected GPU coverage')
        allowed=set(range(24))|set(range(40,49))
        if mode=='clock':allowed={46}
        if mode=='kernels':allowed=set(range(45,49))
        if mode=='waits':allowed=set(range(24))
        if mode=='operators':allowed=set(range(40,45))
        if not stages<=allowed:raise ValueError('unexpected GPU group')
    if len(identities)==2 and identities[0]!=identities[1]:raise ValueError('peer recovery configuration mismatch')
    print(('GPU_STAGE_RECOVERY_VALIDATED ' if node_rank is None else 'GPU_STAGE_RECOVERY_NODE_VALIDATED ')+str(root))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--node-rank',type=int,choices=(0,1))
    a=p.parse_args();validate(a.root,a.node_rank)
