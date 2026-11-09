"""Run an independent strictly DMA-BUF WRITE-only job under the verbs idle gate."""
import json
import os
import subprocess
import sys
import common
from micro_support import select_geometries
from split_support import affinity


def run(args,preflight,environment,snapshot):
    root=args.output/args.job/f'node{args.node_rank}';root.mkdir(parents=True,exist_ok=False)
    error=None;meta=dict(job=args.job,suite='verbs_write_micro',threads=args.micro_threads,kernel=os.uname().release,
        warmups=args.warmups,samples=args.samples,hca=args.hca,gid_index=args.gid_index,diagnostics=common.diagnostics())
    try:
        module,manifest,identity,runtime,log=preflight(args)
        if not runtime['verbs_micro']:raise RuntimeError('VERBS_MICRO_REQUIRES_REBUILD')
        select_geometries(args.micro_layout);placement=affinity(args)
        meta.update(identity=identity,torch=runtime['torch'],libraries=runtime['selected_library_hashes'])
        for name,data in [('module_manifest.json',manifest),('runtime.json',runtime),('affinity.json',placement)]:
            (root/name).write_text(json.dumps(data,indent=2))
        common.legacy.snapshot_host(root,args.hca)
    except Exception as exc:error=str(exc)
    (root/'setup.json').write_text(json.dumps(dict(fingerprint=meta,error=error),indent=2))
    if error:print('VERBS_MICRO_SETUP_ERROR '+error,flush=True)
    common.legacy.pair(args,dict(stage='micro_setup',meta=common.digest(meta),rc=int(bool(error))))
    case=root/'micro';case.mkdir();rc=0
    try:common.legacy.idle_check(case,args.hca,args)
    except Exception as exc:rc=1;(case/'preflight_error.txt').write_text(str(exc))
    common.legacy.pair(args,dict(stage='micro_ready',job=args.job,rc=rc))
    request=dict(module=str(module),module_sha=runtime['module_sha'],rank=args.node_rank,threads=args.micro_threads,
        master_addr=args.master_addr,port=args.port+1,warmups=args.warmups,samples=args.samples,
        placement=placement,layout=str(args.micro_layout.resolve()),fingerprint=meta,hca=args.hca,gid_index=args.gid_index)
    (case/'request.json').write_text(json.dumps(request,indent=2));(case/'layout_input.jsonl').write_bytes(args.micro_layout.read_bytes())
    env=environment(args);env['VM_CASE']=str(case)
    for key,name in [('TMPDIR','tmpdir'),('XDG_CACHE_HOME','cache')]:
        (case/name).mkdir();env[key]=str(case/name)
    snapshot(case,args,'before');print('VERBS_MICRO_BEGIN '+str(case/'bench.log'),flush=True)
    with (case/'bench.log').open('w') as output:
        process=subprocess.Popen([sys.executable,'-u',str(common.HERE/'micro_worker.py')],env=env,stdout=output,stderr=subprocess.STDOUT,start_new_session=True)
        try:rc=process.wait(timeout=args.case_timeout)
        except subprocess.TimeoutExpired:common.legacy.terminate(process);rc=124
        except KeyboardInterrupt:common.legacy.terminate(process);raise
    text=(case/'bench.log').read_text(errors='replace')
    checks=dict(done='VERBS_MICRO_ALL_PASSED' in text,points=text.count('VERBS_MICRO_POINT ')==6,
        raw=(case/'micro_samples.jsonl').is_file(),no_errors='Traceback' not in text and 'ERROR' not in text,
        boundary=(case/'measurement_boundary.json').is_file())
    if rc==0 and not all(checks.values()):rc=90
    snapshot(case,args,'after');(case/'status.json').write_text(json.dumps(dict(rc=rc,checks=checks)))
    print(f'VERBS_MICRO_END rc={rc} checks={checks}',flush=True)
    common.legacy.pair(args,dict(stage='micro_done',job=args.job,rc=rc))
