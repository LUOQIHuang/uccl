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


def crash_matches(root,job,rank):
    matches=set()
    for _,source in existing_cases(root,job,rank):
        node=source/('node%d'%rank);host=node/'host.json';log=node/'normal/bench.log'
        if not host.is_file() or not log.is_file():continue
        boot=json.loads(host.read_text()).get('boot_id','').replace('-','')
        if not re.fullmatch('[0-9a-fA-F]{32}',boot):continue
        text=log.read_text(errors='replace')
        for pid in re.findall(r'\bpid:\s*(\d+)',text):matches.add((boot,pid))
    return sorted(matches)


def collect(root,job,rank,build,destination,since,until):
    if not re.fullmatch('[a-zA-Z0-9_-]+',job):raise ValueError('invalid job')
    spec=importlib.util.spec_from_file_location('failure_collector',REPO/'test/gpu_stage_recovery/collect.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    target=destination/(job+'_failure_capture')/('node%d'%rank)/stamp
    target.mkdir(parents=True,exist_ok=False)
    collected=[];collection_errors=[];archived=[]
    for suffix,source in existing_cases(root,job,rank):
        try:
            module.collect(source,rank,dict(trace=build),destination/source.name)
            collected.append(suffix)
        except (ValueError,OSError) as exc:
            collection_errors.append(dict(suffix=suffix,error=str(exc)))
            # Keep changed source evidence without overwriting published data.
            alternative=target/'conflicting_source'/source.name
            try:
                module.collect(source,rank,dict(trace=build),alternative)
                archived.append(str(alternative.relative_to(target)))
            except (ValueError,OSError) as err:
                collection_errors.append(dict(suffix=suffix,archive_error=str(err)))
    # A separate directory retains read-only diagnostics, even when trace
    # never reached the driver's normal result/status writer.
    # A trace may remain in the repository collection after raw output was
    # removed. Preserve that existing evidence too, never synthesize a case.
    present={s for s,_ in existing_cases(root,job,rank)}
    for suffix,source in existing_cases(destination,job,rank):
        if suffix not in present:
            alternative=target/'destination_only'/source.name
            module.collect(source,rank,dict(),alternative)
            archived.append(str(alternative.relative_to(target)))
    commands=[['uname','-a'],['mx-smi'],['ps','-eo','pid,ppid,user,etime,comm'],
              ['fuser','-v','/dev/mxcd','/dev/uburma/udmac0d1e2'],
              ['journalctl','--list-boots','--no-pager'],
              ['journalctl','_TRANSPORT=kernel','--since',since,'--until',until,'--no-pager','-o','short-iso'],
              ['coredumpctl','--since',since,'--until',until,'--no-pager','list']]
    matches=sorted(set(crash_matches(root,job,rank)+crash_matches(destination,job,rank)))
    for boot,pid in matches:
        commands.append(['coredumpctl','--no-pager','info','COREDUMP_PID='+pid,'_BOOT_ID='+boot])
    snapshots=[snapshot(command) for command in commands]
    barriers=[]
    for path in sorted(Path('/dev/shm').glob('uccl_barrier_*')):
        try:
            stat=path.stat();barriers.append(dict(path=str(path),uid=stat.st_uid,size=stat.st_size,mtime_ns=stat.st_mtime_ns))
        except OSError as exc:barriers.append(dict(path=str(path),error=str(exc)))
    console=root.parent/'fadd_fence_console'/job/('node%d'%rank)
    if console.is_dir():shutil.copytree(console,target/'external_console')
    (target/'capture.json').write_text(json.dumps(dict(collected=collected,collection_errors=collection_errors,archived=archived,crash_matches=matches,missing=[s for s in SUFFIXES if s not in present],
        root=str(root),job=job,rank=rank,since=since,until=until,barriers=barriers,snapshots=snapshots,
        note='Read-only collection. Missing trace is recorded, never synthesized. No benchmark, process kill, shared-memory unlink, device reset, module reload or boot. Core metadata/backtrace only; no core memory dump.'),indent=2))
    print('FENCE_FAILURE_COLLECTED '+str(target))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('root','build','destination'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--job',required=True);p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--since',required=True);p.add_argument('--until',required=True)
    a=p.parse_args();collect(a.root,a.job,a.node_rank,a.build,a.destination,a.since,a.until)
