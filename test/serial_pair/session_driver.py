"""Use the existing backend preflight/idle gate and its sanitized environment."""
import json,os,subprocess,sys,traceback
from pathlib import Path
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
r=json.loads(Path(sys.argv[1]).read_text());case=Path(r['case']);backend=r['backend']
sys.path.insert(0,str(REPO/'test'/('urma_mode_parity' if backend=='urma' else 'verbs_baseline')))
import driver
args=['serial_preflight','--action','check','--node-rank',str(r['node_rank']),'--job',r['job'],
      '--build-dir',r['build_dir'],'--mode','normal','--reference-events','paced','--dispatch-focus',
      '--expected-kernel','6.6.0-gdr_f00955680_diag' if backend=='urma' else '6.6.0-145.3.34.165.oe2403sp3.aarch64',
      '--master-addr',r['master_addr'],'--port',str(r['port'])]
args+=['--backend','urma','--phase-suite','--api-only'] if backend=='urma' else ['--variant','current','--no-profile','--repeats','1','--gid-index',str(r['gid_index'])]
sys.argv=args;a=driver.parser();rc=1
try:
    result=driver.preflight(a)
    if backend=='urma':module,manifest,info=result
    else:module,manifest,identity,info,_=result;legacy=driver.legacy
    # Import the shared gate by file, independent of backend module names.
    import importlib.util
    spec=importlib.util.spec_from_file_location('serial_legacy',REPO/'test/verbs_diag/driver.py');legacy=importlib.util.module_from_spec(spec);spec.loader.exec_module(legacy)
    legacy.snapshot_host(case,r['hca'],backend,'udmac0d1e2')
    legacy.idle_check(case,r['hca'],a)
    (case/'manifest.json').write_text(json.dumps(manifest,indent=2))
    (case/'preflight.json').write_text(json.dumps(info,indent=2))
    r['module']=str(module);r['module_sha']=manifest['module_sha'];r['source_id']={k:manifest[k] for k in ('sources','flags','sdk') if k in manifest}
    req=case/'worker_request.json';req.write_text(json.dumps(r,indent=2))
    env=driver.environment(a);env['SERIAL_PAIR_REQUEST']=str(req)
    with (case/'bench.log').open('w') as log:
        rc=subprocess.run([sys.executable,'-u',str(HERE/'worker.py')],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1500).returncode
    text=(case/'bench.log').read_text(errors='replace')
    status=case/'status.json'
    if rc==0 and (not status.exists() or not json.loads(status.read_text()).get('passed') or 'SERIAL_PAIR_ALL_PASSED' not in text):rc=90
    if rc:raise RuntimeError('serial child failed rc=%s'%rc)
except BaseException as exc:
    (case/'failure.json').write_text(json.dumps(dict(error=str(exc),traceback=traceback.format_exc()),indent=2));print(traceback.format_exc(),flush=True)
finally:
    # No trace activation or changes to PCIe/kernel state.
    try:
        subprocess.run(['journalctl','-b','-k','-n','400','--no-pager'],stdout=(case/'kernel_after.txt').open('w'),stderr=subprocess.STDOUT,timeout=20)
    except Exception:pass
    (case/'driver_status.json').write_text(json.dumps(dict(rc=rc)))
raise SystemExit(rc)
