"""Summarize successful verbs jobs; keep baseline and profiler data separate."""
import argparse
import csv
import json
from pathlib import Path
import statistics
import common


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write(path, rows):
    if not rows:
        path.write_text('')
        return
    fields = sorted({key for row in rows for key in row})
    with path.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(roots, output):
    runs, stages, seen = [], [], set()
    for root in roots:
        setups = list(root.rglob('setup.json'))
        if (root / 'setup.json').is_file():
            setups = [root / 'setup.json']
        for setup_path in setups:
            node = setup_path.parent.resolve()
            if node in seen:
                continue
            seen.add(node)
            setup = read(setup_path)
            if setup['error']:
                raise ValueError('failed setup: ' + str(node))
            meta = setup['fingerprint']
            if meta.get('cpu_diagnostics') or meta.get('suite') in ('verbs_write_micro','verbs_notify_micro'):continue
            host = read(node / 'host.json')
            modes = ['normal', 'low_latency'] if meta['mode'] == 'both' else [meta['mode']]
            expected = []
            if not meta['profile_only']:
                expected += [(f'r{r:02d}_baseline', mode) for r in range(1, meta['repeats'] + 1) for mode in modes]
            if not meta['no_profile']:
                expected += [('gpu_profile', mode) for mode in modes]
            for tag, mode in expected:
                case = node / tag / mode
                status = read(case / 'status.json')
                if status['rc'] or not status['checks'] or not all(status['checks'].values()):
                    raise ValueError('failed case: ' + str(case))
                common.validate_samples(case, mode, meta['samples'])
                config = read(case / 'config.json')
                identity = dict(job=meta['job'], node=node.name, host=host['host'], boot_id=host['boot_id'],
                    variant=meta['variant'], kernel=meta['kernel'], mode=mode,
                    fingerprint=common.digest(meta), profile=config['profile'])
                raw = lines(case / 'api_samples.jsonl')
                for label in sorted({r['label'] for r in raw}):
                    values = [r['us'] for r in raw if r['label'] == label and r['used']]
                    row = dict(identity, tag=tag, label=label, **common.phase_support.distribution(values))
                    runs.append(row)
                if config['profile']:
                    for row in lines(case / 'gpu_stages.jsonl'):
                        for name, value in zip(row['kernel_names'], row['kernel_seconds']):
                            if isinstance(value, list):
                                for index, seconds in enumerate(value):
                                    stages.append(dict(identity, label=row['label'], kernel=name,
                                                       period=index, gpu_us=seconds * 1e6))
                            else:
                                stages.append(dict(identity, label=row['label'], kernel=name, period=0, gpu_us=value * 1e6))
    if not seen:
        raise ValueError('no verbs baseline jobs found')
    # Two ranks are participants, not independent repetitions. Never merge
    # variants, hosts, boots, shapes or benchmark/diagnostic fingerprints.
    grouped = {}
    for row in runs:
        if row['profile']:
            continue
        key = tuple(row[k] for k in ('job', 'node', 'host', 'boot_id', 'variant', 'kernel', 'mode', 'label', 'fingerprint'))
        grouped.setdefault(key, []).append(row)
    summary = []
    for members in grouped.values():
        values = [r['mean_us'] for r in members]
        row = {k:v for k,v in members[0].items() if k in ('job', 'node', 'host', 'boot_id', 'variant', 'kernel', 'mode', 'label', 'fingerprint')}
        row.update(independent_runs=len(values), median_run_mean_us=statistics.median(values),
                   min_run_mean_us=min(values), max_run_mean_us=max(values),
                   cv_run_means=statistics.pstdev(values) / statistics.mean(values))
        summary.append(row)
    output.mkdir(parents=True, exist_ok=True)
    for filename, rows in [('runs.csv', runs), ('baseline_summary.csv', summary), ('gpu_stages.csv', stages)]:
        write(output / filename, rows)
    return runs, summary, stages


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    runs, baseline, stages = summarize(args.roots, args.output)
    print(f'VERBS_SUMMARY_PASSED runs={len(runs)} baseline_groups={len(baseline)} stages={len(stages)} output={args.output}')
