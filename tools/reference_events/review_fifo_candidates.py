"""Exploratory replay of rejected traces; NEVER accepts a performance workload.

Use raw records to select candidates without weakening audit_boundary_fifo's
control gate. Interval shares describe overlapping software waits, not Dispatch
contributions, physical CQ readiness, or URMA-minus-verbs differences.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import statistics

import audit_boundary_fifo as fifo


def review(trace, output, label='dispatch_bf16_chunk32'):
    observations, segments, heads, sources = [], [], [], {}
    seen_windows = collections.defaultdict(list)
    for rank in (0, 1):
        path = trace / ('node%d' % rank) / 'normal/event_trace.jsonl'
        sources[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for window in fifo.boundaries.matched.base.rows(path):
            if window['label'] != label or window['index'] == 0:
                continue
            seen_windows[rank].append(window['index'])
            groups, rings = fifo.replay(window)
            gpu = fifo.boundaries.channel_records(window)
            origin = window['cpu']['origin_ns']
            for channel in range(12):
                proxy, ring = channel % 4, channel // 4
                payloads = sorted(
                    (min(e['ns'] for e in stages['poll']), wid)
                    for (p, wid), stages in groups.items()
                    if p == proxy and wid >> 32 == ring and fifo.kind(stages) == 'payload')
                if len(payloads) != 11:
                    raise ValueError('expected 11 payload requests per channel')
                for ordinal, event in ((6, 11), (11, 4)):
                    end, wid = payloads[ordinal - 1]
                    start = gpu[channel][event][0]['host_ns']
                    identity = dict(node=rank, label=label, index=window['index'],
                                    channel=channel, payload_ordinal=ordinal)
                    observation = dict(identity, proxy=proxy, ring=ring, wr_id=wid,
                                       publication_ns=start, poll_ns=end,
                                       **fifo.observe_interval(rings[proxy, ring], groups, proxy, start, end))
                    observations.append(observation)
                    if end <= start:
                        continue
                    parts = fifo.blocking_segments(rings[proxy, ring], groups, proxy, start, end)
                    segments.extend(dict(identity, **part) for part in parts)
                    by_head = collections.Counter()
                    for part in parts:
                        if part['state'].startswith('full_'):
                            by_head[part['head_wr_id']] += part['duration_ns']
                    for head, duration in sorted(by_head.items()):
                        events = groups[proxy, head]
                        row = dict(identity, proxy=proxy, head_wr_id=head,
                                   head_kind=fifo.kind(events), blocked_us=duration / 1000)
                        for stage in ('post_begin', 'post_end', 'cqe', 'ack_slot'):
                            values = [e['ns'] for e in events.get(stage, [])]
                            selected = (min(values) if stage == 'post_begin' else max(values)) if values else None
                            row[stage + '_from_origin_us'] = (selected - origin) / 1000 if selected is not None else ''
                        row['observed_atomic_fence'] = ','.join(str(e['value']) for e in events.get('atomic_fence', []))
                        heads.append(row)
    if not seen_windows[0] or sorted(seen_windows[0]) != sorted(seen_windows[1]):
        raise ValueError('missing/different two-node sample indices')
    if any(len(indices) != len(set(indices)) for indices in seen_windows.values()):
        raise ValueError('duplicate capture index')
    summary = fifo.blocking_summary(observations, segments)
    intervals = []
    for rank in (0, 1):
        for ordinal in (6, 11):
            rows = [r for r in observations if r['node'] == rank and r['payload_ordinal'] == ordinal]
            positive = [r for r in rows if r['interval_observed']]
            intervals.append(dict(node=rank, label=label, payload_ordinal=ordinal,
                                  observations=len(rows), observed_intervals=len(positive),
                                  censored_intervals=len(rows) - len(positive),
                                  publication_to_poll_signed_median_us=statistics.median(
                                      r['published_to_poll_signed_us'] for r in rows)))
    output.mkdir(parents=True, exist_ok=False)
    write = fifo.boundaries.matched.base.write
    write(output / 'observations.csv', observations)
    write(output / 'blocking_state_summary.csv', summary)
    write(output / 'interval_summary.csv', intervals)
    write(output / 'blocking_heads.csv', heads)
    sources[Path(__file__).as_posix()] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (output / 'context.json').write_text(json.dumps(dict(
        exploratory_only=True, performance_representative=False,
        permits_backend_gap_attribution=False, changes_control_acceptance=False,
        label=label, sample_indices=dict(seen_windows), sources_sha256=sources,
        notes='No outlier exclusion: all matching indices >0 retained. Software FIFO '
        'membership must replay completely and in order at limit 8. Nonpositive '
        'GPU-publication/CPU-poll intervals remain censored. Pooled shares use '
        'overlapping channel observation durations, never Dispatch critical-path '
        'percentages. CQE means application-observed completion, not physical '
        'arrival. This tool does not run or override performance control gates; '
        'the separate control report remains authoritative.'), indent=2), encoding='utf-8')
    print('EXPLORATORY_FIFO_REVIEW_ONLY performance_representative=False ' + str(output))
    for row in intervals:
        dominant = max((r for r in summary if r['node'] == row['node'] and r['payload_ordinal'] == row['payload_ordinal']),
                       key=lambda r: float(r['pooled_interval_share_pct'] or 0))
        print('node=%s ordinal=%s signed_median_us=%.3f observed=%s/%s dominant=%s share=%.3f%%' % (
            row['node'], row['payload_ordinal'], row['publication_to_poll_signed_median_us'],
            row['observed_intervals'], row['observations'], dominant['state'], dominant['pooled_interval_share_pct']))
    return intervals, summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trace', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--label', default='dispatch_bf16_chunk32')
    args = parser.parse_args()
    review(args.trace, args.output, args.label)
