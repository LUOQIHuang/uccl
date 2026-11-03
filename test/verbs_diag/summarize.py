"""Summarize passed cases from one or both nodes; CPU-only."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import re
import statistics
import profile_support

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path, help='write summaries elsewhere without modifying the input result directory')
    args = parser.parse_args()
    output = args.output or args.results
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    expected_affinities = {}
    for path in sorted(args.results.rglob('metrics.jsonl')):
        status = json.loads((path.parent/'status.json').read_text())
        if status['rc'] != 0 or not all(status['checks'].values()):
            raise RuntimeError(f'failed case: {path.parent}')
        match = re.fullmatch(r'r(\d+)_(pinned|unpinned)', path.parent.name)
        if not match:
            continue
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        setup = json.loads((path.parent.parent/'setup.json').read_text())
        mode = setup['fingerprint'].get('affinity_mode','both')
        expected_affinities[path.parent.parent.name] = {'pinned','unpinned'} if mode=='both' else {mode}
        expected = profile_support.plan(setup['fingerprint'].get('suite','scan'))
        if len(values) != len(expected) or {(r['op'], r['rdma']) for r in values} != set(expected):
            raise RuntimeError(f'incomplete/duplicate metrics: {path}')
        for value in values:
            value.setdefault('cpu_profile',False)
            value.setdefault('profiler_num_tests',30)
            rows.append(dict(node=path.parent.parent.name, repeat=int(match[1]), affinity=match[2], **value))
    if not rows:
        raise RuntimeError('no completed metrics found')
    with (output/'metrics.csv').open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = defaultdict(dict)
    for row in rows:
        key = (row['node'], row['op'], row['rdma'], row['cpu_profile'])
        groups[key].setdefault(row['affinity'], []).append(row)
    summaries = []
    for (node, op, rdma, sampled), settings in sorted(groups.items()):
        if set(settings)!=expected_affinities[node]:
            raise RuntimeError(f'incomplete affinity cases: {(node,op,rdma)}')
        on, off = settings.get('pinned',[]), settings.get('unpinned',[])
        if on and off and {r['repeat'] for r in on} != {r['repeat'] for r in off}:
            raise RuntimeError('unmatched repeat IDs')
        item = dict(node=node, op=op, rdma=rdma, cpu_profile=sampled, repeats=len(on or off))
        for metric in ('transmit_us', 'notify_us', 'api_cuda_event_us'):
            pinned = statistics.median(r[metric] for r in on) if on else None
            unpinned = statistics.median(r[metric] for r in off) if off else None
            item[f'pinned_{metric}'] = pinned
            item[f'unpinned_{metric}'] = unpinned
            item[f'unpinned_change_{metric}_pct'] = 100*(unpinned/pinned-1) if on and off and pinned else None
        summaries.append(item)
    with (output/'summary.csv').open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    for row in summaries:
        if row['rdma'] in (16, 32):
            print(f"{row['node']} {row['op']:14s} chunk={row['rdma']:2d} transmit pinned/off={row['pinned_transmit_us']}/{row['unpinned_transmit_us']} us; API-event pinned/off={row['pinned_api_cuda_event_us']}/{row['unpinned_api_cuda_event_us']} us; CPU-profile={row['cpu_profile']}")
    print(f'Wrote {output / "metrics.csv"} and summary.csv')

if __name__ == '__main__':
    main()
