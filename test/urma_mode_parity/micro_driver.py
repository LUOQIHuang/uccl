"""Host checks, provenance and fail-stop process ownership for URMA microbench."""
import json
import os
import subprocess
import sys
import support
import micro_support


def run(args,preflight,environment,kernel_snapshot):
    import importlib.util
    spec=importlib.util.spec_from_file_location('micro_legacy',support.REPO/'test/verbs_diag/driver.py')
    legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    import urma_support
    root=args.output/args.job/f'node{args.node_rank}'
    root.mkdir(parents=True,exist_ok=False)
    error=None;fingerprint=dict(job=args.job,kernel=os.uname().release,threads=args.micro_threads,
        warmups=args.warmups,samples=args.samples,normal_completion=args.normal_completion,
        device=args.urma_device,gpu=args.gpu,ifname=args.ifname,
        diagnostics_hash=support.digest(support.diagnostic_manifest()))
    try:
        module,manifest,info=preflight(args)
        # Validate local capture even though node0's selected plan is shared.
        micro_support.select_geometries(args.micro_layout)
        fingerprint.update(build_hash=support.digest({k:manifest[k] for k in ('sources','sdk','flags')}),libraries=info['libraries'],torch=info['runtime']['torch'])
        (root/'build_manifest.json').write_text(json.dumps(manifest,indent=2))
        (root/'preflight.json').write_text(json.dumps(info,indent=2))
        legacy.snapshot_host(root,args.hca,'urma',args.urma_device)
        if args.micro_suite in ('split','notify'):
            import split_support
            placement=split_support.affinity(args)
            (root/'affinity.json').write_text(json.dumps(placement,indent=2))
    except Exception as exc:error=str(exc)
    if args.micro_suite=='notify':fingerprint['repeat']=args.micro_repeat
    fingerprint['suite']=args.micro_suite
    (root/'setup.json').write_text(json.dumps(dict(fingerprint=fingerprint,error=error),indent=2))
    if error:print('MICRO_SETUP_ERROR '+error,flush=True)
    legacy.pair(args,dict(stage='micro_setup',meta_hash=support.digest(fingerprint),rc=1 if error else 0))
    if error:raise RuntimeError(error)
    case=root/'micro';case.mkdir();rc=0
    try:legacy.idle_check(case,args.hca,args)
    except Exception as exc:
        rc=1;(case/'preflight_error.txt').write_text(str(exc));print('MICRO_PREFLIGHT_ERROR '+str(exc),flush=True)
    legacy.pair(args,dict(stage='micro_ready',job=args.job,rc=rc))
    request=dict(module=str(module),module_sha=manifest['module_sha'],rank=args.node_rank,
        master_addr=args.master_addr,port=args.port+1,threads=args.micro_threads,
        warmups=args.warmups,samples=args.samples,layout=str(args.micro_layout.resolve()),fingerprint=fingerprint)
    worker='micro_worker.py';expected=12
    if args.micro_suite in ('split','notify'):
        import split_support
        request['placement']=placement;worker='split_worker.py'
        expected=len(split_support.points(micro_support.select_geometries(args.micro_layout),args.micro_threads))
    if args.micro_suite=='notify':worker='notify_worker.py';expected=8
    (case/'request.json').write_text(json.dumps(request,indent=2))
    (case/'layout_input.jsonl').write_bytes(args.micro_layout.read_bytes())
    env=environment(args);env['MICRO_CASE']=str(case)
    for name in ('tmpdir','cache'):(case/name).mkdir()
    env.update(TMPDIR=str(case/'tmpdir'),XDG_CACHE_HOME=str(case/'cache'))
    kernel_snapshot(case,'kernel_before.txt')
    (case/'counters_before.json').write_text(json.dumps(urma_support.counters(args)))
    print('URMA_MICRO_BEGIN log='+str(case/'bench.log'),flush=True)
    with (case/'bench.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-u',str(support.REPO/'test/urma_mode_parity'/worker)],
            env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        try:rc=process.wait(timeout=args.case_timeout)
        except subprocess.TimeoutExpired:legacy.terminate(process);rc=124
        except KeyboardInterrupt:legacy.terminate(process);raise
    text=(case/'bench.log').read_text(errors='replace')
    checks=dict(done='URMA_MICRO_ALL_PASSED' in text,points=text.count('URMA_MICRO_POINT ')==expected,
        raw=(case/'micro_samples.jsonl').is_file(),layout_verified=(case/'micro_layout_verified.jsonl').is_file(),boundary=(case/'measurement_boundary.json').is_file(),
        no_errors=not any(s in text for s in ('Traceback (most recent call last)','URMA_RECOVERY','Segmentation fault')))
    if rc==0 and not all(checks.values()):rc=90
    kernel_snapshot(case,'kernel_after.txt')
    (case/'counters_after.json').write_text(json.dumps(urma_support.counters(args)))
    (case/'status.json').write_text(json.dumps(dict(rc=rc,checks=checks),indent=2))
    print(f'URMA_MICRO_END rc={rc} checks={checks}',flush=True)
    legacy.pair(args,dict(stage='micro_done',job=args.job,rc=rc))
