"""Evaluate the fixed 8/32/32/8 inactive-batch admission-credit experiment."""
import argparse
import json
from pathlib import Path
import statistics

import summarize_matched as matched
from summarize_submit import decision


def summarize(root, job, output):
    signatures, contexts, nodes, paired = {}, [], [], []
    for repeat, width in ((1, 8), (1, 32), (2, 32), (2, 8)):
        name = '%s_r%02d_w%d' % (job, repeat, width)
        case_root = root / name
        raw, peers = {}, []
        for rank in (0, 1):
            data = matched.base.case_data(case_root, rank, False, reference_events='batch')
            c, runtime = data['c'], data['runtime']
            expected_plan = 'both32' if width == 32 else 'default'
            device = 'udmac1d1e2' if rank == 0 else 'udmac0d1e2'
            if (c['job'] != name or c.get('normal_window_plan') != expected_plan or
                    c.get('urma_device_plan') != '202c1-204c0' or c.get('device') != device or
                    c.get('normal_fadd_fence_plan') != 'peer-tail-off' or
                    runtime.get('normal_max_inflight') != width or
                    runtime.get('normal_fifo_retire_version') != 1 or
                    runtime.get('normal_fifo_retire') != 'ordered'):
                raise ValueError('wrong credit/device/fence/retirement campaign or runtime')
            status = matched.base.read(data['case'] / 'status.json')
            if not status['checks'].get('normal_fifo_retire'):
                raise ValueError('missing observed FIFO policy/limit check')
            identity = dict(data['identity'])
            if (identity['backend'] != 'urma' or identity['reference_profile'] != 'boundaries' or
                    identity['normal_submit_schedule'] != 'lane-mixed'):
                raise ValueError('changed backend/profile/submission')
            identity.pop('normal_window_plan')
            identity.pop('normal_max_inflight')
            signature = (matched.base.sig(identity), runtime['module_sha'])
            if signatures.setdefault(rank, signature) != signature:
                raise ValueError('source/module/boot/library/workload changed across A/B')
            if repeat == 1 and width == 8:
                contexts.append(dict(node=rank, **identity, module_sha=runtime['module_sha']))
            peers.append(identity)
            for label, stats in sorted(data['means'].items()):
                nodes.append(dict(repeat=repeat, width=width, node=rank, label=label, **stats))
            raw[rank] = {(r['label'], r['index']): r['us']
                         for r in matched.base.rows(data['case'] / 'api_samples.jsonl') if r['used']}
        for key in ('sources', 'flags', 'sdk', 'libraries', 'maca', 'gpu_versions', 'benchmark_sha', 'diagnostics', 'torch'):
            if peers[0][key] != peers[1][key]:
                raise ValueError('peer input mismatch: ' + key)
        if raw[0].keys() != raw[1].keys():
            raise ValueError('unpaired peer samples')
        for (label, index), a in sorted(raw[0].items()):
            b = raw[1][label, index]
            paired.append(dict(repeat=repeat, width=width, label=label, index=index,
                               node0_us=a, node1_us=b, paired_max_us=max(a, b)))
    rounds, decisions = [], []
    for label in sorted(matched.base.ALL_LABELS):
        means, selected = {}, []
        for repeat in (1, 2):
            values = {width: [r['paired_max_us'] for r in paired
                              if r['repeat'] == repeat and r['label'] == label and r['width'] == width]
                      for width in (8, 32)}
            a, b = (statistics.mean(values[width]) for width in (8, 32))
            means[repeat, 8], means[repeat, 32] = a, b
            cvs = [r['cv_pct'] for r in nodes if r['repeat'] == repeat and r['label'] == label]
            cvs.extend(100 * statistics.pstdev(v) / statistics.mean(v) for v in values.values())
            row = dict(repeat=repeat, label=label, w8_mean_max_local_us=a,
                       w32_mean_max_local_us=b, w32_change_pct=100 * (b / a - 1),
                       max_cv_pct=max(cvs))
            rounds.append(row)
            selected.append(row)
        drift = {width: 100 * (means[2, width] / means[1, width] - 1) for width in (8, 32)}
        stable = all(r['max_cv_pct'] <= 5 for r in selected) and all(abs(v) <= 3 for v in drift.values())
        changes = [r['w32_change_pct'] for r in selected]
        decisions.append(dict(label=label, r01_change_pct=changes[0], r02_change_pct=changes[1],
                              w8_drift_pct=drift[8], w32_drift_pct=drift[32],
                              decision=decision(changes, stable)))
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in (('per_node', nodes), ('paired_calls', paired), ('rounds', rounds), ('decision', decisions)):
        matched.base.write(output / (name + '.csv'), rows)
    (output / 'context.json').write_text(json.dumps(dict(nodes=contexts,
        primary='dispatch_bf16_chunk32',
        note='Fixed A8-B32-B32-A8 on both nodes. Inactive continuous batch, '
        'same source/module/library/boot per node. 202c1/204c0 and 204-tail-off '
        'fixed. Changes software pending-command admission limit only; hardware '
        'queue depth, request order and completion semantics unchanged. Primary '
        'metric is mean max of paired local API durations, not global makespan. '
        'No stage attribution or matched RDMA comparison follows. Positive net '
        'gain supports sensitivity to this admission policy, not that hardware '
        'service time disappeared. Negative result does not measure a percentage '
        'of intrinsic device/network/provider cost. CV<=5%, within-plan drift<=3%; '
        'both pairs improve>=5% to retain, both abs(change)<3% closes this knob.'), indent=2), encoding='utf-8')
    primary = next(r for r in decisions if r['label'] == 'dispatch_bf16_chunk32')
    print('CREDIT_AB_SUMMARY_PASSED primary=' + primary['decision'])
    return decisions


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('root', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    summarize(args.root, args.job, args.output)
