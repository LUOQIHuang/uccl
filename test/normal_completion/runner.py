"""Three-case current-device URMA completion campaign; no semantic changes."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

import overlay

HERE = Path(__file__).resolve().parent
PROVIDER_SHA = '18ea061e598330ba56ac07d7f9b4691414884aaf805c7f7a1973b1bc5c028967'


def command(a, action, name):
    args = ['bash', str(a.tree / 'test/urma_mode_parity/run.sh'), '--backend', 'urma',
            '--mode', 'normal', '--action', action, '--job', name, '--node-rank', str(a.node_rank),
            '--build-dir', str(a.build_dir), '--output', str(a.output / a.job / 'cases'),
            '--reference-profile', 'boundaries', '--warmups', '5', '--samples', '10']
    if action == 'build':
        return args + ['--reference-events', 'build', '--build-jobs', str(a.build_jobs)]
    return args + ['--phase-suite', '--api-only', '--dispatch-focus', '--reference-events', 'paced',
        '--normal-tokens', '4096', '--normal-window-plan', 'default', '--normal-retire-plan', 'ordered',
        '--normal-fadd-fence-plan', 'peer-tail-off', '--require-normal-atomic-roles', '--require-normal-retire',
        '--normal-completion', 'all', '--normal-submit-schedule', 'lane-mixed',
        '--urma-device-plan', '202c1-204c0', '--urma-device', 'udmac1d1e2' if a.node_rank == 0 else 'udmac0d1e2',
        '--ifname', a.ifname, '--gpu-pci', a.gpu_pci, '--atomic-port', 'auto',
        '--master-addr', a.master_addr, '--port', str(a.port), '--case-timeout', '600',
        '--expected-kernel', '6.6.0-gdr_f00955680_diag']


def collect(a, name):
    source = a.output / a.job / 'cases' / name
    if source.exists():
        subprocess.run([sys.executable, str(a.tree / 'test/gpu_stage_recovery/collect.py'), str(source),
            '--node-rank', str(a.node_rank), '--trace-build', str(a.build_dir),
            '--destination', str(a.destination / a.job / 'cases' / name)], check=True)


def run_one(a, suffix, action, capture=False):
    name = a.job + '_' + suffix
    source = a.output / a.job / 'cases' / name
    if action == 'run' and (source / ('node%d' % a.node_rank)).exists():
        raise ValueError('case exists; use a new --job')
    console = source / 'console' / ('node%d' % a.node_rank)
    console.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, FADD_PATH_TAIL_SCOPE='1', FADD_PATH_CAPTURE='1' if capture else '0',
               FADD_PATH_CQ_READY='1' if capture else '0')
    env.pop('UCCL_IB_MAX_INFLIGHT_BYTES', None)
    print('NORMAL_COMPLETION_CASE_BEGIN %s action=%s capture=%s log=%s' % (name, action, capture, console), flush=True)
    try:
        with (console / (action + '.log')).open('w') as log:
            process = subprocess.Popen(command(a, action, name), env=env,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                rc = process.wait(timeout=1800 if action == 'build' else 900)
            except (KeyboardInterrupt, subprocess.TimeoutExpired):
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                raise
        if rc:
            raise RuntimeError('case failed rc=%d; inspect collected %s.log' % (rc, action))
    finally:
        collect(a, name)
    print('NORMAL_COMPLETION_CASE_PASSED ' + name, flush=True)


def main(argv=None):
    home = Path(os.environ.get('URMA_DIAG_HOME', str(Path.home())))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--action', choices=('setup', 'run', 'collect'), required=True)
    parser.add_argument('--node-rank', choices=(0, 1), type=int, required=True)
    parser.add_argument('--job', default='normal_completion_01')
    parser.add_argument('--tree', type=Path, default=home / 'metax_dev/normal_completion_src_v1')
    parser.add_argument('--build-dir', type=Path, default=home / 'metax_dev/normal_completion_build/urma_v1')
    parser.add_argument('--output', type=Path, default=home / 'metax_dev/normal_completion_results')
    parser.add_argument('--destination', type=Path, default=HERE / 'results')
    parser.add_argument('--build-jobs', type=int, default=1)
    parser.add_argument('--master-addr', default='141.61.17.202')
    parser.add_argument('--port', type=int, default=33780)
    parser.add_argument('--ifname')
    parser.add_argument('--gpu-pci', default=os.environ.get('URMA_DIAG_GPU_PCI', '0000:21:00.0'))
    a = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', a.job) or not 1024 <= a.port <= 65533 or not 1 <= a.build_jobs <= 8:
        parser.error('invalid job/port/build-jobs')
    a.ifname = a.ifname or os.environ.get('URMA_DIAG_IFNAME') or ('enp40s0' if a.node_rank == 0 else 'enp34s0f5u1u4')
    for field in ('tree', 'build_dir', 'output', 'destination'):
        setattr(a, field, getattr(a, field).resolve())
    result = a.output / a.job
    dest = a.destination / a.job
    result.mkdir(parents=True, exist_ok=True)
    dest.mkdir(parents=True, exist_ok=True)
    if a.action == 'collect':
        for suffix in ('build', 'check', 'pre', 'probe', 'post'):
            collect(a, a.job + '_' + suffix)
        for path in result.glob('*_node%d.json' % a.node_rank):
            (dest / path.name).write_bytes(path.read_bytes())
        return
    request = {k: str(v) if isinstance(v, Path) else v for k, v in vars(a).items()}
    (dest / ('request_node%d.json' % a.node_rank)).write_text(json.dumps(request, indent=2))
    provider = Path(os.environ.get('URMA_DIAG_LIBRARY_DIR', '/missing')) / 'urma/liburma-udma.so'
    if not provider.is_file() or overlay.sha(provider) != PROVIDER_SHA:
        raise ValueError('COMPLETION_PROVIDER_MISMATCH: expected recorded private provider, ' + str(provider))
    provenance = overlay.prepare(a.tree)
    (dest / ('overlay_node%d.json' % a.node_rank)).write_text(json.dumps(provenance, indent=2))
    marker = result / ('setup_node%d.json' % a.node_rank)
    if a.action == 'setup':
        run_one(a, 'build', 'build')
        run_one(a, 'check', 'check')
        passed = dict(module_sha=json.loads((a.build_dir / 'manifest.json').read_text())['module_sha'],
                      boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(), provider_sha=PROVIDER_SHA,
                      provenance=provenance)
        marker.write_text(json.dumps(passed, indent=2))
        (dest / marker.name).write_bytes(marker.read_bytes())
    else:
        saved = json.loads(marker.read_text())
        if (saved['module_sha'] != json.loads((a.build_dir / 'manifest.json').read_text())['module_sha'] or
                saved['boot'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip() or saved['provenance'] != provenance):
            raise ValueError('setup module/boot/source changed')
        for suffix in ('pre', 'probe', 'post'):
            if ((a.output / a.job / 'cases' / (a.job + '_' + suffix) / ('node%d' % a.node_rank)).exists() or
                    (dest / 'cases' / (a.job + '_' + suffix) / ('node%d' % a.node_rank)).exists()):
                raise ValueError('case exists; use a new --job')
        run_one(a, 'check', 'check')
        for suffix in ('pre', 'probe', 'post'):
            run_one(a, suffix, 'run', suffix == 'probe')
    print('NORMAL_COMPLETION_%s_PASSED node=%d destination=%s' % (a.action.upper(), a.node_rank, dest), flush=True)


if __name__ == '__main__':
    main()
