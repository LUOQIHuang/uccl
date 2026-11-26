"""Bound the wait of already published commands using the actual FIFO head."""

from collections import deque
import statistics


def replay_ring(events):
    pending = deque()
    collected = {}
    ready = {}
    transitions = []
    previous = 0
    for event in events:
        wr, ns, kind = event['wr_id'], event['ns'], event['kind']
        if ns < previous:
            raise ValueError('out-of-order FIFO event')
        previous = ns
        if kind == 3:
            if len(pending) != 8 or wr in ready or wr in collected:
                raise ValueError('invalid non-consuming FIFO observation')
            ready[wr] = ns
            continue
        if kind == 1:
            if len(pending) == 8 or wr in collected:
                raise ValueError('invalid credit acquisition')
            collected[wr] = event
            pending.append(wr)
        elif kind == 2:
            if not pending or pending.popleft() != wr:
                raise ValueError('ordered credit retirement mismatch')
        else:
            raise ValueError('unknown FIFO event')
        transitions.append((ns, len(pending), pending[0] if pending else None))
    if pending or not ready.keys() <= collected.keys():
        raise ValueError('incomplete FIFO lifetime')
    return collected, ready, transitions


def split_wait(start, end, transitions, writes):
    def overlap(a, b, low, high):
        return max(0, min(b, high) - max(a, low))

    for (left, pending, head), (right, _, _) in zip(transitions, transitions[1:]):
        a, b = max(start, left), min(end, right)
        if b <= a:
            continue
        spans = dict(credit_available=0, target_before_post=0, target_sdk_submit=0, target_cqe_invalid=0,
                     target_cqe_valid_before_sdk=0, target_sdk_returned=0, unresolved=0)
        duration = b - a
        target = writes.get(head)
        if pending < 8:
            spans['credit_available'] = duration
        elif target is not None:
            begin, post, sdk = target['post_begin_ns'], target['post_end_ns'], target['sdk_return_ns']
            invalid, valid = target['last_invalid_begin_ns'], target['first_valid_end_ns']
            spans['target_before_post'] = overlap(a, b, 0, begin)
            spans['target_sdk_submit'] = overlap(a, b, begin, post)
            if invalid is not None:
                spans['target_cqe_invalid'] = overlap(a, b, post, invalid)
            if valid is not None:
                spans['target_cqe_valid_before_sdk'] = overlap(a, b, max(post, valid), sdk)
            spans['target_sdk_returned'] = overlap(a, b, sdk, b)
        spans['unresolved'] = duration - sum(spans.values())
        if spans['unresolved'] < 0:
            raise ValueError('overlapping FIFO subintervals')
        yield dict(begin_ns=a, end_ns=b, pending=pending, head_wr_id=head, **spans)


def analyze_window(window, rank, operations):
    waits, all_parts = [], []
    for stream in window['streams']:
        proxy = stream['proxy']
        by_id = {r['wr_id']: r for r in operations if r['proxy'] == proxy}
        for ring in range(3):
            events = [e for e in stream['fifo'] if e['wr_id'] >> 32 == ring]
            collected, ready, transitions = replay_ring(events)
            payloads = [wr for wr, e in collected.items() if e['bytes'] >= 4096]
            if len(payloads) != 11:
                raise ValueError('expected 11 WRITE requests on every active FIFO')
            ordinals = {wr: n for n, wr in enumerate(payloads, 1)}
            if ring == 0 and (set(payloads[:7]) != by_id.keys() or
                    any(collected[wr]['bytes'] != w['payload_bytes'] for wr, w in by_id.items())):
                raise ValueError('FIFO and CQ target WR identities do not agree')
            for position in (6, 11):
                target = payloads[position - 1]
                ready_at = ready.get(target)
                read_at = collected[target]['ns']
                identity = dict(node=rank, index=window['index'], proxy=proxy, ring=ring,
                                waiting_payload=position, waiting_wr_id=target)
                parts = list(split_wait(ready_at, read_at, transitions, by_id)) if ready_at is not None else []
                if ready_at is not None and sum(p['end_ns'] - p['begin_ns'] for p in parts) != read_at - ready_at:
                    raise ValueError('incomplete observed FIFO wait')
                for part in parts:
                    all_parts.append(dict(identity, **part, head_payload=ordinals.get(part['head_wr_id'])))
                def sum_us(field):
                    return sum(p[field] for p in parts) / 1000 if ready_at is not None else None
                waits.append(dict(identity, observed=ready_at is not None, ready_observed_ns=ready_at, read_ns=read_at,
                    observed_wait_lower_us=(read_at - ready_at) / 1000 if ready_at is not None else None,
                    full_wait_lower_us=sum(p['end_ns'] - p['begin_ns'] for p in parts if p['pending'] == 8) / 1000 if ready_at is not None else None,
                    credit_available_us=sum_us('credit_available'), target_before_post_us=sum_us('target_before_post'),
                    target_sdk_submit_us=sum_us('target_sdk_submit'),
                    target_cqe_invalid_lower_us=sum_us('target_cqe_invalid'),
                    target_cqe_valid_before_sdk_lower_us=sum_us('target_cqe_valid_before_sdk'),
                    target_sdk_returned_us=sum_us('target_sdk_returned'), unresolved_us=sum_us('unresolved')))
    return waits, all_parts


def summarize_waits(waits):
    output = []
    for node in (0, 1):
        for position in (6, 11):
            for rings in ((0,), (1, 2)):
                rows = [w for w in waits if w['node'] == node and w['ring'] in rings and w['waiting_payload'] == position]
                observed = [w for w in rows if w['observed']]
                summary = dict(node=node, waiting_payload=position, rings=','.join(map(str, rings)),
                               targets=len(rows), observed=len(observed))
                for field in ('observed_wait_lower_us', 'full_wait_lower_us', 'credit_available_us',
                              'target_before_post_us', 'target_sdk_submit_us',
                              'target_cqe_invalid_lower_us', 'target_cqe_valid_before_sdk_lower_us',
                              'target_sdk_returned_us', 'unresolved_us'):
                    summary[field[:-3] + '_median_us'] = statistics.median(w[field] for w in observed) if observed else None
                total = sum(w['observed_wait_lower_us'] for w in observed)
                summary['full_share_of_observed_wait_pct'] = 100 * sum(w['full_wait_lower_us'] for w in observed) / total if total else None
                summary['tracked_invalid_share_of_observed_wait_pct'] = 100 * sum(w['target_cqe_invalid_lower_us'] for w in observed) / total if total and rings == (0,) else None
                output.append(summary)
    return output
