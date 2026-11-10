"""Collect one node, including failures, console and available build logs."""
import argparse
from pathlib import Path
import shutil

def collect(root,rank,builds,destination):
    pairs=[]
    for part in (f'node{rank}',f'console/node{rank}'):
        for p in (root/part).rglob('*'):
            if p.is_file() and p.suffix in ('.json','.jsonl','.log','.txt','.py','.csv') and not any(x in p.parts for x in ('cache','tmpdir','__pycache__')):
                pairs.append((p,destination/p.relative_to(root)))
    for kind,build in builds.items():
        if not build:continue
        for name in ('build.log','host_tests.log','manifest.json','request.json','build_request.json'):
            p=build/name
            if p.is_file():pairs.append((p,destination/f'build/node{rank}'/kind/name))
    if not pairs:raise ValueError('no recovery logs')
    for p,q in pairs:
        if q.exists() and q.read_bytes()!=p.read_bytes():raise ValueError('result conflict: '+str(q))
    for p,q in pairs:q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    print('GPU_STAGE_RECOVERY_COLLECTED '+str(destination))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--base-build',type=Path);p.add_argument('--trace-build',type=Path);p.add_argument('--destination',type=Path)
    a=p.parse_args();collect(a.root,a.node_rank,dict(base=a.base_build,trace=a.trace_build),a.destination or Path(__file__).resolve().parent/'results'/a.root.name)
