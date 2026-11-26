"""Associate each latest FIFO wait with the actual head on the shared CQ."""

import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'test/normal_completion'))
import analyze as completion


def csv_rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def same_csv(left, right):
    def equivalent(a, b):
        if a == b:
            return True
        try:
            return abs(Decimal(a) - Decimal(b)) <= Decimal('1e-10')
        except (TypeError, InvalidOperation):
            return False

    a, b = csv_rows(left), csv_rows(right)
    return len(a) == len(b) and all(x.keys() == y.keys() and
        all(equivalent(value, y[key]) for key, value in x.items()) for x, y in zip(a, b))


def bounds(part, queue):
    """Use the consumed WR identity to identify the slot in earlier snapshots."""
    head = int(part['head_wr_id'])
    matches = [(poll, entry) for poll in queue['polls'] for entry in poll['completed']
               if entry['wr_id'] == head]
    if not matches:
        return dict(cq_covered=False, cqe_invalid_lower_us=None, cqe_to_sdk_upper_us=None,
                    sdk_to_retire_us=None)
    if len(matches) != 1:
        raise ValueError('ambiguous head WR consumption')
    consumed, entry = matches[0]
    begin, end = int(part['begin_ns']), int(part['end_ns'])
    if consumed['sdk_return'] > end:
        raise ValueError('credit retired before SDK completion')
    position = entry['position']
    invalid = None
    valid_seen = False
    for poll in queue['polls']:
        if poll['seq'] > consumed['seq']:
            break
        offset = position - poll['ci']
        if not 0 <= offset < queue['lookahead']:
            continue
        if poll['ready_mask'] & (1 << offset):
            valid_seen = True
        elif valid_seen:
            raise ValueError('target owner reverted before consumption')
        else:
            invalid = poll['begin']
    return dict(cq_covered=True,
                cqe_invalid_lower_us=max(0, min(invalid, end) - begin)/1000 if invalid is not None else None,
                cqe_to_sdk_upper_us=(consumed['sdk_return'] - invalid)/1000 if invalid is not None else None,
                sdk_to_retire_us=(end - consumed['sdk_return'])/1000)


def summarize(root, output):
    with tempfile.TemporaryDirectory() as temp:
        verified = Path(temp) / 'verified'
        decision = completion.summarize(root, verified, study='blockers')
        latest = csv_rows(verified / 'latest_collected_channel.csv')
        parts = csv_rows(verified / 'fifo_head_intervals.csv')
        archive_equal = all(same_csv(p, root/'summary'/p.name)
                            for p in verified.glob('*.csv'))
    if not decision['mechanism_usable']:
        raise ValueError('capture failed the pre/probe/post controls')

    sources, queues, retired = {}, {}, {}
    for node in (0, 1):
        path = root/'cases'/(root.name + '_probe')/f'node{node}'/'normal/fadd_path.jsonl'
        sources[path.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        for window in completion.base.rows(path):
            for queue in window['cq_ready']:
                queues[str(node), str(window['index']), str(queue['proxy'])] = queue
            for stream in window['streams']:
                for event in stream['fifo']:
                    if event['kind'] == 2:
                        retired[str(node), str(window['index']), str(stream['proxy']), str(event['wr_id'])] = event['ns']

    key_fields = ('node', 'waiting_payload', 'index', 'proxy', 'ring', 'waiting_wr_id')
    latest_keys = {tuple(r[k] for k in key_fields) for r in latest}
    detail = []
    for part in parts:
        key = tuple(part[k] for k in key_fields)
        if part['pending'] != '8' or key not in latest_keys:
            continue
        identity = {k: part[k] for k in key_fields}
        queue = queues[part['node'], part['index'], part['proxy']]
        retire = retired[part['node'], part['index'], part['proxy'], part['head_wr_id']]
        if retire != int(part['end_ns']):
            raise ValueError('full interval does not end at recorded head retirement')
        detail.append(dict(identity, head_payload=part['head_payload'], head_wr_id=part['head_wr_id'],
                           full_wait_us=(int(part['end_ns']) - int(part['begin_ns']))/1000,
                           **bounds(part, queue)))

    summary = []
    for node in ('0', '1'):
        for payload in ('6', '11'):
            selected = [r for r in latest if (r['node'], r['waiting_payload']) == (node, payload)]
            observed = [float(r['observed_wait_lower_us']) for r in selected if r['observed'] == 'True']
            intervals = [p for p in detail if (p['node'], p['waiting_payload']) == (node, payload)]
            covered = [p for p in intervals if p['cqe_invalid_lower_us'] is not None]
            total = sum(p['full_wait_us'] for p in covered)
            blocked = sum(p['cqe_invalid_lower_us'] for p in covered)
            summary.append(dict(node=node, waiting_payload=payload, windows=len(selected),
                fifo_wait_observed=len(observed), fifo_wait_lower_median_us=statistics.median(observed) if observed else None,
                full_intervals=len(intervals), cqe_bound_intervals=len(covered),
                cqe_invalid_lower_median_us=statistics.median(p['cqe_invalid_lower_us'] for p in covered) if covered else None,
                invalid_share_of_covered_full_wait_lower_pct=100 * blocked / total if total else None))
    output.mkdir(parents=True, exist_ok=False)
    completion.base.write(output/'latest_full_intervals.csv', detail)
    completion.base.write(output/'summary.csv', summary)
    (output/'context.json').write_text(json.dumps(dict(archive_csv_equal_with_rounding=archive_equal,
        sources_sha256=sources, decision=decision,
        note='CQ snapshots cover all completions delivered in each observed shared JFC poll. '
             'The target position is recovered from WR identity and consumer position. '
             'Missing snapshots remain unclassified. Latest refers to local CPU collection time, '
             'not the final token on the remote GPU. No URMA-to-verbs attribution.'), indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
