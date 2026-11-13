"""Collect existing fence-observation failures; no benchmark, cleanup or reset."""
import argparse
import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

REPO=Path(__file__).resolve().parents[2]
SUFFIXES=('observe_smoke128','all_on_paced','all_on_trace','all_on_post',
          'peer_off_paced','peer_off_trace','peer_off_post')


def existing_cases(root,job,rank):
    return [(suffix,root/(job+'_'+suffix)) for suffix in SUFFIXES
            if (root/(job+'_'+suffix)/('node%d'%rank)).is_dir()
            or (root/(job+'_'+suffix)/'console'/('node%d'%rank)).is_dir()]


def snapshot(command):
    try:
        p=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                         universal_newlines=True,errors='replace',timeout=30)
        return dict(command=command,rc=p.returncode,output=p.stdout)
    except (OSError,subprocess.TimeoutExpired) as exc:
        return dict(command=command,error=str(exc))


def collect(root,job,rank,build,destination,since,until):
    if not re.fullmatch('[a-zA-Z0-9_-]+',job):raise ValueError('invalid job')
    spec=importlib.util.spec_from_file_location('failure_collector',REPO/'test/gpu_stage_recovery/collect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    collected=[];collection_errors=[]
    for suffix,source in existing_cases(root,job,rank):
        try:
            module.collect(source,rank,dict(trace=build),destination/source.name)
            collected.append(suffix)
        except (ValueError,OSError) as exc:
            collection_errors.append(dict(suffix=suffix,error=str(exc)))
    # A separate directory retains read-only diagnostics, even when trace
    # never reached the driver's normal result/status writer.
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=destination/(job+'_failure_capture')/('node%d'%rank)/stamp
    target.mkdir(parents=True,exist_ok=False)
    commands=[['uname','-a'],['mx-smi'],['ps','-eo','pid,ppid,user,etime,comm'],
              ['fuser','-v','/dev/mxcd','/dev/uburma/udmac0d1e2'],
              ['journalctl','-k','--since',since,'--until',until,'--no-pager','-o','short-iso'],
              ['coredumpctl','--since',since,'--until',until,'--no-pager','list'],
              ['coredumpctl','--since',since,'--until',until,'--no-pager','info']]
    snapshots=[snapshot(command) for command in commands]
    barriers=[]
    for path in sorted(Path('/dev/shm').glob('uccl_barrier_*')):
        try:
            stat=path.stat();barriers.append(dict(path=str(path),uid=stat.st_uid,size=stat.st_size,mtime_ns=stat.st_mtime_ns))
        except OSError as exc:barriers.append(dict(path=str(path),error=str(exc)))
    console=root.parent/'fadd_fence_console'/job/('node%d'%rank)
    if console.is_dir():shutil.copytree(console,target/'external_console')
    present={s for s,_ in existing_cases(root,job,rank)}
    (target/'capture.json').write_text(json.dumps(dict(collected=collected,collection_errors=collection_errors,missing=[s for s in SUFFIXES if s not in present],
        root=str(root),job=job,rank=rank,since=since,until=until,barriers=barriers,snapshots=snapshots,
        note='Read-only collection. Missing trace is recorded, never synthesized. No benchmark, process kill, shared-memory unlink, device reset, module reload or boot. Core metadata/backtrace only; no core memory dump.'),indent=2))
    print('FENCE_FAILURE_COLLECTED '+str(target))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','build','destination'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--job',required=True);p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--since',required=True);p.add_argument('--until',required=True)
    a=p.parse_args();collect(a.root,a.job,a.node_rank,a.build,a.destination,a.since,a.until)
