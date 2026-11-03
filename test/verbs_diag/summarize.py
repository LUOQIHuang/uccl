"""Summarize passed cases from one or both nodes; CPU-only."""
import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import re
import statistics

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    parser.add_argument('--output', type=Path, help='write summaries elsewhere without modifying the input result directory')
    args = parser.parse_args()
    output = args.output or args.results
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    for path in sorted(args.results.rglob('metrics.jsonl')):
        status = json.loads((path.parent/'status.json').read_text())
        if status['rc'] != 0 or not all(status['checks'].values()):
            raise RuntimeError(f'failed case: {path.parent}')
        match = re.fullmatch(r'r(\d+)_(pinned|unpinned)', path.parent.name)
        if not match:
            continue
        values = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if len(values) != 22 or len({(r['op'], r['rdma']) for r in values}) != 22:
            raise RuntimeError(f'incomplete/duplicate metrics: {path}')
        for value in values:
            rows.append(dict(node=path.parent.parent.name, repeat=int(match[1]), affinity=match[2], **value))
    if not rows:
        raise RuntimeError('no completed metrics found')
    with (output/'metrics.csv').open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    groups = defaultdict(dict)
    for row in rows:
        key = (row['node'], row['op'], row['rdma'])
        groups[key].setdefault(row['affinity'], []).append(row)
    summaries = []
    for (node, op, rdma), settings in sorted(groups.items()):
        if set(settings) != {'pinned', 'unpinned'}:
            raise RuntimeError(f'incomplete affinity pair: {(node,op,rdma)}')
        on, off = settings['pinned'], settings['unpinned']
        if {r['repeat'] for r in on} != {r['repeat'] for r in off}:
            raise RuntimeError('unmatched repeat IDs')
        item = dict(node=node, op=op, rdma=rdma, repeats=len(on))
        for metric in ('transmit_us', 'notify_us', 'api_cuda_event_us'):
            pinned = statistics.median(r[metric] for r in on)
            unpinned = statistics.median(r[metric] for r in off)
            item[f'pinned_{metric}'] = pinned
            item[f'unpinned_{metric}'] = unpinned
            item[f'unpinned_change_{metric}_pct'] = 100*(unpinned/pinned-1)
        summaries.append(item)
    with (output/'summary.csv').open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.DictWriter(out, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    for row in summaries:
        if row['rdma'] in (16, 32):
            print(f"{row['node']} {row['op']:14s} chunk={row['rdma']:2d} transmit pinned/off={row['pinned_transmit_us']:.1f}/{row['unpinned_transmit_us']:.1f} us; API-event pinned/off={row['pinned_api_cuda_event_us']:.1f}/{row['unpinned_api_cuda_event_us']:.1f} us")
    print(f'Wrote {output / "metrics.csv"} and summary.csv')

if __name__ == '__main__':
    main()
