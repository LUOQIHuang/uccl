"""Copy selected diagnostics into this checkout for an explicit Git commit."""
import argparse
import os
from pathlib import Path
import re
import shutil

ROOT_FILES = {'setup.json', 'host.json', 'lscpu.txt', 'ibv_devinfo.txt', 'test_internode_diag.py', 'metrics.csv', 'summary.csv', 'urma_preflight.json', 'perf_capability.json'}
CASE_FILES = {'bench.log', 'status.json', 'metrics.jsonl', 'affinity.jsonl', 'gpu_before.txt', 'counters_before.json', 'counters_after.json', 'preflight_error.txt', 'runtime.json', 'proxy_starts.jsonl', 'urma_preflight.json', 'perf.report.txt', 'perf.callgraph.txt', 'perf.stderr.log', 'perf.status.json'}

def collect(source, destination):
    if not source.is_dir():
        raise ValueError(f'RESULTS_MISSING: {source}')
    selected = [p for p in source.iterdir() if p.is_file() and p.name in ROOT_FILES]
    generated_utils = source/'diagnostic_utils/utils.py'
    if generated_utils.is_file():
        selected.append(generated_utils)
    for case in source.iterdir():
        if case.is_dir() and re.fullmatch(r'r\d+_(?:pinned|unpinned)', case.name):
            selected.extend(p for p in case.iterdir() if p.is_file() and p.name in CASE_FILES)
    if not selected:
        raise ValueError('no recognized result files; check the node directory')
    # Check the whole collection before writing. Repeating an identical
    # collection is allowed; a reused job must not overwrite earlier results.
    for path in selected:
        target = destination/path.relative_to(source)
        if target.exists() and target.read_bytes() != path.read_bytes():
            raise ValueError(f'RESULT_CONFLICT: {target}; use a new job name')
    for path in selected:
        target = destination/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return len(selected)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('verbs','urma'), default='verbs')
    parser.add_argument('--job', required=True)
    parser.add_argument('--node-rank', type=int, choices=(0,1), required=True)
    parser.add_argument('--source', type=Path, help='override the local node result directory')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', args.job):
        parser.error('invalid job name')
    task_root = Path(os.environ.get('VERBS_DIAG_HOME', str(Path.home()))).expanduser()
    dev_root = Path(os.environ.get('VERBS_DIAG_DEV_ROOT', str(task_root/'metax_dev'))).expanduser()
    source = (args.source or dev_root/f'{args.backend}_diag_results'/args.job/f'node{args.node_rank}').resolve()
    repository = Path(__file__).resolve().parents[2]
    relative = Path('test/bench_results')/f'{args.backend}_diag'/args.job/f'node{args.node_rank}'
    destination = repository/relative
    count = collect(source, destination)
    print(f'COLLECTED={count} DESTINATION={destination}')
    print('No commit or push was performed. Review files, then:')
    print(f'git add -- "{relative.as_posix()}"')

if __name__ == '__main__':
    main()
