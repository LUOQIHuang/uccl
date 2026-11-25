"""Count actual Normal data posts and non-network retired commands offline.

This inventory does not accept timing controls or compare backend performance.
UINT64_MAX internal transport requests are excluded from data-op counts.
"""
import argparse
import collections
import csv
import hashlib
import json
from pathlib import Path


def inventory(verbs, urma, output):
    rows, sources = [], {}
    for backend, root, atomic_opcode in (('verbs', verbs, 6), ('urma', urma, 34)):
        for rank in (0, 1):
            case = 'r01_baseline/normal' if backend == 'verbs' else 'normal'
            path = root / ('node%d' % rank) / case / 'event_trace.jsonl'
            sources[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            indices = []
            for line in path.open(encoding='utf-8'):
                window = json.loads(line)
                if window['label'] != 'dispatch_bf16_chunk32' or window['index'] == 0:
                    continue
                if window['cpu'].get('dropped', 0):
                    raise ValueError('CPU trace dropped records')
                indices.append(window['index'])
                groups = collections.defaultdict(lambda: collections.defaultdict(list))
                for event in window['cpu']['events']:
                    if event['wr_id'] != 2**64 - 1:
                        groups[event['proxy'], event['wr_id']][event['stage']].append(event)
                logical, physical, fences = collections.Counter(), collections.Counter(), collections.Counter()
                local = 0
                for stages in groups.values():
                    posts = stages.get('post_begin', [])
                    if posts:
                        codes = {e['value'] for e in posts}
                        if len(codes) != 1 or not codes <= {0, atomic_opcode}:
                            raise ValueError('unexpected/mixed data opcode')
                        code = next(iter(codes))
                        logical[code] += 1
                        physical[code] += len(posts)
                    elif stages.get('collect') and stages.get('ack_slot'):
                        local += 1
                    fences.update(e['value'] for e in stages.get('atomic_fence', []))
                rows.append(dict(backend=backend, node=rank, index=window['index'],
                    logical_write=logical[0], logical_fadd=logical[atomic_opcode],
                    physical_write=physical[0], physical_fadd=physical[atomic_opcode],
                    collected_retired_without_data_post=local,
                    atomic_fence_on_records=fences[1], atomic_fence_off_records=fences[0]))
            if sorted(indices) != list(range(1, 10)):
                raise ValueError('expected all nine retained BF16 windows')
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'operation_counts.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / 'context.json').write_text(json.dumps(dict(
        sources_sha256=sources, performance_comparison=False,
        note='Counts only. Historical verbs and current URMA captures are not '
        'a newly matched timing comparison. Internal UINT64_MAX transport posts '
        'excluded. Opcodes: verbs WRITE=0 FADD=6; private URMA WRITE=0 FADD=34, '
        'as observed in these archived builds. Local unposted/retired commands '
        'are counted without individually tagging QUIET versus BARRIER. '
        'Verbs trace has no atomic_fence event: zero records do not assert a '
        'measured SDK fence value. No dropped sample or outlier exclusion beyond '
        'the original index0 warm sample.'), indent=2), encoding='utf-8')
    print('NORMAL_OPERATION_INVENTORY_WRITTEN rows=%d timing_comparison=False' % len(rows))
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('verbs', 'urma', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inventory(args.verbs, args.urma, args.output)
