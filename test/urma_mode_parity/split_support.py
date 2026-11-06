"""Normal micro v2: explicit traffic roles and local CPU placement."""
import os
from pathlib import Path


def cpu_list(text):
    result=[]
    for part in text.strip().split(','):
        if '-' in part:
            a,b=map(int,part.split('-'));result.extend(range(a,b+1))
        else:result.append(int(part))
    return result


def affinity(args):
    allowed=set(os.sched_getaffinity(0));pci=Path('/sys/bus/pci/devices')/args.gpu_pci
    try:gpu_node=int((pci/'numa_node').read_text())
    except (OSError,ValueError):gpu_node=-1
    node_sets={p.parent.name:set(cpu_list(p.read_text())) for p in Path('/sys/devices/system/node').glob('node*/cpulist')}
    reason='explicit'
    if args.micro_cpus=='auto':
        preferred=sorted(allowed & node_sets.get('node'+str(gpu_node),set()))
        reason='GPU NUMA node'
        if len(preferred)<5:
            preferred=next((sorted(v&allowed) for k,v in sorted(node_sets.items()) if len(v&allowed)>=5),[])
            reason='fallback first eligible NUMA node; GPU NUMA unknown/unavailable'
        if len(preferred)<5:raise ValueError('split requires >=5 available CPUs in one NUMA node; choose --micro-cpus explicitly')
        workers=preferred[:4]
    else:workers=cpu_list(args.micro_cpus)
    if len(workers)!=4 or len(set(workers))!=4 or not set(workers)<=allowed or max(workers)>=1024:
        raise ValueError('invalid/unavailable split CPU IDs (must be <1024)')
    local=next((v for v in node_sets.values() if set(workers)<=v),set())
    if not local:raise ValueError('worker CPUs must belong to one NUMA node')
    spare=sorted((allowed&local)-set(workers))
    if not spare:raise ValueError('need a fifth CPU on same node for Python/controller')
    return dict(workers=workers,controller=spare[0],gpu_numa_node=gpu_node,reason=reason,
        allowed=sorted(allowed),worker_numa_nodes=[k for k,v in node_sets.items() if set(workers)<=v])


def points(geometries,threads):
    result=[]
    for g in geometries:
        for direction in ('bidir','0to1','1to0'):
            result.append(dict(label=g['label']+'_write_'+direction,geometry=g,direction=direction,
                window=4,messages=128//threads,fadd=False,observation='cqe',local=False))
        if threads==4:
            for obs in ('cqe','flag','payload'):
                result.append(dict(label=g['label']+'_observe_'+obs,geometry=g,direction='bidir',
                    window=1,messages=8,fadd=True,observation=obs,local=False))
            result.append(dict(label=g['label']+'_local_scan',geometry=g,direction='none',
                window=1,messages=0,fadd=False,observation='scan',local=True))
    return result


def roles(point,rank):
    d=point['direction']
    return d=='bidir' or d==str(rank)+'to'+str(1-rank),d=='bidir' or d==str(1-rank)+'to'+str(rank)
