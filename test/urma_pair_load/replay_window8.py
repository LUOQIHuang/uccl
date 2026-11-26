"""Replay the first credit in a full eight-command window from archived CQ returns."""

import argparse
import hashlib
import json
from pathlib import Path
import statistics
import tempfile

import analyze


def replay(root, backend, shape, expected_lanes):
    # Reuse the archive's original identity, path, sample and ordering checks.
    with tempfile.TemporaryDirectory() as temporary:
        analyze.summarize(root, Path(temporary) / 'validated', backend=backend)

    campaign = root / backend / 'cases' / f'{root.name}_{backend}_run'
    rows, sources = [], {}
    for rank in (0, 1):
        path = campaign / f'node{rank}' / 'samples.jsonl'
        sources[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for record in analyze.rows(path):
            if record['shape'] != shape or record['sender'] != rank or not record['used']:
                continue
            if record['lanes'] != expected_lanes or record['limit'] != 4 * expected_lanes:
                raise ValueError('expected four in-flight WRITE/FADD pairs per lane')
            for lane in range(expected_lanes):
                pairs = record['sample']['pairs'][lane::expected_lanes]
                if len(pairs) != 4:
                    raise ValueError('expected eight commands on this lane')
                full = max(pair[key] for pair in pairs for key in ('write_post_end', 'atomic_post_end'))
                completion = [pair[key] for pair in pairs for key in ('write_cqe', 'atomic_cqe')]
                first, head, last = min(completion), pairs[0]['write_cqe'], max(completion)
                if not full <= first <= head <= last:
                    raise ValueError('completion outside window replay')
                rows.append(dict(campaign=root.name, backend=backend, shape=shape, sender=rank, lane=lane,
                    repeat=record['repeat'], index=record['index'],
                    first_cqe_us=(first-full)/1000, head_cqe_us=(head-full)/1000,
                    all_cqe_us=(last-full)/1000, head_extra_over_first_us=(head-first)/1000))
    if not rows:
        raise ValueError('missing full-window samples')
    return rows, sources


def summarize(rows):
    keys = sorted({(r['shape'], r['sender'], r['lane']) for r in rows})
    result = []
    for shape, sender, lane in keys:
        group = [r for r in rows if (r['shape'], r['sender'], r['lane']) == (shape, sender, lane)]
        head = [r['head_cqe_us'] for r in group]
        result.append(dict(shape=shape, sender=sender, lane=lane, samples=len(group),
            head_cqe_median_us=statistics.median(head), head_cqe_p95_us=analyze.percentile(head, .95),
            head_cqe_max_us=max(head), all_cqe_median_us=statistics.median(r['all_cqe_us'] for r in group),
            head_first_or_tied=sum(r['head_extra_over_first_us']==0 for r in group)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single', type=Path, required=True)
    parser.add_argument('--triple', type=Path, required=True)
    parser.add_argument('--backend', choices=('urma', 'verbs'), default='urma')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    single, single_sources = replay(args.single, args.backend, 'queue4', 1)
    triple, triple_sources = replay(args.triple, args.backend, 'lane3_w4', 3)
    rows = single + triple
    args.output.mkdir(parents=True, exist_ok=False)
    analyze.write(args.output / 'summary.csv', summarize(rows))
    (args.output / 'context.json').write_text(json.dumps(dict(backend=args.backend,
        sources_sha256={**single_sources, **triple_sources}, lane_observations=len(rows),
        note='Both microbenchmarks send one direction at a time. Per lane: last submit return to observed head CQE. '
             'Ninth command is hypothetical; no real Normal FIFO, credit-retirement timestamp or CQE visibility. '
             'Lane observations in the same iteration are correlated. Baselines come from separate campaigns.'),
             indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
