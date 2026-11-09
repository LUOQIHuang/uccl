"""Copy one node's complete campaign, including failed cases, without binaries."""
import argparse
from pathlib import Path
import shutil


def collect(root,job,rank,destination):
    inputs=sorted(root.glob(job+f'_*/node{rank}'));files=[]
    for node in inputs:
        for p in node.rglob('*'):
            if p.is_file() and p.suffix in ('.json','.jsonl','.txt','.log','.csv','.py') and not any(x in p.parts for x in ('tmpdir','cache','__pycache__')):
                files.append((p,destination/node.parent.name/node.name/p.relative_to(node)))
    if not files:raise ValueError('NO_CAMPAIGN_RESULTS')
    for p,q in files:
        if q.exists() and q.read_bytes()!=p.read_bytes():raise ValueError('RESULT_CONFLICT: '+str(q))
    for p,q in files:q.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,q)
    print('VERBS_CAMPAIGN_COLLECTED '+str(destination))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--job',required=True);p.add_argument('--node-rank',type=int,choices=(0,1),required=True);p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args();collect(a.root,a.job,a.node_rank,a.destination)
