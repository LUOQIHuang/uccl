"""Revalidate both paced/on pairs and compare local observations across backends."""
import argparse
import collections
import csv
import json
from pathlib import Path
import statistics
import tempfile

import summarize_matched as matched


def csv_rows(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def compatible(verbs, urma):
    if verbs['backend'] != 'verbs' or urma['backend'] != 'urma':
        raise ValueError('wrong backend order')
    if urma.get('normal_submit_schedule','lane-mixed') != 'lane-mixed':
        raise ValueError('default backend comparison requires lane-mixed URMA; analyze submission A/B separately')
    if urma.get('normal_fadd_fence','on')!='on' or urma.get('normal_fadd_fence_plan','default') not in ('default','all-on'):
        raise ValueError('analyze FADD fence A/B separately from default backend comparison')
    if any(c.get('normal_window_plan','default')!='default' for c in (verbs,urma)):
        raise ValueError('analyze window A/B separately from default backend comparison')
    if any(c.get('reference_profile','legacy')!='legacy' for c in (verbs,urma)):
        raise ValueError('use boundary analysis for the extended reference profile')
    for key in ('node', 'host', 'torch', 'maca', 'gpu_versions', 'sources', 'benchmark_sha', 'ifname', 'gpu'):
        if verbs[key] != urma[key]:
            raise ValueError('cross-backend mismatch: ' + key)
    vf, uf = dict(verbs['flags']), dict(urma['flags'])
    if vf.pop('USE_URMA') != '0' or uf.pop('USE_URMA') != '1' or vf != uf:
        raise ValueError('unexpected cross-backend build flags')


def gpu_windows(rows):
    groups = collections.defaultdict(list)
    result = []
    for row in rows:
        groups[row['node'], row['label'], row['index']].append(row)
    for (node, label, index), records in sorted(groups.items()):
        times = {event: [int(r['host_ns']) for r in records if int(r['event']) == event]
                 for event in (0, 1, 2)}
        if any(len(v) != 12 for v in times.values()):
            raise ValueError('incomplete GPU window')
        # Event 0 and 1 use different warps in the same Sender CTA.
        pairs = collections.defaultdict(dict)
        for row in records:
            event = int(row['event'])
            if event not in (0, 1):
                continue
            pair = pairs[row['block'], row['epoch']]
            if event in pair:
                raise ValueError('duplicate GPU event for Sender CTA/epoch')
            pair[event] = int(row['host_ns'])
        if len(pairs) != 12 or any(set(pair) != {0, 1} for pair in pairs.values()):
            raise ValueError('unpaired Sender CTA events')
        intervals = [(pair[1] - pair[0]) / 1000 for pair in pairs.values()]
        if min(intervals) < 0 or max(times[2]) < min(times[1]):
            raise ValueError('non-monotonic GPU span')
        result.append(dict(node=node, label=label, index=index,
            sender_cta_0_to_1_median_us=statistics.median(intervals),
            first_token_to_first_write_us=(min(times[1]) - min(times[0])) / 1000,
            first_write_to_last_visible_us=(max(times[2]) - min(times[1])) / 1000,
            first_token_to_last_visible_us=(max(times[2]) - min(times[0])) / 1000))
    return result


def differences(verbs, urma, keys, fields):
    index = lambda rows: {tuple(r[k] for k in keys): r for r in rows}
    vv, uu = index(verbs), index(urma)
    if vv.keys() != uu.keys():
        raise ValueError('different comparison points')
    result = []
    for key in sorted(vv):
        row = dict(zip(keys, key))
        for field in fields:
            v, u = float(vv[key][field]), float(uu[key][field])
            row.update({field + '_verbs': v, field + '_urma': u,
                        field + '_urma_minus_verbs': u - v})
            if v:
                row[field + '_urma_change_pct'] = (u / v - 1) * 100
        result.append(row)
    return result


def completion_tails(rows, by_window=False):
    groups = collections.defaultdict(list)
    for row in rows:
        key = (row['node'], row['label'], row['operation'])
        if by_window:
            key += (row['index'],)
        groups[key].append(float(row['post_end_to_cqe_us']))
    result = []
    for key, values in sorted(groups.items()):
        values.sort()
        row = dict(node=key[0], label=key[1], operation=key[2], requests=len(values))
        if by_window:
            row['index'] = key[3]
        for name, fraction in [('p50_us', .5), ('p95_us', .95), ('p99_us', .99), ('max_us', 1)]:
            position = (len(values)-1)*fraction
            low = int(position)
            row[name] = values[low] + (values[min(low+1, len(values)-1)]-values[low])*(position-low)
        result.append(row)
    return result


def run(verbs_control, verbs_trace, urma_control, urma_trace, output):
    with tempfile.TemporaryDirectory() as temporary:
        roots = {}
        for backend, control, trace in [('verbs', verbs_control, verbs_trace), ('urma', urma_control, urma_trace)]:
            root = Path(temporary) / backend
            controls = matched.summarize(control, trace, root)
            if not all(r['representative'] for r in controls if r['label'].startswith('dispatch_')):
                raise ValueError('Dispatch perturbation/CV gate failed: ' + backend)
            roots[backend] = root
        contexts = {b: json.loads((p / 'context.json').read_text()) for b, p in roots.items()}
        for v, u in zip(contexts['verbs']['nodes'], contexts['urma']['nodes']):
            compatible(v, u)
        tables = {}
        for b, root in roots.items():
            tables[b] = {name: csv_rows(root / (name + '.csv')) for name in
                         ('matched_controls', 'cpu_stage_medians', 'cpu_operation_medians', 'cpu_requests', 'gpu_events', 'capture_quality')}
        read = lambda b, name: tables[b][name]
        api = differences(read('verbs', 'matched_controls'), read('urma', 'matched_controls'),
                          ('node', 'label'), ('paced_us', 'trace_us'))
        stage_fields = [k for k in read('verbs', 'cpu_stage_medians')[0] if k.endswith('_us')]
        stages = differences(read('verbs', 'cpu_stage_medians'), read('urma', 'cpu_stage_medians'),
                             ('node', 'label'), stage_fields)
        operations = differences(read('verbs', 'cpu_operation_medians'), read('urma', 'cpu_operation_medians'),
                                 ('node', 'label', 'operation'), ('median_post_end_to_cqe_us',))
        tails = differences(completion_tails(read('verbs', 'cpu_requests')),
                            completion_tails(read('urma', 'cpu_requests')),
                            ('node', 'label', 'operation'), ('p50_us', 'p95_us', 'p99_us', 'max_us'))
        windows, spans, quality, tail_windows = [], {}, [], []
        for b in ('verbs', 'urma'):
            tail_windows.extend(dict(backend=b, **r) for r in completion_tails(read(b, 'cpu_requests'), by_window=True))
            ww = gpu_windows(read(b, 'gpu_events'))
            windows.extend(dict(backend=b, **r) for r in ww)
            grouped = collections.defaultdict(list)
            for r in ww:
                grouped[r['node'], r['label']].append(r)
            spans[b] = [dict(node=node, label=label, **{
                k: statistics.median(r[k] for r in rows)
                for k in ww[0] if k.endswith('_us')}) for (node, label), rows in sorted(grouped.items())]
            quality.extend(dict(backend=b, **r) for r in read(b, 'capture_quality'))
        gpu = differences(spans['verbs'], spans['urma'], ('node', 'label'),
                          [k for k in spans['verbs'][0] if k.endswith('_us')])
        output.mkdir(parents=True, exist_ok=False)
        for name, rows in [('api_comparison', api), ('cpu_stage_comparison', stages),
                           ('cpu_operation_comparison', operations), ('cpu_completion_tails', tails), ('gpu_span_comparison', gpu),
                           ('gpu_windows', windows), ('cpu_completion_windows', tail_windows), ('capture_quality', quality)]:
            matched.base.write(output / (name + '.csv'), rows)
        (output / 'context.json').write_text(json.dumps(dict(inputs={
            'verbs_control': str(verbs_control), 'verbs_trace': str(verbs_trace),
            'urma_control': str(urma_control), 'urma_trace': str(urma_trace)},
            contexts=contexts, note='Each backend passed matched paced/on validation. Sources, prepared benchmark, recorded MACA runtime hash, Torch, KMD/MACA version strings and common build flags match. Kernels, devices, SDKs and boots differ and remain recorded. One campaign per backend; samples are not independent campaign repeats. CPU intervals overlap across requests. GPU mapped-clock spans are approximate local envelopes, not pure network latency or an additive critical path. Same-CTA spans pair block and epoch across different role warps. Each window is reduced before taking medians; differences of medians need not add.'), indent=2))
    print('REFERENCE_BACKEND_COMPARISON_PASSED ' + str(output))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('verbs-control', 'verbs-trace', 'urma-control', 'urma-trace', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    run(args.verbs_control, args.verbs_trace, args.urma_control, args.urma_trace, args.output)
