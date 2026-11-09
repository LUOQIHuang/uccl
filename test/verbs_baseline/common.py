"""Provenance and measurement helpers for the two-node verbs baseline."""
import ast
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import types

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.append(str(HERE.parent / 'verbs_diag'))
sys.path.append(str(HERE.parent / 'urma_mode_parity'))
import phase_support
import profile_support

spec = importlib.util.spec_from_file_location('verbs_legacy_driver', HERE.parent / 'verbs_diag/driver.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)

ORIGINAL_SHA = legacy.DEFAULT_SHA
BENCH_REVISION = '41443b64'
FILES = ('test_internode.py', 'test_low_latency.py', 'buffer.py', 'utils.py')
NORMAL_PLAN = [('dispatch_fp8', 32), ('dispatch_bf16', 32), ('combine', 12), ('combine', 16)]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args]).decode('utf-8')


def sources():
    paths = [REPO / 'ep/setup.py']
    for directory in ('ep/src', 'ep/include', 'include'):
        paths.extend(p for p in (REPO / directory).rglob('*')
                     if p.is_file() and p.suffix in ('.h', '.hpp', '.cuh', '.cpp', '.cc', '.cu'))
    return {p.relative_to(REPO).as_posix(): sha(p) for p in sorted(set(paths))}


def diagnostics():
    paths = list(HERE.glob('*.py')) + list(HERE.glob('*.sh'))
    paths += [HERE.parent / 'verbs_diag' / n for n in
              ('driver.py', 'exit_guard.py', 'run.sh', 'profile_support.py')]
    paths += [HERE.parent / 'urma_mode_parity/phase_support.py', REPO / 'ep/bench/utils.py']
    paths += [HERE.parent/'urma_mode_parity'/n for n in ('split_support.py','micro_support.py')]
    paths += [REPO/'ep/tests/verbs_diag_test.cpp',REPO/'ep/tests/verbs_notify_test.cpp',REPO/'ep/tests/urma_support/ep_shim.hpp']
    return {p.relative_to(REPO).as_posix(): sha(p) for p in paths}


def function_source(source, name):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ''.join(source.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])


def prepare(root, variant):
    """Freeze compatible benchmark Python; never alter the user's checkout."""
    revision = git('rev-parse', BENCH_REVISION + '^{commit}').strip() if variant == 'original' else None
    original = {}
    for name in FILES:
        source = git('show', revision + ':ep/bench/' + name) if revision else (REPO / 'ep/bench' / name).read_text()
        original[name] = hashlib.sha256(source.encode()).hexdigest()
        if variant == 'original' and name in legacy.EXPECTED and original[name] != legacy.EXPECTED[name]:
            raise RuntimeError('HISTORICAL_BENCH_HASH_MISMATCH: ' + name)
        if name == 'test_internode.py':
            source = profile_support.prepare_benchmark(source, 'boundaries')
        if name == 'test_low_latency.py':
            source = phase_support.immediate_hook_source(source)
        (root / name).write_text(source)
    # Reuse the trace-export fix without changing historical Buffer/Proxy APIs.
    # The adapter is compiled in the selected utils module's namespace.
    (root / 'profile_adapter.py').write_text(function_source((REPO / 'ep/bench/utils.py').read_text(), 'bench_kineto'))
    return dict(variant=variant, benchmark_revision=revision, input_hashes=original,
                prepared_hashes={p.name: sha(p) for p in root.glob('*.py')},
                changes=['fixed Normal tuning points', 'LL hook immediately invokes receive hook',
                         'profiler trace-export fix; timed callables unchanged'])


def load_extension(module):
    runtime = Path(os.environ.get('MACA_PATH', '/opt/maca-3.7.0')) / 'lib/libmcruntime.so'
    if runtime.is_file():
        ctypes.CDLL(str(runtime), mode=os.RTLD_NOW | os.RTLD_GLOBAL)
    import torch
    package = types.ModuleType('uccl')
    package.__path__ = []
    sys.modules['uccl'] = package
    spec = importlib.util.spec_from_file_location('uccl.ep', str(module))
    extension = importlib.util.module_from_spec(spec)
    sys.modules['uccl.ep'] = extension
    spec.loader.exec_module(extension)
    package.ep = extension
    loaded = sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines()
                     if '.so' in line and '/' in line})
    selected = ('libibverbs.so', 'libmlx5', 'libhns', 'liburma', 'libmcruntime.so')
    library_hashes = {Path(p).name: sha(p) for p in loaded
                      if any(name in Path(p).name for name in selected) and Path(p).is_file()}
    return extension, dict(module=str(module), module_sha=sha(module), python=sys.executable,
                          torch=torch.__version__, torch_file=torch.__file__, loaded_libraries=loaded,
                          selected_library_hashes=library_hashes,
                          verbs_diag_version=extension.get_verbs_diag_version() if hasattr(extension,'get_verbs_diag_version') else 0,
                          verbs_micro=hasattr(extension,'VerbsWriteSession'),
                          verbs_notify_version=extension.get_verbs_notify_version() if hasattr(extension,'get_verbs_notify_version') else 0)


def validate_samples(case, mode, samples):
    rows = [json.loads(line) for line in (case / 'api_samples.jsonl').read_text().splitlines()]
    expected = ['layout'] + [f'{op}_chunk{chunk}' for op, chunk in NORMAL_PLAN] if mode == 'normal' else ['ll_no_hook_dispatch_combine']
    if set(row['label'] for row in rows) != set(expected):
        raise ValueError('unexpected timing labels')
    for label in expected:
        selected = [r for r in rows if r['label'] == label]
        if len(selected) != samples or [r['index'] for r in selected] != list(range(samples)):
            raise ValueError('incomplete timing samples: ' + label)
        if [r['used'] for r in selected] != [False] + [True] * (samples - 1):
            raise ValueError('unexpected dropped samples')
        if any(not isinstance(r['us'], (int, float)) or not 0 < r['us'] < float('inf') for r in selected):
            raise ValueError('invalid timing sample')
    return True


if __name__ == '__main__':
    _, info = load_extension(Path(sys.argv[1]))
    print('VERBS_IMPORT_OK ' + json.dumps(info), flush=True)
