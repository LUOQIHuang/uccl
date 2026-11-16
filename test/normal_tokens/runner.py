"""Build/check/run/collect the fixed seven-size Normal comparison campaign."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

import plan

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arguments(argv=None):
    home = Path(os.environ.get('URMA_DIAG_HOME', os.environ.get('VERBS_DIAG_HOME', str(Path.home()))))
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action', choices=('build', 'check', 'run', 'collect', 'plan'), default='check')
    p.add_argument('--backend', choices=('verbs', 'urma'), required=True)
    p.add_argument('--node-rank', type=int, choices=(0, 1), required=True)
    p.add_argument('--job', required=True)
    p.add_argument('--build-dir', type=Path)
    p.add_argument('--output', type=Path, default=home/'metax_dev/normal_tokens_results')
    p.add_argument('--destination', type=Path, default=HERE/'results')
    p.add_argument('--master-addr', default='141.61.17.202')
    p.add_argument('--port', type=int, default=33780)
    p.add_argument('--round', choices=('all', '1', '2', '3'), default='all')
    p.add_argument('--resume', action='store_true', help='skip only fully validated successful cases on the same boot/module')
    p.add_argument('--gid-index', type=int, default=3)
    p.add_argument('--hca', default='rocep40s0f0')
    p.add_argument('--ifname', default='enp33s0')
    p.add_argument('--urma-device', default='udmac0d1e2')
    p.add_argument('--expected-kernel', help='explicit override, recorded in the campaign plan')
    a = p.parse_args(argv)
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', a.job) or not 1024 <= a.port <= 65533 or not 0 <= a.gid_index < 256:
        p.error('invalid job, port or GID index')
    a.build_dir = (a.build_dir or home/('metax_dev/normal_tokens_build/%s_v1' % a.backend)).resolve()
    a.output = a.output.resolve()
    a.destination = a.destination.resolve()
    a.expected_kernel = a.expected_kernel or plan.KERNELS[a.backend]
    return a


def driver_command(a, action, point):
    entry = HERE.parent/('urma_mode_parity/run.sh' if a.backend == 'urma' else 'verbs_baseline/run.sh')
    common = ['bash', str(entry), '--action', action, '--node-rank', str(a.node_rank),
              '--job', point['job'], '--build-dir', str(a.build_dir), '--output', str(a.output/a.job/a.backend/'cases'),
              '--mode', 'normal', '--normal-tokens', str(point['tokens']), '--warmups', str(plan.WARMUPS),
              '--samples', str(plan.SAMPLES), '--master-addr', a.master_addr, '--port', str(a.port),
              '--build-jobs', '1', '--expected-kernel', a.expected_kernel, '--ifname', a.ifname, '--hca', a.hca,
              '--gpu', '0',
              '--reference-profile', 'legacy', '--reference-events', 'build' if action == 'build' else 'paced']
    if a.backend == 'urma':
        common += ['--backend', 'urma', '--urma-device', a.urma_device]
        if action != 'build':
            common += ['--phase-suite', '--api-only', '--dispatch-focus', '--normal-fadd-fence-plan', 'all-on',
                       '--normal-retire-plan', 'ordered', '--normal-completion', 'all',
                       '--normal-window-plan', 'default', '--normal-submit-schedule', 'lane-mixed']
    else:
        common += ['--variant', 'current', '--gid-index', str(a.gid_index)]
        if action != 'build':
            common += ['--no-profile', '--repeats', '1', '--dispatch-focus']
    return common


def execute(command, log):
    log.parent.mkdir(parents=True, exist_ok=True)
    original, number = log, 1
    while log.exists():
        number += 1
        log = original.with_name('%s_%02d%s' % (original.stem, number, original.suffix))
    print('TOKENS_COMMAND ' + json.dumps(command), flush=True)
    with log.open('w') as out:
        process = subprocess.Popen(command, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   universal_newlines=True, bufsize=1)
        try:
            for line in process.stdout:
                out.write(line)
                out.flush()
                print(line, end='', flush=True)
            return process.wait()
        except KeyboardInterrupt:
            # Let the existing driver terminate its own torchrun session.
            try:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)
            raise


def collect_case(a, point, include_build=False):
    source = a.output/a.job/a.backend/'cases'/point['job']
    dest = a.destination/a.job/a.backend/'cases'/point['job']
    command = [sys.executable, str(HERE.parent/'gpu_stage_recovery/collect.py'), str(source),
               '--node-rank', str(a.node_rank), '--destination', str(dest)]
    if include_build:
        command += ['--trace-build', str(a.build_dir)]
    subprocess.run(command, check=True)


def record_plan(a):
    root = a.output/a.job/a.backend
    root.mkdir(parents=True, exist_ok=True)
    data = dict(version=plan.VERSION, job=a.job, backend=a.backend, node_rank=a.node_rank,
                tokens=plan.TOKENS, warmups=plan.WARMUPS, samples=plan.SAMPLES, repeats=plan.REPEATS,
                cases=plan.cases(a.job, a.backend), build_dir=str(a.build_dir), expected_kernel=a.expected_kernel,
                master_addr=a.master_addr, port=a.port, gid_index=a.gid_index, hca=a.hca, ifname=a.ifname,
                urma_device=a.urma_device, cpu_affinity='inherited', capture='inactive-paced',
                script_hashes={n:sha(HERE/n) for n in ('plan.py', 'runner.py', 'run.sh', 'summarize.py')},
                dependency_hashes={n:sha(REPO/n) for n in plan.DEPENDENCIES})
    # JSON normalizes tuples, so compare the serialized form.
    encoded = json.dumps(data, sort_keys=True, indent=2) + '\n'
    path = root/('plan_node%d.json' % a.node_rank)
    if path.exists() and path.read_text() != encoded:
        raise ValueError('CAMPAIGN_PLAN_CHANGED: use a new job; do not mix code/configurations')
    path.write_text(encoded)
    dest = a.destination/a.job/a.backend/path.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != path.read_bytes():
        raise ValueError('COLLECTED_PLAN_CONFLICT: ' + str(dest))
    shutil.copy2(str(path), str(dest))


def validate_existing(a, point):
    # Resume only an already successful point; no deletion, automatic retries
    # or relabelling of failed/incomplete data.
    import summarize
    root = a.output/a.job/a.backend/'cases'/point['job']
    data = summarize.load_case(root, a.node_rank, a.backend, point['tokens'])
    if data['identity']['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        raise ValueError('RESUME_BOOT_CHANGED')
    manifest = json.loads((a.build_dir/'manifest.json').read_text())
    module = a.build_dir/manifest['module']
    if data['runtime']['module_sha'] != manifest['module_sha'] or sha(module) != manifest['module_sha']:
        raise ValueError('RESUME_MODULE_CHANGED')


def main(argv=None):
    a = arguments(argv)
    points = plan.cases(a.job, a.backend)
    if a.round != 'all':
        points = [p for p in points if p['repeat'] == int(a.round)]
    if a.action == 'plan':
        print(json.dumps(dict(backend=a.backend, points=points, warmups=plan.WARMUPS,
                              samples=plan.SAMPLES, build_dir=str(a.build_dir)), indent=2))
        return
    if a.action == 'collect':
        source_plan = a.output/a.job/a.backend/('plan_node%d.json' % a.node_rank)
        if not source_plan.is_file():
            raise ValueError('no existing campaign plan to collect')
        dest_plan = a.destination/a.job/a.backend/source_plan.name
        dest_plan.parent.mkdir(parents=True, exist_ok=True)
        if dest_plan.exists() and dest_plan.read_bytes() != source_plan.read_bytes():
            raise ValueError('COLLECTED_PLAN_CONFLICT')
        shutil.copy2(str(source_plan), str(dest_plan))
        for point in points:
            source = a.output/a.job/a.backend/'cases'/point['job']
            if (source/('node%d' % a.node_rank)).exists() or (source/('console/node%d' % a.node_rank)).exists():
                collect_case(a, point, include_build=point == points[0])
        return
    record_plan(a)
    if a.action in ('build', 'check'):
        point = points[0]
        log = a.output/a.job/a.backend/'cases'/point['job']/('console/node%d/%s.log' % (a.node_rank, a.action))
        rc = execute(driver_command(a, a.action, point), log)
        collect_case(a, point, include_build=True)
        if rc:
            raise SystemExit(rc)
        print('NORMAL_TOKENS_%s_PASSED' % a.action.upper())
        return
    for point in points:
        source = a.output/a.job/a.backend/'cases'/point['job']
        if (source/('node%d' % a.node_rank)).exists():
            if not a.resume:
                raise ValueError('RESULT_EXISTS: use --resume for validated successes or a new job')
            validate_existing(a, point)
            collect_case(a, point)
            print('TOKENS_RESUME_VALIDATED ' + point['job'], flush=True)
            continue
        console = source/('console/node%d' % a.node_rank)
        rc = 0
        try:
            rc = execute(driver_command(a, 'check', point), console/'check.log')
            if not rc:
                print('TOKENS_POINT_BEGIN '+point['job'], flush=True)
                rc = execute(driver_command(a, 'run', point), console/'run.log')
        finally:
            collect_case(a, point)
        if rc:
            raise SystemExit(rc)
        print('TOKENS_POINT_PASSED '+point['job'], flush=True)
    print('NORMAL_TOKENS_RUN_PASSED backend=%s rank=%d round=%s' % (a.backend, a.node_rank, a.round))


if __name__ == '__main__':
    main()
