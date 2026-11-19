"""Audit moved Dispatch tails and local GPU visibility with paired trace controls."""
import argparse
from collections import defaultdict
from pathlib import Path
import statistics

import summarize_matched as matched

BF16 = 'dispatch_bf16_chunk32'
SCHEDULES = ('lane-mixed', 'tail-interleaved')


def moved_tails(events):
    by_request = defaultdict(dict)
    candidates = []
    for event in events:
        key = event['proxy'], event['wr_id']
        if event['stage'] == 'dispatch_tail_interleaved':
            candidates.append(event)
        if event['stage'] in ('payload_bytes', 'post_begin', 'post_end'):
            by_request[key][event['stage']] = event
    writes = defaultdict(list)
    for (proxy, request), fields in by_request.items():
        if 'payload_bytes' in fields and 'post_begin' in fields:
            begin = fields['post_begin']
            writes[proxy, begin['qp'], request >> 32].append(begin['ns'])
    effective = 0
    for marker in candidates:
        proxy = marker['proxy']
        tail = by_request[proxy, marker['wr_id']]
        write = by_request[proxy, marker['value']]
        if not {'post_begin', 'post_end'}.issubset(tail) or not {'payload_bytes', 'post_begin', 'post_end'}.issubset(write):
            continue
        begin, end = tail['post_begin'], tail['post_end']
        if write['post_begin']['qp'] != begin['qp'] or write['post_begin']['ns'] > begin['ns']:
            raise ValueError('marked Dispatch tail is not after its matching WRITE on the same lane')
        # The interval between this chain's pre/post hooks contains later WRs
        # in the chain, before the Proxy can start another submission.
        later = writes[proxy, begin['qp'], marker['wr_id'] >> 32]
        effective += any(begin['ns'] < time < end['ns'] for time in later)
    return len(candidates), effective


def summarize(root, job, output):
    base = matched.base
    windows, controls = [], []
    for rank in (0, 1):
        reference = None
        for schedule in SCHEDULES:
            case_suffix = schedule.replace('-', '_')
            trace_root = root / f'{job}_trace_{case_suffix}'
            trace = base.case_data(trace_root, rank, False, reference_events='on')
            identity = trace['identity']
            if identity['backend'] != 'urma' or identity['reference_profile'] != 'order' or identity['normal_submit_schedule'] != schedule:
                raise ValueError('unexpected order trace configuration')
            comparable = dict(identity)
            comparable.pop('normal_submit_schedule')
            signature = base.sig(comparable), trace['runtime']['module_sha']
            if reference is not None and reference != signature:
                raise ValueError('trace A/B changed runtime, build or workload')
            reference = signature
            paced = [base.case_data(root / f'{job}_r{repeat:02d}_{case_suffix}', rank, False, reference_events='paced')
                     for repeat in (1, 2)]
            if any(base.sig(p['identity']) != base.sig(identity) or p['runtime']['module_sha'] != signature[1] for p in paced):
                raise ValueError('paced and trace settings differ')
            control = statistics.mean(p['means'][BF16]['mean_us'] for p in paced)
            measured = trace['means'][BF16]['mean_us']
            change = (measured / control - 1) * 100
            cv = max(p['means'][BF16]['cv_pct'] for p in paced + [trace])
            controls.append(dict(node=rank,schedule=schedule,paced_us=control,trace_us=measured,
                                 impact_pct=change,max_cv_pct=cv,representative=abs(change)<=5 and cv<=5))
            for window in base.rows(trace['case'] / 'event_trace.jsonl'):
                if window['label'] != BF16 or window['index'] == 0:
                    continue
                times = defaultdict(list)
                for event in window['gpu']['records']:
                    times[event['event']].append(event['host_ns'])
                origin = min(times[0])
                markers, moved = moved_tails(window['cpu']['events'])
                if schedule == 'lane-mixed' and markers:
                    raise ValueError('control unexpectedly used tail interleaving')
                windows.append(dict(node=rank,schedule=schedule,index=window['index'],
                                    first_remote_us=(min(times[3])-origin)/1000,
                                    last_remote_us=(max(times[2])-origin)/1000,
                                    interleaved_markers=markers,tails_before_later_write_in_chain=moved,
                                    clock_max_gap_us=window['gpu']['clock_stats']['max_gap_ns']/1000))
    output.mkdir(parents=True, exist_ok=False)
    base.write(output/'trace_controls.csv',controls)
    base.write(output/'visibility_windows.csv',windows)
    if all(control['representative'] for control in controls):
        print('ORDER_TRACE_BF16_GATE_PASSED')
    else:
        print('ORDER_TRACE_BF16_GATE_FAILED; retain windows without attributing paced performance')
    if any(row['tails_before_later_write_in_chain'] for row in windows):
        print('ORDER_TRACE_CHAIN_CHANGE_OBSERVED')
    else:
        print('ORDER_TRACE_CHAIN_CHANGE_NOT_OBSERVED; check pairing coverage before interpreting a null result')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.job, args.output)
