"""Check/build/run/collect the fixed Normal and LL verbs baseline suite."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import common

HERE, REPO, legacy = common.HERE, common.REPO, common.legacy
BUILD_FLAGS = dict(USE_METAX='1', USE_URMA='0', USE_URMA_METAX='0', USE_LIBFABRIC_CXI='0',
                   USE_DMABUF='1', MAX_NUM_GPUS='1', NUM_MAX_NVL_PEERS='1', PER_EXPERT_BATCHING='0')

def build_flags(args):
    return dict(BUILD_FLAGS, UCCL_GPU_STAGE_TRACE=str(int(bool(getattr(args,'gpu_stage_build',False) or getattr(args,'gpu_stages',False)))))


def parser():
    dev = Path(os.environ.get('VERBS_DIAG_DEV_ROOT', str(Path(os.environ.get('VERBS_DIAG_HOME', str(Path.home()))) / 'metax_dev')))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action', choices=('check', 'run', 'collect', 'build','micro'), default='check')
    p.add_argument('--micro-repeat',type=int,choices=(1,2,3),default=1)
    p.add_argument('--micro-suite',choices=('write','notify'),default='write')
    p.add_argument('--timeline',action='store_true')
    p.add_argument('--stage-suite',action='store_true')
    p.add_argument('--gpu-stages',action='store_true')
    p.add_argument('--gpu-stage-build',action='store_true')
    p.add_argument('--notify-check',action='store_true')
    p.add_argument('--micro-layout',type=Path)
    p.add_argument('--micro-threads',type=int,choices=(1,4),default=4)
    p.add_argument('--micro-cpus',default='auto')
    p.add_argument('--gpu-pci',default='0000:a1:00.0')
    p.add_argument('--variant', choices=('original', 'current'), default='original')
    p.add_argument('--module', type=Path, default=dev / 'urma_after_kernel_20261029/diag/ep_host_atomic_4ed19a6b/module/ep.abi3.so')
    p.add_argument('--build-dir', type=Path, default=dev / 'verbs_baseline_build/current_v1')
    p.add_argument('--build-jobs', type=int, default=4)
    p.add_argument('--job', required=True)
    p.add_argument('--node-rank', type=int, choices=(0, 1), required=True)
    p.add_argument('--master-addr', default='141.61.17.202')
    p.add_argument('--port', type=int, default=31380, help='control port; torchrun uses port+1')
    p.add_argument('--output', type=Path, default=dev / 'verbs_baseline_results')
    p.add_argument('--mode', choices=('both', 'normal', 'low_latency'), default='both')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--warmups', type=int, default=50)
    p.add_argument('--cpu-diagnostics',action='store_true')
    p.add_argument('--campaign-check',action='store_true')
    p.add_argument('--gid-index',type=int)
    p.add_argument('--samples', type=int, default=50)
    profile = p.add_mutually_exclusive_group()
    profile.add_argument('--no-profile', action='store_true')
    profile.add_argument('--profile-only', action='store_true')
    p.add_argument('--hca', default='rocep40s0f0')
    p.add_argument('--ifname', default='enp33s0')
    p.add_argument('--gpu', default='0')
    p.add_argument('--expected-kernel', help='optional exact kernel check; never checks URMA hardware')
    p.add_argument('--case-timeout', type=int, default=1800)
    args = p.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.job):
        p.error('invalid --job')
    if not 1 <= args.repeats <= 10 or args.warmups < 1 or args.samples < 3 or not 1024 <= args.port <= 65533 or args.build_jobs < 1 or args.case_timeout < 1:
        p.error('invalid repeats, warmups, samples, port, build jobs or timeout')
    if args.action == 'build' and args.variant != 'current':
        p.error('--action build requires --variant current; original uses the previously validated binary')
    args.backend = 'verbs'
    if getattr(args,'gid_index',None) is not None and not 0<=getattr(args,'gid_index',None)<256:p.error('invalid GID index')
    if getattr(args,'cpu_diagnostics',False) and (args.variant!='current' or args.mode!='normal' or not args.no_profile):p.error('CPU diagnostics requires current Normal --no-profile')
    if args.action=='micro' and (args.variant!='current' or args.mode!='normal' or getattr(args,'gid_index',None) is None or not args.micro_layout):p.error('micro requires current Normal, --gid-index and --micro-layout')
    if args.timeline and not args.cpu_diagnostics:p.error('--timeline requires --cpu-diagnostics')
    if args.stage_suite and (args.variant!='current' or args.mode!='normal' or not args.no_profile or args.repeats!=1):p.error('--stage-suite requires current Normal --no-profile --repeats 1')
    if args.gpu_stages and (not args.stage_suite or not args.timeline):p.error('--gpu-stages requires --stage-suite --timeline --cpu-diagnostics')
    if args.gpu_stage_build and args.action!='build':p.error('--gpu-stage-build is only for build; use --gpu-stages for checks/runs')
    if args.micro_suite=='notify' and args.action=='micro' and args.micro_threads!=4:p.error('notify suite requires four threads')
    args.output = args.output.resolve()
    args.module = args.module.resolve()
    args.build_dir = args.build_dir.resolve()
    return args


def environment(args):
    env = os.environ.copy()
    # Remove inherited tuning/instrumentation so both sides use the fixed plan.
    for key in list(env):
        if key.startswith(('UCCL_', 'PARITY_', 'DIAG_')) or key in ('LD_PRELOAD', 'PYTHONPATH', 'UMDK_HOME'):
            env.pop(key)
    env.update(UCCL_EP_TRANSPORT='verbs', UCCL_EP_VERBS_REG='dmabuf', UCCL_IB_HCA=args.hca,
               UCCL_EP_DISABLE_CPU_AFFINITY='1', UCCL_RDMA_ADAPTIVE_SLEEP='0',
               CUDA_VISIBLE_DEVICES=args.gpu, MCCL_IB_DISABLE='1', NCCL_IB_DISABLE='1',
               MCCL_SOCKET_IFNAME=args.ifname, NCCL_SOCKET_IFNAME=args.ifname,
               MCCL_DEBUG='INFO', NCCL_DEBUG='INFO', PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
    if args.variant == 'original':
        # The historical validated binary uses this switch for its verbs
        # control-atomic registration path; preserve the earlier baseline.
        env['UCCL_METAX_URMA_HOST_ATOMICS'] = '1'
    if getattr(args,'gid_index',None) is not None:env['UCCL_IB_GID_INDEX']=str(getattr(args,'gid_index',None))
    return env


def build(args):
    before = common.sources()
    flags = build_flags(args)
    manifest_path = args.build_dir / 'manifest.json'
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text())
        if data['sources'] != before or data['flags'] != flags or common.sha(args.build_dir / data['module']) != data['module_sha']:
            raise RuntimeError('BUILD_CHANGED: use a new --build-dir')
        print('VERBS_BUILD_ALREADY_PASSED', flush=True)
        return
    args.build_dir.mkdir(parents=True, exist_ok=True)
    request = dict(sources=before, flags=flags)
    path = args.build_dir / 'request.json'
    if path.exists() and json.loads(path.read_text()) != request:
        raise RuntimeError('BUILD_INPUTS_CHANGED: use a new --build-dir')
    path.write_text(json.dumps(request, indent=2))
    env = environment(args)
    env.update(flags, MAX_JOBS=str(args.build_jobs))
    for key in ('URMA_INCLUDE_DIR','URMA_LIBRARY_DIR'):env.pop(key,None)
    if args.variant=='current':
        with (args.build_dir/'host_tests.log').open('a') as log:
            subprocess.run([sys.executable,str(HERE/'host_tests.py')],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    for key in ('URMA_INCLUDE_DIR', 'URMA_LIBRARY_DIR'):
        env.pop(key, None)
    env.update(flags, MAX_JOBS=str(args.build_jobs))
    command = [sys.executable, str(REPO / 'ep/setup.py'), 'build_ext',
               '--build-lib', str(args.build_dir / 'lib'), '--build-temp', str(args.build_dir / 'objects')]
    print('VERBS_BUILD_STARTED log=' + str(args.build_dir / 'build.log'), flush=True)
    with (args.build_dir / 'build.log').open('a') as log:
        subprocess.run(command, cwd=REPO / 'ep', env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    modules = list((args.build_dir / 'lib').glob('ep*.so'))
    if len(modules) != 1 or common.sources() != before:
        raise RuntimeError('BUILD_OUTPUT_OR_SOURCE_CHANGED')
    data = dict(request, revision=common.git('rev-parse', 'HEAD').strip(),
                module=str(modules[0].relative_to(args.build_dir)), module_sha=common.sha(modules[0]))
    manifest_path.write_text(json.dumps(data, indent=2))
    print('VERBS_BUILD_PASSED module=' + str(modules[0]), flush=True)


def preflight(args):
    if args.expected_kernel and os.uname().release != args.expected_kernel:
        raise RuntimeError('KERNEL_MISMATCH: ' + os.uname().release)
    if args.variant == 'current':
        data = json.loads((args.build_dir / 'manifest.json').read_text())
        module = args.build_dir / data['module']
        if data['sources'] != common.sources() or data['flags'] != build_flags(args) or common.sha(module) != data['module_sha']:
            raise RuntimeError('CURRENT_BUILD_MISMATCH: rebuild in a new directory')
        identity = dict(kind='current_source_build', sources_hash=common.digest(data['sources']), flags=data['flags'])
    else:
        module = args.module
        if not module.is_file() or common.sha(module) != common.ORIGINAL_SHA:
            raise RuntimeError('ORIGINAL_MODULE_MISSING_OR_SHA_MISMATCH: ' + str(module))
        data = dict(module_sha=common.ORIGINAL_SHA, source_revision=None,
                    note='Previously validated historical private binary; exact build source/flags unavailable. Benchmark revision is not binary provenance.')
        identity = dict(kind='historical_validated_binary', module_sha=common.ORIGINAL_SHA)
    probe = subprocess.run([sys.executable, str(HERE / 'common.py'), str(module)], env=environment(args),
                           capture_output=True, text=True, timeout=90)
    if probe.returncode:
        raise RuntimeError('IMPORT_FAILED:\n' + probe.stdout + probe.stderr)
    info = next(json.loads(line.split(' ', 1)[1]) for line in probe.stdout.splitlines() if line.startswith('VERBS_IMPORT_OK '))
    if (getattr(args,'notify_check',False) or getattr(args,'timeline',False) or getattr(args,'micro_suite','write')=='notify') and info.get('verbs_notify_version')!=1:raise RuntimeError('NOTIFICATION_SUITE_REQUIRES_REBUILD')
    if getattr(args,'campaign_check',False) and (info.get('verbs_diag_version')!=1 or not info.get('verbs_micro')):raise RuntimeError('CAMPAIGN_REQUIRES_REBUILD')
    if getattr(args,'cpu_diagnostics',False) and info.get('verbs_diag_version')!=1:raise RuntimeError('VERBS_DIAGNOSTICS_REQUIRES_REBUILD')
    if getattr(args,'gpu_stages',False) and info.get('gpu_stage_version')!=1:raise RuntimeError('GPU_STAGE_REQUIRES_REBUILD')
    if getattr(args,'gid_index',None) is not None:
        port=Path('/sys/class/infiniband')/args.hca/'ports/1'
        if not (port/'gids'/str(getattr(args,'gid_index',None))).read_text().strip().replace(':','').strip('0'):raise RuntimeError('selected GID is zero')
        if 'v2' not in (port/'gid_attrs/types'/str(getattr(args,'gid_index',None))).read_text().lower():raise RuntimeError('campaign requires RoCE v2 GID')
    if args.variant == 'current' and any('/liburma.so' in p for p in info['loaded_libraries']):
        raise RuntimeError('PURE_VERBS_BUILD_LOADED_URMA: inspect runtime library environment')
    return module, data, identity, info, probe.stdout + probe.stderr


def snapshot(root, args, label):
    legacy.capture(root / ('kernel_' + label + '.txt'), ['journalctl', '-b', '-k', '-n', '400', '--no-pager'], timeout=20)
    (root / ('counters_' + label + '.json')).write_text(json.dumps(legacy.counters(args.hca), indent=2))


def check(args):
    module, manifest, identity, info, log = preflight(args)
    with tempfile.TemporaryDirectory(prefix='verbs_check_') as tmp:
        directory = Path(tmp)
        provenance = common.prepare(directory, args.variant)
        legacy.idle_check(directory, args.hca, args)
        print(json.dumps(dict(identity=identity, benchmark=provenance, runtime=info), indent=2))
    print(log)
    print('VERBS_CHECK_PASSED (no GPU benchmark; runtime ABI still requires the real run)', flush=True)


def collect(args):
    source = args.output / args.job / f'node{args.node_rank}'
    target = HERE / 'results' / args.job / f'node{args.node_rank}'
    files = [p for p in source.rglob('*') if p.is_file() and p.suffix in ('.py', '.json', '.jsonl', '.log', '.txt', '.csv')
             and not any(part in ('cache', 'tmpdir', '__pycache__') for part in p.parts)]
    if not files:
        raise RuntimeError('NO_RESULTS_TO_COLLECT')
    for path in files:
        out = target / path.relative_to(source)
        if out.exists() and out.read_bytes() != path.read_bytes():
            raise RuntimeError('RESULT_CONFLICT: ' + str(out))
    for path in files:
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, out)
    print('COLLECTED=' + str(target), flush=True)


def run_case(args, root, tag, mode, profile, module, module_sha):
    case = root / tag / mode
    case.mkdir(parents=True)
    rc, checks, error = 0, {}, None
    try:
        legacy.idle_check(case, args.hca, args)
        snapshot(case, args, 'before')
    except Exception as exc:
        rc, error = 1, str(exc)
        (case / 'preflight_error.txt').write_text(error)
        (case / 'status.json').write_text(json.dumps(dict(rc=rc, checks={}, error=error), indent=2))
        print(f'VERBS_PREFLIGHT_ERROR {tag}/{mode}: {error}', flush=True)
    legacy.pair(args, dict(stage='ready', job=args.job, tag=tag, mode=mode, rc=rc))
    env = environment(args)
    env.update(VB_CASE=str(case), VB_MODULE=str(module), VB_SHA=module_sha, VB_MODE=mode,
               VB_PROFILE=str(int(profile)), VB_WARMUPS=str(args.warmups), VB_SAMPLES=str(args.samples),
               VB_CPU_DIAG=str(int(getattr(args,'cpu_diagnostics',False))), VB_TIMELINE=str(int(getattr(args,'timeline',False))),
               UCCL_TORCHRUN_NODE_RANK=str(args.node_rank), UCCL_EXIT_GUARD='1')
    env.update(VB_GPU_STAGES=str(int(getattr(args,'gpu_stages',False))),VB_STAGE_SUITE=str(int(getattr(args,'stage_suite',False))))
    for key, name in [('TMPDIR', 'tmpdir'), ('XDG_CACHE_HOME', 'cache')]:
        path = case / name
        path.mkdir()
        env[key] = str(path)
    (case / 'config.json').write_text(json.dumps(dict(tag=tag, mode=mode, profile=profile,
        warmups=args.warmups, samples=args.samples, shape=dict(tokens=4096 if mode == 'normal' else 128,
        hidden=7168, experts=256, topk=8), env={k:v for k,v in env.items() if k.startswith(('UCCL_', 'VB_', 'MCCL_', 'NCCL_'))}), indent=2))
    command = [sys.executable, str(HERE.parent / 'verbs_diag/exit_guard.py'), '--nnodes=2', '--nproc_per_node=1',
               f'--node_rank={args.node_rank}', f'--master_addr={args.master_addr}', f'--master_port={args.port + 1}',
               '--no-python', sys.executable, '-u', str(HERE / 'worker.py')]
    print(f'VERBS_CASE_BEGIN {tag}/{mode} log={case / "bench.log"}', flush=True)
    try:
        with (case / 'bench.log').open('w') as log:
            process = subprocess.Popen(command, cwd=root / 'benchmark', env=env, stdout=log,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = process.wait(timeout=args.case_timeout)
            except subprocess.TimeoutExpired:
                legacy.terminate(process)
                rc = 124
            except KeyboardInterrupt:
                legacy.terminate(process)
                raise
        text = (case / 'bench.log').read_text(errors='replace')
        checks = dict(done='VERBS_BASELINE_DONE' in text, mode=f'VERBS_MODE_OK mode={mode}' in text,
            exit_ack=f'UCCL_EXIT_ACK_OK node_rank={args.node_rank}' in text,
            dmabuf='GPU MR registration path=dmabuf requested=dmabuf' in text,
            no_errors=not re.search(r'Traceback \(most recent call last\)|AssertionError|Failed CR status|Segmentation fault|ChildFailedError|\[WARNING\]', text),
            samples=common.validate_samples(case, mode, args.samples))
        checks['correctness'] = text.count('[testing] Running with') == 24 if mode == 'normal' else 'All correctness tests passed!' in text
        if getattr(args,'cpu_diagnostics',False):
            checks['cpu_stages']=(case/'verbs_phases.jsonl').is_file() and (case/'write_layout.jsonl').is_file()
            if checks['cpu_stages']:
                windows=[json.loads(x) for x in (case/'verbs_phases.jsonl').read_text().splitlines() if x.strip()]
                metrics=[record['metrics'] for window in windows for record in window['records']]
                checks['native_write_fadd_samples']=all(sum(m.get(name,{}).get('calls',0) for m in metrics)>0 for name in ('write_requests','fadd_requests','submit_to_observed_cqe_sampled'))
                checks['no_verbs_diag_errors']=not any(m.get(name,{}).get('calls',0) for m in metrics for name in ('verbs_post_errors','verbs_completion_errors','verbs_tracking_overflow'))
                checks['complete_wr_layout']=not any(json.loads(x)['dropped'] for x in (case/'write_layout.jsonl').read_text().splitlines() if x.strip())
        if getattr(args,'timeline',False):checks['timeline']=(case/'verbs_timeline.jsonl').is_file()
        expected = 4 if mode == 'normal' else 2
        checks['profiles'] = text.count('VERBS_GPU_PROFILE ') == expected if profile else text.count('VERBS_PROFILE_SKIPPED ') == expected and (getattr(args,'gpu_stages',False) or not (case / 'gpu_stages.jsonl').exists())
        if getattr(args,'gpu_stages',False):checks['gpu_stages']=(case/'gpu_stages.jsonl').is_file()
        if getattr(args,'stage_suite',False):checks['measurement_boundary']=(case/'measurement_boundary.json').is_file() and (case/'kernel_after_measurements.txt').is_file()
        if profile:
            checks['traces'] = all((case / f'gpu_trace_{i}.json').is_file() and (case / f'gpu_trace_{i}.json').stat().st_size > 0 for i in range(expected))
        if rc == 0 and not all(checks.values()):
            rc = 90
    except Exception as exc:
        rc, error = rc or 91, str(exc)
    finally:
        try:
            snapshot(case, args, 'after')
        except Exception as exc:
            rc, error = rc or 92, str(exc)
        (case / 'status.json').write_text(json.dumps(dict(rc=rc, checks=checks, error=error), indent=2))
    print(f'VERBS_CASE_END {tag}/{mode} rc={rc} checks={checks} error={error}', flush=True)
    legacy.pair(args, dict(stage='done', job=args.job, tag=tag, mode=mode, rc=rc))


def run(args):
    root = args.output / args.job / f'node{args.node_rank}'
    root.mkdir(parents=True, exist_ok=False)
    error, meta = None, {}
    try:
        module, manifest, identity, info, log = preflight(args)
        bundle = root / 'benchmark'
        bundle.mkdir()
        benchmark = common.prepare(bundle, args.variant)
        (root / 'module_manifest.json').write_text(json.dumps(manifest, indent=2))
        (root / 'runtime.json').write_text(json.dumps(info, indent=2))
        (root / 'import.log').write_text(log)
        if getattr(args,'stage_suite',False):shutil.copy2(HERE.parent/'urma_mode_parity/gpu_stage_sites.json',root/'gpu_stage_sites.json')
        legacy.snapshot_host(root, args.hca)
        port = Path('/sys/class/infiniband') / args.hca / 'ports/1'
        port_paths = [p for p in port.glob('*') if p.is_file()]
        for subdir in ('gids', 'gid_attrs/types', 'gid_attrs/ndevs'):
            port_paths.extend((port / subdir).glob('*'))
        port_info = {}
        for path in port_paths:
            try:
                port_info[path.relative_to(port).as_posix()] = path.read_text().strip()
            except OSError:
                continue
        (root / 'rdma_port.json').write_text(json.dumps(port_info, indent=2))
        # Module addresses/paths/boot IDs differ across nodes. Compare selected
        # inputs, source/build identity and kernel; retain local details above.
        meta = dict(job=args.job, variant=args.variant, identity=identity, benchmark=benchmark,
            diagnostics=common.diagnostics(), kernel=os.uname().release, torch=info['torch'],
            libraries=info.get('selected_library_hashes', {}),
            mode=args.mode, repeats=args.repeats, warmups=args.warmups, samples=args.samples,
            no_profile=args.no_profile, profile_only=args.profile_only, hca=args.hca, ifname=args.ifname, gpu=args.gpu)
        meta.update(cpu_diagnostics=getattr(args,'cpu_diagnostics',False),timeline=getattr(args,'timeline',False),gid_index=getattr(args,'gid_index',None))
        meta.update(stage_suite=getattr(args,'stage_suite',False),gpu_stages=getattr(args,'gpu_stages',False))
    except Exception as exc:
        error = str(exc)
    (root / 'setup.json').write_text(json.dumps(dict(fingerprint=meta, error=error), indent=2))
    if error:
        print(f'VERBS_SETUP_ERROR {error}; details={root / "setup.json"}', flush=True)
    legacy.pair(args, dict(stage='setup', meta_hash=common.digest(meta), rc=1 if error else 0))
    if error:
        raise RuntimeError(error)
    modes = ('normal', 'low_latency') if args.mode == 'both' else (args.mode,)
    if getattr(args,'cpu_diagnostics',False):
        run_case(args,root,'cpu_diagnostic','normal',False,module,info['module_sha'])
        print('VERBS_BASELINE_ALL_PASSED output='+str(root),flush=True);return
    if not args.profile_only:
        for repeat in range(1, args.repeats + 1):
            for mode in modes:
                run_case(args, root, f'r{repeat:02d}_baseline', mode, False, module, info['module_sha'])
    if not args.no_profile:
        for mode in modes:
            run_case(args, root, 'gpu_profile', mode, True, module, info['module_sha'])
    print('VERBS_BASELINE_ALL_PASSED output=' + str(root), flush=True)


def main():
    args = parser()
    if args.action=='micro':
        import micro_driver
        micro_driver.run(args,preflight,environment,snapshot);return
    {'build': build, 'check': check, 'collect': collect, 'run': run}[args.action](args)


if __name__ == '__main__':
    main()
