#!/usr/bin/env python3
"""Linux baseline runner. Standard library only; all child processes bounded."""
import argparse
import csv
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import time
import traceback

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BAD = re.compile(r'\b(?:FAIL(?:ED)?|TIMEOUT|STALL|NON-DETERMINISM)\b|Traceback|AssertionError|NON-SUCCESS|POLL_ERR|[A-Z_]+_FAIL')
METRIC = re.compile(r'^(WRITE-latency|FADD-latency|WRITE-bw|FADD-throughput|BARRIER-latency|QUIET-latency|BARRIER-perf|QUIET-perf)\b')


def verdict(client, server, codes, require_checks=True):
    errors = []
    if any(x != 0 for x in codes):
        errors.append('nonzero exit or timeout: ' + str(codes))
    errors.extend(line for line in (client + '\n' + server).splitlines() if BAD.search(line))
    if require_checks:
        for marker in ('ALL_OK', 'WRITE-verify       : PASS',
                       'FADD-oldval seq    : PASS', 'FADD-oldval pipe   : PASS'):
            if marker not in client:
                errors.append('missing client marker: ' + marker)
    return errors


def stop(p):
    if p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()


class Runner:
    def __init__(self, args):
        self.a = args
        self.out = Path(args.output).resolve()
        self.out.mkdir(parents=True, exist_ok=False)
        self.records = []
        self.metrics = []
        self.children = []

    def spawn(self, cmd, log, env=None, cwd=None):
        with (self.out / log).open('w') as f:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 env=env, cwd=cwd, start_new_session=True)
        self.children.append(p)
        return p

    def command(self, name, cmd, env=None, cwd=None, timeout=None):
        start = time.monotonic()
        record = dict(name=name, command=cmd, cwd=str(cwd or HERE), log=name + '.log')
        try:
            p = self.spawn(cmd, record['log'], env, cwd or HERE)
            try:
                code = p.wait(timeout=timeout or self.a.timeout)
            except subprocess.TimeoutExpired:
                stop(p)
                code = 124
        except OSError as exc:
            (self.out / record['log']).write_text(str(exc))
            code = 127
        record.update(exit_code=code, seconds=time.monotonic() - start)
        self.records.append(record)
        return record

    def environment(self):
        env = {k: v for k, v in os.environ.items() if k.startswith(
            ('UCCL_', 'URMA_', 'IBV_', 'USE_', 'MACA_', 'CUCC_', 'CUDA_', 'NCCL_'))
            or k in ('PATH', 'LD_LIBRARY_PATH', 'UMDK_HOME', 'CC', 'CXX', 'MAX_JOBS', 'TORCH_CUDA_ARCH_LIST')}
        (self.out / 'config.json').write_text(json.dumps(dict(args=vars(self.a), env=env), indent=2))
        probes = {
            'system': ['uname', '-a'], 'cpu': ['lscpu'], 'numa': ['numactl', '--hardware'],
            'network': ['ip', '-details', 'address'], 'rdma': ['rdma', 'link'],
            'verbs-devices': ['ibv_devinfo'], 'urma-devices': ['urma_admin', 'show'],
            'gpu': ['nvidia-smi', '-q'], 'gpu-topology': ['nvidia-smi', 'topo', '-m'],
            'metax': ['mx-smi'], 'memory': ['sh', '-c', 'ulimit -a; cat /proc/meminfo'],
            'kernel': ['dmesg', '--ctime'], 'compiler': [self.a.cc, '--version'],
            'git': ['git', '-C', str(REPO), 'rev-parse', 'HEAD'],
            'git-diff': ['git', '-C', str(REPO), 'diff', '--stat'],
        }
        for name, cmd in probes.items():
            record = self.command('env-' + name, cmd, timeout=15)
            record['diagnostic_only'] = True
        hashes = {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in HERE.rglob('*') if p.is_file() and p.suffix in ('.py', '.c', '.h', '.sh', '.md')
                  and self.out not in p.parents}
        (self.out / 'sources.json').write_text(json.dumps(hashes, indent=2))

    def build_micro(self, backend):
        flags = shlex.split(os.getenv('CFLAGS', ''))
        includes, libs = [], []
        if backend == 'urma':
            prefix = os.getenv('UMDK_HOME')
            inc = os.getenv('URMA_INCLUDE_DIR') or (str(Path(prefix) / 'include') if prefix else None)
            lib = os.getenv('URMA_LIBRARY_DIR') or (str(Path(prefix) / 'lib64') if prefix else None)
            if inc:
                includes += ['-I' + inc]
            if lib:
                libs += ['-L' + lib, '-Wl,-rpath,' + lib]
        cmd = [self.a.cc, '-O3', '-g', '-std=gnu11', '-Wall', '-Wextra', *flags,
               *includes, str(HERE / (backend + '_bench.c')), '-o',
               str(self.out / backend), *libs, '-l' + ('urma' if backend == 'urma' else 'ibverbs')]
        rec = self.command('build-' + backend, cmd)
        if rec['exit_code'] == 0:
            self.command('ldd-' + backend, ['ldd', str(self.out / backend)])['diagnostic_only'] = True
        return rec['exit_code'] == 0

    def micro(self):
        built = {b: self.build_micro(b) for b in self.a.backends}
        index = 0
        for rep in range(self.a.repeats):
            # Alternate order to reduce systematic thermal/order bias.
            for b in (self.a.backends if rep % 2 == 0 else list(reversed(self.a.backends))):
                for size in self.a.sizes:
                    for window in self.a.windows:
                        index += 1
                        if not built[b]:
                            continue
                        label = f'{b}-r{rep}-s{size}-w{window}'
                        env = os.environ.copy()
                        # Remove inherited experiment toggles; both peers use identical methodology.
                        for k in list(env):
                            if k.startswith(('URMA_', 'IBV_')) and k not in ('URMA_DEV_NAME', 'URMA_INCLUDE_DIR', 'URMA_LIBRARY_DIR', 'IBV_DEV_NAME', 'IBV_GID_IDX'):
                                del env[k]
                        env.update(BASELINE_PEER=self.a.peer, BASELINE_PORT=str(self.a.port + index))
                        for prefix in ('URMA', 'IBV'):
                            env.update({prefix + '_BW_SIZE': str(size), prefix + '_BW_WINDOW': str(window),
                                        prefix + '_BW_ITERS': str(self.a.iters), prefix + '_FA_WINDOW': str(min(window, 128))})
                        cmd = [str(self.out / b)]
                        srv, cli = '', ''
                        codes = []
                        started = time.monotonic()
                        server = None
                        if self.a.mode in ('local', 'server'):
                            server = self.spawn(cmd + ['-s'], label + '-server.log', env)
                        if self.a.mode == 'local':
                            while time.monotonic() - started < min(60, self.a.timeout):
                                if 'BASELINE_LISTEN' in (self.out / (label + '-server.log')).read_text(errors='replace') or server.poll() is not None:
                                    break
                                time.sleep(.05)
                        if self.a.mode in ('local', 'client'):
                            rec = self.command(label + '-client', cmd, env)
                            codes.append(rec['exit_code'])
                            cli = (self.out / rec['log']).read_text(errors='replace')
                        if server is not None:
                            try:
                                code = server.wait(timeout=self.a.timeout)
                            except subprocess.TimeoutExpired:
                                stop(server)
                                code = 124
                            codes.append(code)
                            srv = (self.out / (label + '-server.log')).read_text(errors='replace')
                        errors = verdict(cli, srv, codes, self.a.mode != 'server')
                        rec = dict(name=label, backend=b, repeat=rep, size=size, window=window,
                                   port=self.a.port + index, env_overrides={k: v for k, v in env.items() if k.startswith(('BASELINE_', 'IBV_', 'URMA_'))},
                                   exit_code=1 if errors else 0, errors=errors, seconds=time.monotonic() - started)
                        self.records.append(rec)
                        for line in cli.splitlines():
                            if METRIC.match(line):
                                self.metrics.append(dict(case=label, backend=b, repeat=rep, size=size, window=window,
                                                         valid=not errors, metric=line.split(':')[0].strip(), raw=line))
                        print(label, 'FAIL' if errors else 'PASS', flush=True)

    def ep(self):
        env = os.environ.copy()
        env.update(USE_URMA='1', PYTHONUNBUFFERED='1', PYTHONFAULTHANDLER='1')
        # Build in place and stage the extension under a private uccl package.
        root = Path(self.a.repo).resolve()
        rec = self.command('build-ep', [sys.executable, 'setup.py', 'build_ext', '--inplace'], env, root / 'ep', self.a.build_timeout)
        if rec['exit_code']:
            return
        extensions = list((root / 'ep').glob('ep*.so'))
        if len(extensions) != 1:
            raise RuntimeError(f'expected exactly one built ep*.so, found {extensions}')
        package = self.out / 'python' / 'uccl'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        shutil.copy2(extensions[0], package / extensions[0].name)
        env['PYTHONPATH'] = str(package.parent) + os.pathsep + env.get('PYTHONPATH', '')
        rec = self.command('ep-import', [sys.executable, '-c',
            'import torch; from uccl import ep; print(torch.__version__); print(ep.__file__)'], env)
        if rec['exit_code']:
            return
        for rep in range(self.a.repeats):
            for b in (self.a.backends if rep % 2 == 0 else list(reversed(self.a.backends))):
                env['UCCL_EP_TRANSPORT'] = b
                for mode in ('normal', 'll'):
                    name = f'ep-{b}-{mode}-r{rep}'
                    cmd = [sys.executable, '-m', 'torch.distributed.run', '--nnodes', str(self.a.nnodes),
                           '--nproc-per-node', str(self.a.gpus), '--node-rank', str(self.a.node_rank),
                           '--master-addr', self.a.master_addr, '--master-port', str(self.a.port),
                           str(root / 'ep/bench' / ('test_internode.py' if mode == 'normal' else 'test_low_latency.py')),
                           '--num-tokens', str(self.a.tokens if mode == 'normal' else self.a.ll_tokens),
                           '--hidden', str(self.a.hidden), '--num-topk', str(self.a.topk), '--num-experts', str(self.a.experts)]
                    rec = self.command(name, cmd, env, root / 'ep/bench')
                    log = (self.out / rec['log']).read_text(errors='replace')
                    rec.update(backend=b, mode=mode, repeat=rep,
                               errors=verdict(log, '', [rec['exit_code']], False))
                    if rec['errors']:
                        rec['exit_code'] = 1
                    if rec['exit_code']:
                        # Distributed ranks cannot safely proceed independently after a failure.
                        return

    def finish(self, fatal=None):
        for p in self.children:
            stop(p)
        if fatal:
            (self.out / 'exception.log').write_text(fatal)
        (self.out / 'results.json').write_text(json.dumps(self.records, indent=2))
        with (self.out / 'metrics.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['case', 'backend', 'repeat', 'size', 'window', 'valid', 'metric', 'raw'])
            w.writeheader()
            w.writerows(self.metrics)
        failures = [r for r in self.records if r['exit_code'] and not r.get('diagnostic_only')]
        lines = ['# Baseline report', '', 'Status: ' + ('FAIL' if fatal or failures else 'PASS'),
                 '', 'Micro latency = local post-to-completion, NOT network RTT. Local mode = same-host loopback.',
                 'EP uses production GPU kernels and Proxy; micro uses host buffers and raw transport APIs.',
                 'Server-only PASS is provisional: combine with client report. Missing optional diagnostic tools are recorded.', '']
        for r in failures:
            lines += ['## ' + r['name'], '```', json.dumps(r, indent=2), '```']
        if fatal:
            lines += ['## Runner exception', '```', fatal, '```']
        lines += ['', '## Raw measurements', 'See metrics.csv and per-process logs; invalid rows must not be used in comparisons.',
                  'Compare matching size/window/iterations and physical GPU/NIC topology; repeat median and p99 separately.']
        groups = {}
        for row in self.metrics:
            if not row['valid']:
                continue
            raw = row['raw'].split(':', 1)[-1]
            values = {k: float(v) for k, v in re.findall(r'(p50|p99|mean)=([0-9.]+)', raw)}
            if not values:
                match = re.search(r'([0-9.]+)\s+(GB/s|Mops/s|us)\b', raw)
                if match:
                    values[match[2]] = float(match[1])
            for field, value in values.items():
                key = (row['size'], row['window'], row['metric'], field)
                groups.setdefault(key, {}).setdefault(row['backend'], []).append(value)
        lines += ['', '## Median comparison (valid repetitions only)', '',
                  '| size | window | measurement | field | verbs median (n) | URMA median (n) | URMA / verbs |',
                  '|---|---|---|---|---|---|---|']
        for key, values in sorted(groups.items()):
            cells = []
            for backend in ('verbs', 'urma'):
                samples = values.get(backend, [])
                cells.append(f'{statistics.median(samples):.4g} ({len(samples)})' if samples else 'missing')
            ratio = 'n/a'
            if values.get('verbs') and values.get('urma') and statistics.median(values['verbs']):
                ratio = f"{statistics.median(values['urma']) / statistics.median(values['verbs']):.4g}"
            lines.append('| ' + ' | '.join(map(str, [*key, *cells, ratio])) + ' |')
        lines += ['', 'Latency ratios <1 favor URMA; throughput ratios >1 favor URMA. Local/server-only runs do not establish cross-node EP performance.']
        (self.out / 'REPORT.md').write_text('\n'.join(lines))
        archive = str(self.out) + '.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(self.out, arcname=self.out.name)
        print('Report:', self.out / 'REPORT.md', '\nDebug bundle:', archive, flush=True)
        return 1 if fatal or failures else 0


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite', choices=['micro', 'ep'], default='micro')
    p.add_argument('--mode', choices=['local', 'server', 'client'], default='local')
    p.add_argument('--peer', default='127.0.0.1', type=lambda x: str(ipaddress.IPv4Address(x)))
    p.add_argument('--backends', nargs='+', choices=['verbs', 'urma'], default=['verbs', 'urma'])
    p.add_argument('--sizes', nargs='+', type=int, default=[8, 4096, 32768])
    p.add_argument('--windows', nargs='+', type=int, default=[1, 32])
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--iters', type=int, default=8192)
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--build-timeout', type=int, default=1800)
    p.add_argument('--port', type=int, default=18716)
    p.add_argument('--cc', default=os.getenv('CC', 'cc'))
    p.add_argument('--output', default='results-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    p.add_argument('--repo', default=str(REPO))
    p.add_argument('--nnodes', type=int, default=2)
    p.add_argument('--node-rank', type=int, default=0)
    p.add_argument('--master-addr', default='127.0.0.1')
    p.add_argument('--gpus', type=int, default=1)
    p.add_argument('--tokens', type=int, default=4096)
    p.add_argument('--ll-tokens', type=int, default=128)
    p.add_argument('--hidden', type=int, default=7168)
    p.add_argument('--topk', type=int, default=8)
    p.add_argument('--experts', type=int, default=32)
    a = p.parse_args(argv)
    if min(a.repeats, a.iters, a.timeout, a.build_timeout, a.gpus, a.tokens, a.ll_tokens, a.topk, a.experts) <= 0:
        p.error('counts/timeouts must be positive')
    if any(x < 1 or x > 32 for x in a.windows) or any(x < 1 or x > 32768 for x in a.sizes):
        p.error('windows must be 1..32 and sizes 1..32768 (allocated payload bounds)')
    if len(set(a.backends)) != len(a.backends):
        p.error('duplicate backends')
    if not 1024 <= a.port <= 65535 - a.repeats * len(a.backends) * len(a.sizes) * len(a.windows):
        p.error('port range exceeds 1024..65535')
    if a.suite == 'ep' and (a.nnodes < 2 or not 0 <= a.node_rank < a.nnodes or a.experts % (a.nnodes * a.gpus) or a.experts < a.topk):
        p.error('EP needs >=2 nodes, valid rank, experts divisible by world size and >= topk')
    return a


def main():
    a = parse()
    runner = Runner(a)
    fatal = None
    try:
        if not sys.platform.startswith('linux'):
            raise RuntimeError('Run on Linux with the target verbs/URMA drivers and SDKs')
        runner.environment()
        runner.micro() if a.suite == 'micro' else runner.ep()
    except BaseException:
        fatal = traceback.format_exc()
    return runner.finish(fatal)


if __name__ == '__main__':
    sys.exit(main())
