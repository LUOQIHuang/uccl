"""Build/check/run/collect the wire-v2 transport, using the real benchmarks."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent/'verbs_diag'))
legacy_spec=importlib.util.spec_from_file_location('legacy_diag_driver',HERE.parent/'verbs_diag/driver.py')
legacy=importlib.util.module_from_spec(legacy_spec)
legacy_spec.loader.exec_module(legacy)
import profile_support
import urma_support
import support
import phase_support

def sdk_directory(args):
    candidates=[args.urma_include_dir] if args.urma_include_dir else [args.urma_lib_dir.parent/'include',Path('/usr/include/ub/umdk/urma'),Path('/usr/include/umdk'),Path('/usr/include/urma'),Path('/usr/local/include'),Path('/usr/include')]
    directory=next((p.resolve() for p in candidates if p and (p/'urma_api.h').is_file()),None)
    if directory is None: raise RuntimeError('PRIVATE_SDK_HEADERS_MISSING: set URMA_DIAG_INCLUDE_DIR / --urma-include-dir')
    for name in ('urma_api.h','urma_types.h'):
        path=directory/name
        if path.is_file() and any(marker in path.read_bytes() for marker in (b'Test fixture only',b'CPU test placeholder')):
            raise RuntimeError('MOCK_SDK_REJECTED: production build requires the real private SDK')
    return directory

def build(args):
    sources=support.source_manifest()
    include=sdk_directory(args)
    sdk={p.name:support.sha(p) for p in include.glob('*.h')}
    args.build_dir.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.pop('LD_PRELOAD',None); env.pop('UMDK_HOME',None)
    env['UCCL_URMA_LL_WRITE_ORDER']='default'
    env['UCCL_URMA_NORMAL_WRITE_ORDER']='default'
    env['UCCL_URMA_NORMAL_COMPLETION']='all'
    flags=dict(UCCL_GPU_STAGE_TRACE=str(int(getattr(args,'gpu_stage_build',False))),USE_METAX='1',USE_URMA='1',USE_URMA_METAX='0',USE_LIBFABRIC_CXI='0',USE_DMABUF='1',MAX_NUM_GPUS='1',NUM_MAX_NVL_PEERS='1',PER_EXPERT_BATCHING=str(args.per_expert_batching),MAX_JOBS=str(args.build_jobs),URMA_INCLUDE_DIR=str(include),URMA_LIBRARY_DIR=str(args.urma_lib_dir.resolve()))
    env.update(flags)
    if getattr(args,'gpu_stage_roles_only',False):
        flags['UCCL_GPU_STAGE_ROLES_ONLY']='1';env['UCCL_GPU_STAGE_ROLES_ONLY']='1'
    else:env.pop('UCCL_GPU_STAGE_ROLES_ONLY',None)
    stable_flags={key:value for key,value in flags.items() if key not in ('URMA_INCLUDE_DIR','URMA_LIBRARY_DIR','MAX_JOBS')}
    if (args.build_dir/'manifest.json').exists():
        module,previous=support.read_build(args.build_dir)
        if previous['sdk']!=sdk or previous['flags']!=stable_flags:
            raise RuntimeError('BUILD_INPUTS_CHANGED: use a new build directory')
        print('BUILD_ALREADY_PASSED module='+str(module),flush=True)
        return
    request=dict(sources=sources,sdk=sdk,flags=stable_flags)
    request_path=args.build_dir/'build_request.json'
    if request_path.exists() and json.loads(request_path.read_text())!=request:
        raise RuntimeError('BUILD_INPUTS_CHANGED: use a new build directory')
    request_path.write_text(json.dumps(request,indent=2))
    probe_index=1
    while (args.build_dir/f'precision_probe_{probe_index:02d}').exists():probe_index+=1
    probe_dir=args.build_dir/f'precision_probe_{probe_index:02d}'
    print('PRECISION_PROBE_STARTED log='+str(probe_dir.with_suffix('.log')),flush=True)
    with probe_dir.with_suffix('.log').open('w') as log:
        subprocess.run([sys.executable,str(support.REPO/'test/gpu_stage_recovery/precision_probe.py'),'--output',str(probe_dir)]+(['--trace-build'] if getattr(args,'gpu_stage_build',False) else []),env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    # Validate against the REAL private SDK before starting GPU compilation.
    command=[sys.executable,str(support.REPO/'ep/tests/run_urma_tests.py'),'--sdk-include',str(include)]
    with (args.build_dir/'host_tests.log').open('a') as log:
        subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    command=[sys.executable,str(support.REPO/'ep/setup.py'),'build_ext','--build-lib',str(args.build_dir/'lib'),'--build-temp',str(args.build_dir/'objects')]
    print('BUILD_STARTED log='+str(args.build_dir/'build.log'),flush=True)
    with (args.build_dir/'build.log').open('a') as log:
        subprocess.run(command,cwd=support.REPO/'ep',env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    modules=list((args.build_dir/'lib').glob('ep*.so'))
    if len(modules)!=1: raise RuntimeError(f'expected one built extension, found {modules}')
    if sources!=support.source_manifest(): raise RuntimeError('SOURCES_CHANGED_DURING_BUILD')
    record=dict(sources=sources,sdk=sdk,flags=stable_flags,module=str(modules[0].relative_to(args.build_dir)),module_sha=support.sha(modules[0]))
    (args.build_dir/'manifest.json').write_text(json.dumps(record,indent=2))
    print('BUILD_PASSED module='+str(modules[0]),flush=True)

def parser():
    root=Path(os.environ.get('VERBS_DIAG_HOME',str(Path.home())))
    dev=Path(os.environ.get('VERBS_DIAG_DEV_ROOT',str(root/'metax_dev')))
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action',choices=('build','check','run','collect','probe','micro'),default='check')
    p.add_argument('--capture-layout',action='store_true')
    p.add_argument('--notification-check',action='store_true')
    p.add_argument('--timeline',action='store_true')
    p.add_argument('--gpu-stages',action='store_true')
    p.add_argument('--dispatch-focus',action='store_true')
    p.add_argument('--dispatch-compiled-off',action='store_true',help='Dispatch trace build with capture completely disabled; same API timing boundary as baseline')
    p.add_argument('--gpu-stage-roles-only',action='store_true',help='sparse compile-time role probes; currently only build or compiled-off validation')
    p.add_argument('--gpu-stage-mode',choices=('bypass','access','clock','kernels','waits','operators','all','dispatch_access','dispatch_clock','dispatch'),default='all')
    p.add_argument('--gpu-stage-build',action='store_true')
    p.add_argument('--micro-repeat',type=int,choices=(1,2,3),default=1)
    p.add_argument('--dispatch-trial',action='store_true')
    p.add_argument('--dispatch-delay-us',type=int,choices=(0,200),default=0)
    p.add_argument('--micro-layout',type=Path)
    p.add_argument('--micro-threads',type=int,choices=(1,4),default=4)
    p.add_argument('--micro-suite',choices=('legacy','split','notify'),default='legacy')
    p.add_argument('--micro-cpus',default='auto',help='split suite: four distinct CPU IDs, or auto on GPU NUMA node')
    p.add_argument('--probe-transports',type=int,choices=(1,4),default=4)
    p.add_argument('--probe-channels',type=int,choices=(1,8),default=8)
    p.add_argument('--probe-control',choices=('production','torch'),default='production')
    p.add_argument('--probe-bytes',type=int,default=117506432)
    p.add_argument('--phase-suite',action='store_true')
    p.add_argument('--api-only',action='store_true',help='phase suite: skip Kineto and retain whole-call samples only')
    p.add_argument('--ll-write-order',choices=('default','strong','relax'),default='default',help='diagnostic LL WRITE ordering; FADD fences unchanged')
    p.add_argument('--normal-write-order',choices=('default','strong','relax'),default='default',help='diagnostic Normal WRITE placement; FADD, fence and completion unchanged')
    p.add_argument('--normal-completion',choices=('all','ordered','paired'),default='all',help='Normal-only completion A/B; paired suppresses eligible WRITE CQEs')
    p.add_argument('--stage-profile',action='store_true')
    p.add_argument('--save-traces',action='store_true')
    p.add_argument('--warmups',type=int,default=50)
    p.add_argument('--samples',type=int,default=50)
    p.add_argument('--ll-hook-workload',choices=('gemm','immediate'),default='gemm')
    p.add_argument('--job',required=True)
    p.add_argument('--collect-suite',action='store_true',help='collect --job_* jobs under --output into one shared results folder')
    p.add_argument('--node-rank',type=int,choices=(0,1),required=True)
    p.add_argument('--master-addr')
    p.add_argument('--port',type=int,default=31280)
    p.add_argument('--build-dir',type=Path)
    p.add_argument('--output',type=Path,default=dev/'urma_mode_parity_results')
    p.add_argument('--backend',choices=('urma','verbs'),default='urma')
    p.add_argument('--mode',choices=('both','normal','low_latency'),default='both')
    p.add_argument('--normal-tokens',type=int,default=4096)
    p.add_argument('--ll-tokens',type=int,default=128)
    p.add_argument('--hidden',type=int,default=7168)
    p.add_argument('--experts',type=int,default=256)
    p.add_argument('--topk',type=int,default=8)
    p.add_argument('--build-jobs',type=int,default=4)
    p.add_argument('--per-expert-batching',type=int,choices=(0,1),default=0)
    p.add_argument('--urma-include-dir',type=Path,default=os.environ.get('URMA_DIAG_INCLUDE_DIR'))
    p.add_argument('--urma-lib-dir',type=Path,default=Path(os.environ.get('VERBS_DIAG_LIBRARY_DIR',str(dev/'urma_after_kernel_20261029/diag/umdk_private_618957c3/lib'))))
    p.add_argument('--ifname',default='enp33s0'); p.add_argument('--hca',default='rocep40s0f0')
    p.add_argument('--gpu',default='0'); p.add_argument('--case-timeout',type=int,default=1800)
    p.add_argument('--expected-kernel',default='6.6.0-gdr_f00955680_diag')
    p.add_argument('--urma-device',default='udmac0d1e2'); p.add_argument('--gpu-pci',default='0000:a1:00.0'); p.add_argument('--atomic-port',default='0000:a0:08.0')
    p.add_argument('--ubctl-selector',default='0,1,1')
    args=p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+',args.job): p.error('invalid job')
    if args.action=='run' and not args.master_addr: p.error('--master-addr is required to run')
    if args.warmups<1 or args.samples<3: p.error('warmups >= 1 and samples >= 3 required')
    if (args.stage_profile or args.save_traces or args.ll_hook_workload=='immediate') and not args.phase_suite:
        p.error('stage/trace/immediate-hook options require --phase-suite')
    if args.phase_suite and args.backend!='urma': p.error('phase suite currently supports URMA only')
    if args.api_only and (not args.phase_suite or args.save_traces): p.error('--api-only requires --phase-suite and excludes --save-traces')
    if args.ll_write_order!='default' and args.backend!='urma': p.error('LL WRITE order diagnostics require URMA')
    if args.normal_write_order!='default' and (args.backend!='urma' or args.mode!='normal' or args.normal_completion!='all' or not args.api_only or args.action in ('micro','probe')):
        p.error('Normal WRITE order A/B requires URMA Normal API-only completion=all; excludes micro/probe')
    if args.normal_completion!='all' and (args.backend!='urma' or args.mode!='normal'):
        p.error('normal completion diagnostics require --backend urma --mode normal')
    if args.collect_suite and args.action!='collect': p.error('--collect-suite requires --action collect')
    if args.capture_layout and (args.mode!='normal' or not args.api_only or args.backend!='urma'):
        p.error('--capture-layout requires URMA Normal API-only')
    if args.dispatch_trial and (args.backend!='urma' or args.mode!='normal' or not args.api_only or args.normal_completion!='all'):
        p.error('dispatch trial requires URMA Normal API-only all')
    if args.timeline and (args.backend!='urma' or args.mode!='normal' or not args.api_only or not args.stage_profile or args.dispatch_trial or args.normal_completion!='all'):p.error('timeline requires URMA Normal API-only stage profile all; excludes delay trial')
    if args.dispatch_focus and (args.mode!='normal' or not args.api_only or args.stage_profile or args.timeline or args.capture_layout or args.dispatch_trial or args.normal_completion!='all' or args.normal_write_order!='default' or args.ll_write_order!='default'):p.error('dispatch focus requires Normal API-only default ordering/completion=all without CPU/timeline/layout/trial')
    if args.dispatch_focus and args.gpu_stages and args.gpu_stage_mode not in ('bypass','dispatch_access','dispatch_clock','dispatch'):p.error('invalid Dispatch focus GPU mode')
    if args.dispatch_compiled_off and (not args.dispatch_focus or args.gpu_stages or args.gpu_stage_build or args.action not in ('check','run')):p.error('compiled-off requires Dispatch focus check/run without GPU capture/build switches')
    if args.gpu_stage_roles_only and not ((args.action=='build' and args.gpu_stage_build) or args.dispatch_compiled_off):p.error('roles-only currently requires trace build or compiled-off; active capture is gated on the disabled-cost result')
    if args.gpu_stage_mode.startswith('dispatch') and not args.dispatch_focus:p.error('Dispatch GPU modes require --dispatch-focus')
    if args.gpu_stages and not args.dispatch_focus and (not args.timeline or not args.capture_layout):p.error('--gpu-stages requires --timeline --capture-layout')
    if args.micro_suite=='notify' and args.micro_threads!=4:p.error('notification micro requires four threads')
    if args.dispatch_delay_us and not args.dispatch_trial:p.error('--dispatch-delay-us requires --dispatch-trial')
    if args.action=='micro' and (args.backend!='urma' or args.mode!='normal' or args.normal_completion!='all' or not args.master_addr or not args.micro_layout):
        p.error('micro requires URMA Normal all, master address and --micro-layout')
    if args.micro_cpus!='auto' and not re.fullmatch(r'\d+,\d+,\d+,\d+',args.micro_cpus):p.error('--micro-cpus requires four comma-separated CPU IDs')
    if args.action=='probe' and (args.backend!='urma' or args.mode=='both' or args.probe_bytes<1):
        p.error('probe requires --backend urma --mode normal|low_latency and positive --probe-bytes')
    if not (1024<=args.port<65535) or not re.fullmatch(r'\d+,\d+,\d+',args.ubctl_selector): p.error('invalid port / UB selector')
    if args.hidden<=0 or args.hidden%128 or args.normal_tokens<=0 or args.ll_tokens<=0 or args.ll_tokens>8191 or args.experts%2 or not 1<=args.topk<=min(args.experts,16) or args.build_jobs<1:
        p.error('invalid benchmark shape / build jobs')
    args.build_dir=(args.build_dir or dev/'urma_mode_parity_build'/args.job).resolve()
    args.output=args.output.resolve(); args.urma_lib_dir=args.urma_lib_dir.resolve()
    return args

def environment(args):
    env=os.environ.copy()
    for key in list(env):
        if key.startswith(('UCCL_','PARITY_','DIAG_')) and key!='UCCL_METAX_RUNTIME_LIB':env.pop(key,None)
    for key in ('LD_PRELOAD','PYTHONPATH','UCCL_PROJECT_DIAG','UCCL_METAX_URMA_HOST_ATOMICS','UMDK_HOME','UCCL_EP_DISPATCH_CONFIG','UCCL_EP_COMBINE_CONFIG'):
        env.pop(key,None)
    env.update(UCCL_EP_TRANSPORT=args.backend,UCCL_URMA_TM='rc',UCCL_URMA_DEV_NAME=args.urma_device,URMA_DIAG_LIBRARY_DIR=str(args.urma_lib_dir),CUDA_VISIBLE_DEVICES=args.gpu,MCCL_IB_DISABLE='1',NCCL_IB_DISABLE='1',MCCL_SOCKET_IFNAME=args.ifname,NCCL_SOCKET_IFNAME=args.ifname,MCCL_DEBUG='INFO',NCCL_DEBUG='INFO',UCCL_RDMA_ADAPTIVE_SLEEP='0',UCCL_EP_DISABLE_CPU_AFFINITY='1',PYTHONUNBUFFERED='1',PYTHONDONTWRITEBYTECODE='1',UCCL_EP_VERBS_REG='dmabuf',UCCL_IB_HCA=args.hca)
    if args.backend=='urma':
        env.pop('UCCL_EP_VERBS_REG',None); env.pop('UCCL_IB_HCA',None)
    env['UCCL_URMA_LL_WRITE_ORDER']=args.ll_write_order
    env['UCCL_URMA_NORMAL_WRITE_ORDER']=getattr(args,'normal_write_order','default')
    env['UCCL_URMA_NORMAL_COMPLETION']=args.normal_completion
    env['LD_LIBRARY_PATH']=str(args.urma_lib_dir)+':'+env.get('LD_LIBRARY_PATH','')
    return env

def preflight(args):
    module,manifest=support.read_build(args.build_dir)
    libraries=urma_support.library_manifest(args.urma_lib_dir)
    hardware=urma_support.hardware_check(args) if args.backend=='urma' else {}
    env=environment(args)
    probe=subprocess.run([sys.executable,str(HERE/'support.py'),str(module)],env=env,capture_output=True,text=True,timeout=90)
    if probe.returncode: raise RuntimeError(probe.stdout+probe.stderr)
    metadata=next(json.loads(line.split(' ',1)[1]) for line in probe.stdout.splitlines() if line.startswith('MODE_PARITY_IMPORT_OK '))
    if args.phase_suite and not metadata.get('phase_diagnostics'):
        raise RuntimeError('PHASE_DIAGNOSTICS_REQUIRES_REBUILD')
    if args.ll_write_order!='default' and metadata.get('ll_write_order')!=args.ll_write_order:
        raise RuntimeError('LL_WRITE_ORDER_DIAGNOSTICS_REQUIRES_REBUILD')
    if getattr(args,'normal_write_order','default')!='default' and metadata.get('normal_write_order')!=args.normal_write_order:
        raise RuntimeError('NORMAL_WRITE_ORDER_DIAGNOSTICS_REQUIRES_REBUILD')
    if args.normal_completion!='all' and metadata.get('normal_completion')!=args.normal_completion:
        raise RuntimeError('NORMAL_COMPLETION_DIAGNOSTICS_REQUIRES_REBUILD')
    if args.capture_layout and not metadata.get('layout_diagnostics'):raise RuntimeError('LAYOUT_DIAGNOSTICS_REQUIRES_REBUILD')
    if args.dispatch_trial and not metadata.get('dispatch_trial'):raise RuntimeError('DISPATCH_TRIAL_REQUIRES_REBUILD')
    if args.action=='micro' and not metadata.get('micro_diagnostics'):raise RuntimeError('MICRO_DIAGNOSTICS_REQUIRES_REBUILD')
    if (getattr(args,'notification_check',False) or getattr(args,'timeline',False) or args.micro_suite=='notify') and metadata.get('notify_version')!=1:raise RuntimeError('URMA_NOTIFICATION_SUITE_REQUIRES_REBUILD')
    if args.micro_suite=='split' and metadata.get('micro_version')!=2:raise RuntimeError('MICRO_SPLIT_REQUIRES_REBUILD_V2')
    if getattr(args,'gpu_stages',False) and metadata.get('gpu_stage_version')!=2:raise RuntimeError('GPU_STAGE_REQUIRES_REBUILD')
    if getattr(args,'dispatch_focus',False) and metadata.get('dispatch_trace_version')!=1:raise RuntimeError('DISPATCH_FOCUS_REQUIRES_REBUILD')
    if getattr(args,'dispatch_compiled_off',False):
        if metadata.get('gpu_stage_version')!=2 or manifest['flags'].get('UCCL_GPU_STAGE_TRACE')!='1':raise RuntimeError('COMPILED_OFF_REQUIRES_TRACE_BUILD_V2')
    elif not getattr(args,'gpu_stages',False) and metadata.get('gpu_stage_version',0):raise RuntimeError('CPU_OR_BASELINE_REQUIRES_GPU_TRACE_OFF_BUILD')
    if manifest.get('flags',{}).get('UCCL_GPU_STAGE_ROLES_ONLY','0')!=str(int(getattr(args,'gpu_stage_roles_only',False))):raise RuntimeError('GPU_STAGE_ROLE_BUILD_MISMATCH')
    return module,manifest,dict(libraries=libraries,hardware=hardware,import_log=probe.stdout+probe.stderr,runtime=metadata)

def collect(args):
    suite=getattr(args,'collect_suite',False)
    sources=sorted(args.output.glob(args.job+f'_*/node{args.node_rank}')) if suite else [args.output/args.job/f'node{args.node_rank}']
    target=support.REPO/'test/urma_mode_parity/results'/args.job if suite else support.REPO/'test/bench_results/urma_mode_parity'/args.job/f'node{args.node_rank}'
    files=[]
    for source in sources:
        for p in source.rglob('*'):
            if p.is_file() and p.suffix in ('.json','.jsonl','.log','.txt','.csv','.py') and 'tmpdir' not in p.parts and 'cache' not in p.parts:
                relative=Path(source.parent.name)/source.name/p.relative_to(source) if suite else p.relative_to(source)
                files.append((p,target/relative))
    if not files: raise RuntimeError('NO_RESULTS_TO_COLLECT')
    for p,out in files:
        if out.exists() and out.read_bytes()!=p.read_bytes(): raise RuntimeError('RESULT_CONFLICT: '+str(out))
    for p,out in files:
        out.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,out)
    print('COLLECTED='+str(target),flush=True)

def kernel_snapshot(root, name):
    # Keep diagnostics failures from hiding the original registration failure.
    try:
        legacy.capture(root/name,['journalctl','-b','-k','-n','400','--no-pager'],timeout=20)
    except Exception as exc:
        (root/name).write_text('KERNEL_SNAPSHOT_FAILED: '+str(exc))

def probe(args):
    root=args.output/args.job/f'node{args.node_rank}'
    root.mkdir(parents=True,exist_ok=False)
    module,manifest,info=preflight(args)
    (root/'preflight.json').write_text(json.dumps(info,indent=2))
    (root/'build_manifest.json').write_text(json.dumps(manifest,indent=2))
    legacy.snapshot_host(root,args.hca,args.backend,args.urma_device)
    legacy.idle_check(root,args.hca,args)
    kernel_snapshot(root,'kernel_before.txt')
    command=[sys.executable,'-u',str(HERE/'probe.py'),'--module',str(module),
             '--mode',args.mode,'--channels',str(args.probe_channels),
             '--transports',str(args.probe_transports),'--bytes',str(args.probe_bytes),
             '--control',args.probe_control]
    try:
        with (root/'probe.log').open('w') as log:
            result=subprocess.run(command,env=environment(args),stdout=log,stderr=subprocess.STDOUT,timeout=300)
        (root/'status.json').write_text(json.dumps(dict(rc=result.returncode)))
        if result.returncode: raise RuntimeError('REGISTRATION_PROBE_FAILED: '+str(root/'probe.log'))
    finally:
        kernel_snapshot(root,'kernel_after.txt')
    print('REGISTRATION_PROBE_PASSED output='+str(root),flush=True)

def main():
    args=parser()
    if args.action=='build': build(args); return
    if args.action=='collect': collect(args); return
    if args.action=='probe': probe(args); return
    if args.action=='micro':
        import micro_driver
        micro_driver.run(args,preflight,environment,kernel_snapshot)
        return
    if args.action=='check':
        _,_,info=preflight(args)
        print(json.dumps(info,indent=2)); print('MODE_PARITY_CHECK_PASSED',flush=True); return
    root=args.output/args.job/f'node{args.node_rank}'
    root.mkdir(parents=True,exist_ok=False)
    error=None; fingerprint=dict(job=args.job,backend=args.backend,mode=args.mode,shape=[args.normal_tokens,args.ll_tokens,args.hidden,args.experts,args.topk],sources_hash=support.digest(support.source_manifest()),diagnostics_hash=support.digest(support.diagnostic_manifest()),ifname=args.ifname,kernel=os.uname().release,
        phase_suite=args.phase_suite,stage_profile=args.stage_profile,save_traces=args.save_traces,
        warmups=args.warmups,samples=args.samples,ll_hook_workload=args.ll_hook_workload,
        api_only=args.api_only,ll_write_order=args.ll_write_order,normal_write_order=args.normal_write_order,normal_completion=args.normal_completion,capture_layout=args.capture_layout,
        dispatch_trial=args.dispatch_trial,dispatch_delay_us=args.dispatch_delay_us,delay_node=1,timeline=getattr(args,'timeline',False),gpu_stages=getattr(args,'gpu_stages',False),gpu_stage_mode=args.gpu_stage_mode,device=args.urma_device,gpu=args.gpu,dispatch_focus=args.dispatch_focus,dispatch_compiled_off=args.dispatch_compiled_off,gpu_stage_roles_only=args.gpu_stage_roles_only)
    try:
        module,manifest,info=preflight(args)
        fingerprint.update(build_hash=support.digest({key:manifest[key] for key in ('sources','sdk','flags')}),libraries=info['libraries'],runtime_libraries=info['runtime'].get('selected_runtime_hashes',{}),torch=info['runtime']['torch'])
        (root/'preflight.json').write_text(json.dumps(info,indent=2))
        (root/'build_manifest.json').write_text(json.dumps(manifest,indent=2))
        legacy.snapshot_host(root,args.hca,args.backend,args.urma_device)
        if getattr(args,'gpu_stages',False):shutil.copy2(HERE/'gpu_stage_sites.json',root/'gpu_stage_sites.json')
        if args.dispatch_focus:shutil.copy2(HERE.parent/'dispatch_diag/dispatch_sites.json',root/'dispatch_sites.json')
        for mode,filename in [('normal','test_internode.py'),('low_latency','test_low_latency.py')]:
            source=(support.REPO/'ep/bench'/filename).read_text()
            if mode=='normal': source=profile_support.prepare_benchmark(source,'boundaries')
            elif args.phase_suite and args.ll_hook_workload=='immediate': source=phase_support.immediate_hook_source(source)
            (root/(mode+'.py')).write_text(source)
    except Exception as exc: error=str(exc)
    (root/'setup.json').write_text(json.dumps(dict(fingerprint=fingerprint,error=error),indent=2))
    legacy.pair(args,dict(stage='setup',meta=fingerprint,rc=1 if error else 0))
    if error: raise RuntimeError(error)
    modes=('normal','low_latency') if args.mode=='both' else (args.mode,)
    for mode in modes:
        case=root/mode; case.mkdir()
        rc=0
        try: legacy.idle_check(case,args.hca,args)
        except Exception as exc:
            rc=1; (case/'preflight_error.txt').write_text(str(exc))
        legacy.pair(args,dict(stage='ready',job=args.job,mode=mode,rc=rc))
        env=environment(args)
        env.update(PARITY_CASE=str(case),PARITY_MODULE=str(module),PARITY_MODULE_SHA=manifest['module_sha'],PARITY_MODE=mode,PARITY_TOKENS=str(args.normal_tokens if mode=='normal' else args.ll_tokens),PARITY_HIDDEN=str(args.hidden),PARITY_EXPERTS=str(args.experts),PARITY_TOPK=str(args.topk),UCCL_TORCHRUN_NODE_RANK=str(args.node_rank),UCCL_EXIT_GUARD='1')
        env.update(PARITY_PHASE_SUITE=str(int(args.phase_suite)),PARITY_STAGE_PROFILE=str(int(args.stage_profile)),
                   PARITY_DISPATCH_FOCUS=str(int(args.dispatch_focus)),
                   PARITY_DISPATCH_TRIAL=str(int(args.dispatch_trial)),PARITY_DISPATCH_DELAY_US=str(args.dispatch_delay_us if args.node_rank==1 else 0),
                   PARITY_GPU_STAGES=str(int(getattr(args,'gpu_stages',False))), PARITY_GPU_STAGE_MODE=args.gpu_stage_mode, PARITY_API_ONLY=str(int(args.api_only)), PARITY_TIMELINE=str(int(getattr(args,'timeline',False))),
                   PARITY_CAPTURE_LAYOUT=str(int(args.capture_layout)),
                   PARITY_SAVE_TRACES=str(int(args.save_traces)),PARITY_WARMUPS=str(args.warmups),
                   PARITY_SAMPLES=str(args.samples),PARITY_LL_HOOK_WORKLOAD=args.ll_hook_workload)
        for key in ('TMPDIR','XDG_CACHE_HOME'):
            directory=case/('tmpdir' if key=='TMPDIR' else 'cache'); directory.mkdir(); env[key]=str(directory)
        (case/'counters_before.json').write_text(json.dumps(urma_support.counters(args) if args.backend=='urma' else legacy.counters(args.hca)))
        kernel_snapshot(case,'kernel_before.txt')
        command=[sys.executable,str(HERE.parent/'verbs_diag/exit_guard.py'),'--nnodes=2','--nproc_per_node=1',f'--node_rank={args.node_rank}',f'--master_addr={args.master_addr}',f'--master_port={args.port+1}','--no-python',sys.executable,'-u',str(HERE/'worker.py')]
        print('CASE_BEGIN '+mode,flush=True)
        with (case/'bench.log').open('w') as log:
            process=subprocess.Popen(command,cwd=support.REPO/'ep/bench',env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try: rc=process.wait(timeout=args.case_timeout)
            except subprocess.TimeoutExpired: legacy.terminate(process); rc=124
            except KeyboardInterrupt: legacy.terminate(process); raise
        text=(case/'bench.log').read_text(errors='replace')
        kernel_snapshot(case,'kernel_after.txt')
        checks=dict(mode=f'TRANSPORT_MODE_OK backend={args.backend} mode={mode}' in text,done='MODE_PARITY_BENCH_DONE' in text,exit_ack=f'UCCL_EXIT_ACK_OK node_rank={args.node_rank}' in text,no_errors=not re.search(r'Traceback \(most recent call last\)|AssertionError|Failed CR status|Segmentation fault|ChildFailedError|\[WARNING\]',text))
        expected_points=4 if mode=='normal' else 2
        point_marker='MODE_PARITY_API_ONLY_POINT ' if args.api_only else 'MODE_PARITY_PROFILE '
        if mode=='normal': checks.update(correctness=text.count('[testing] Running with')==24,profiles=text.count(point_marker)==4,tuning=text.count('[tuning] SMs ')==4)
        else: checks.update(correctness='All correctness tests passed!' in text,profiles=text.count(point_marker)==2,bandwidth=args.api_only or 'Dispatch bandwidth:' in text,hook=args.api_only or 'Dispatch send/recv time:' in text)
        if args.api_only:
            checks['no_gpu_profiler']=text.count('MODE_PARITY_GPU_PROFILE_SKIPPED ')==expected_points and not list(case.glob('gpu_trace_*.json'))
            checks['measurement_boundary']=(case/'measurement_boundary.json').is_file() and (case/'kernel_after_measurements.txt').is_file()
        if args.capture_layout:checks['layout_capture']=(case/'write_layout.jsonl').is_file()
        if getattr(args,'gpu_stages',False):checks['gpu_stages']=(case/'gpu_stages.jsonl').is_file()
        if args.dispatch_focus and args.gpu_stages:
            sys.path.insert(0,str(HERE.parent/'dispatch_diag'))
            from dispatch_checks import validate_gpu
            try:checks['dispatch_coverage']=validate_gpu(case,args.samples,args.gpu_stage_mode)
            except Exception as exc:
                checks['dispatch_coverage']=False
                (case/'dispatch_validation_error.txt').write_text(str(exc))
        if getattr(args,'timeline',False):checks['timeline']=(case/'urma_timeline.jsonl').is_file()
        if args.dispatch_trial:checks['dispatch_trial_samples']=(case/'dispatch_trial_samples.jsonl').is_file()
        if args.backend=='urma':
            checks['native_mode']=f'[URMA] mode={mode} channels={8 if mode=="normal" else 1} endpoints={9 if mode=="normal" else 1} batch=linked' in text
            checks['device']=f': {args.urma_device}' in text
            if args.ll_write_order!='default' or args.normal_write_order!='default':
                expected_order=(args.normal_write_order if args.normal_write_order!='default' else 'relax') if mode=='normal' else args.ll_write_order
                checks['write_order']=text.count('write_order='+expected_order+' atomic_fence=per_request')>=1
            if mode=='normal' and info['runtime'].get('normal_completion') is not None:
                checks['normal_completion']=text.count('normal_completion='+args.normal_completion)>=1
        if args.phase_suite:
            checks.update(phase_files=(case/'phases.jsonl').is_file() and (case/'api_samples.jsonl').is_file(),
                          samples=text.count('MODE_PARITY_SAMPLES ')==(5 if mode=='normal' else (2 if args.ll_hook_workload=='immediate' else 1)))
            if (case/'phases.jsonl').is_file() and args.stage_profile:
                windows=[json.loads(line) for line in (case/'phases.jsonl').read_text().splitlines()]
                checks['stage_activity']=any(r['metrics'].get('sdk_post',{}).get('calls',0)>0
                    for w in windows for r in w['records'])
                if args.normal_completion=='paired':
                    checks['pair_activity']=any(r['metrics'].get('write_cqe_suppressed',{}).get('calls',0)>0
                        for w in windows for r in w['records'])
            if args.save_traces:
                checks['gpu_traces']=all((case/f'gpu_trace_{i}.json').is_file() and
                    (case/f'gpu_trace_{i}.json').stat().st_size>0 for i in range(4 if mode=='normal' else 2))
        if rc==0 and not all(checks.values()): rc=90
        (case/'status.json').write_text(json.dumps(dict(rc=rc,checks=checks),indent=2))
        (case/'counters_after.json').write_text(json.dumps(urma_support.counters(args) if args.backend=='urma' else legacy.counters(args.hca)))
        print(f'CASE_END {mode} rc={rc} checks={checks}',flush=True)
        legacy.pair(args,dict(stage='done',job=args.job,mode=mode,rc=rc))
    print('URMA_MODE_PARITY_ALL_PASSED output='+str(root),flush=True)

if __name__=='__main__': main()
