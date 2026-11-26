"""Replay RDMA FIFO-full observations with their actual pending head."""

import argparse
from collections import Counter
from pathlib import Path
from statistics import median

from audit_boundary_fifo import blocking_segments, replay
from summarize_same_kernel_gap import KERNEL, LABEL, base


def window_rows(window, rank):
    cpu = window['cpu']
    if cpu['clock'] != 'CLOCK_MONOTONIC_RAW' or cpu['version'] != 1 or cpu['dropped'] or cpu['heartbeat_stride']:
        raise ValueError('invalid CPU observation window')
    if any(e['value'] for e in cpu['events'] if e['stage'] in ('cqe', 'post_end')):
        raise ValueError('failed verbs post or completion')
    groups, segments = replay(window)
    payload_channels = {(proxy, ring) for proxy in range(4) for ring in range(3)}
    if not payload_channels <= segments.keys():
        raise ValueError('missing payload FIFO channel')
    rows, detail = [], []
    for proxy, ring in sorted(payload_channels):
        spans = segments[proxy, ring]
        collected = [(wid, events) for (p, wid), events in groups.items()
                     if p == proxy and wid >> 32 == ring and events.get('collect')]
        if any(len(events['collect']) != 1 for _, events in collected):
            raise ValueError('missing or duplicated CPU collection')
        payloads = sorted((wid, events) for wid, events in collected
                          if any(e['value'] >= 4096 for e in events.get('payload_bytes', ())))
        if len(payloads) != 11:
            raise ValueError('expected 11 payload WRITEs per FIFO channel')
        payload_ordinals = {wid: ordinal for ordinal, (wid, _) in enumerate(payloads, 1)}
        for ordinal in (6, 11):
            wid, events = payloads[ordinal - 1]
            collected_ns = events['collect'][0]['ns']
            ready = events.get('fifo_full_ready', ())
            if len(ready) > 1:
                raise ValueError('duplicate non-consuming FIFO observation')
            key = dict(node=rank, index=window['index'], proxy=proxy, ring=ring,
                       payload=ordinal, wr_id=wid, collected_ns=collected_ns)
            start = ready[0]['ns'] if ready else None
            if start is not None and not any(a <= start < b and len(queue) == 8 for a, b, queue in spans):
                raise ValueError('observed command outside a full window')
            parts = blocking_segments(spans, groups, proxy, start, collected_ns) if ready else []
            full_parts = [part for part in parts if part['pending'] == 8]
            totals = Counter()
            for part in parts:
                totals[part['state']] += part['duration_us']
                detail.append(dict(**key, **part, head_payload=payload_ordinals.get(part['head_wr_id'])))
            first_head = full_parts[0]['head_wr_id'] if full_parts else None
            rows.append(dict(**key, observed=bool(ready),
                wait_us=(collected_ns - start) / 1000 if ready else None,
                full_us=sum(part['duration_us'] for part in full_parts) if ready else None,
                first_head_payload=payload_ordinals.get(first_head),
                head_pre_submit_us=totals['full_head_pre_submit'] if ready else None,
                head_submit_us=totals['full_head_submission_span'] if ready else None,
                payload_wait_observed_cqe_us=totals['full_payload_wait_cqe'] if ready else None,
                head_cqe_observed_to_retire_us=totals['full_head_cqe_observed_unretired'] if ready else None))
    latest = [max((row for row in rows if row['payload'] == ordinal), key=lambda row: row['collected_ns'])
              for ordinal in (6, 11)]
    return rows, detail, latest


def summarize(root, output):
    overlay = base.read(root/'overlay_node0.json')
    if overlay != base.read(root/'overlay_node1.json'):
        raise ValueError('RDMA probe source differs between nodes')
    controls, waits, intervals, latest = [], [], [], []
    for rank in (0, 1):
        cases = [base.case_data(root/name, rank, False, reference_events='paced', fifo_cpu=(name == 'probe'))
                 for name in ('pre', 'probe', 'post')]
        first = cases[0]
        identity = first['identity']
        if (identity['backend'] != 'verbs' or identity['kernel'] != KERNEL or identity['reference_profile'] != 'boundaries'
                or identity['flags'].get('USE_URMA') != '0' or identity['flags'].get('USE_DMABUF') != '1'):
            raise ValueError('wrong RDMA environment')
        if identity['sources']['ep/src/proxy.cpp'] != overlay['sources']['ep/src/proxy.cpp']:
            raise ValueError('RDMA module was not built with the FIFO probe source')
        for case in cases:
            if case['identity'] != identity or case['runtime']['module_sha'] != first['runtime']['module_sha']:
                raise ValueError('RDMA source, boot or environment changed between control and probe')
            if not base.read(case['case']/'status.json')['checks'].get('dmabuf'):
                raise ValueError('GPU DMA-BUF was not verified')
        pre, probe, post = (case['means'][LABEL]['mean_us'] for case in cases)
        ref = (pre + post) / 2
        drift, perturbation = (post-pre)/ref*100, (probe-ref)/ref*100
        cv = max(case['means'][LABEL]['cv_pct'] for case in cases)
        controls.append(dict(node=rank, pre_us=pre, probe_us=probe, post_us=post, drift_pct=drift,
            perturbation_pct=perturbation, max_cv_pct=cv, representative=max(abs(drift), abs(perturbation), cv) <= 5))
        probe_case = cases[1]['case']
        api = {row['index']: row['us'] for row in base.rows(probe_case/'api_samples.jsonl') if row['label'] == LABEL}
        windows = base.rows(probe_case/'fifo_trace.jsonl')
        if ([row['index'] for row in windows] != sorted(api)
                or any(row['label'] != LABEL or row['us'] != api[row['index']] for row in windows)):
            raise ValueError('FIFO capture and timing samples disagree')
        for window in windows[1:]:
            w, p, l = window_rows(window, rank)
            waits.extend(w)
            intervals.extend(p)
            latest.extend(l)

    accepted = all(row['representative'] for row in controls)
    report = []
    for rank in (0, 1):
        for ordinal in (6, 11):
            entries = [row for row in latest if row['node'] == rank and row['payload'] == ordinal]
            observed = [row for row in entries if row['observed']]
            all_channels = [row for row in waits if row['node'] == rank and row['payload'] == ordinal]
            durations = {f'{key[:-3]}_median_us': median(r[key] for r in observed) if observed else None
                         for key in ('wait_us', 'full_us', 'head_pre_submit_us', 'head_submit_us',
                                     'payload_wait_observed_cqe_us', 'head_cqe_observed_to_retire_us')}
            report.append(dict(node=rank, payload=ordinal, usable=accepted,
                latest_windows=len(entries), latest_observed=len(observed), all_channels=len(all_channels),
                all_channels_observed=sum(r['observed'] for r in all_channels), **durations))
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('controls', controls), ('fifo_waits', waits), ('head_intervals', intervals),
                       ('latest_channels', latest), ('summary', report)]:
        base.write(output/f'{name}.csv', rows)
    print(f'VERBS_FIFO_SUMMARY_WRITTEN usable={accepted} {output}')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
