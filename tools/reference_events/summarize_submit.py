"""Decide a fixed two-pair Normal submission A/B, without mixing node timings."""
import argparse
import json
from pathlib import Path
import statistics

import summarize_matched as matched


def decision(changes, stable):
    if not stable:
        return 'unstable_keep_default'
    if all(x <= -5 for x in changes):
        return 'material_improvement'
    if all(x >= 5 for x in changes):
        return 'material_regression_keep_default'
    if all(abs(x) < 3 for x in changes):
        return 'no_material_change_keep_default'
    return 'threshold_not_met_keep_default'


def summarize(root, job, output):
    cases = {}
    identities = {}
    rows, paired, context = [], [], []
    for repeat in (1, 2):
        for schedule in ('lane-mixed', 'writes-first'):
            case_root = root / ('%s_r%02d_%s' % (job, repeat, schedule.replace('-', '_')))
            for rank in (0, 1):
                data = matched.base.case_data(case_root, rank, False, reference_events='paced')
                c, rt = data['c'], data['runtime']
                if c.get('job') != case_root.name or c.get('normal_submit_schedule') != schedule or rt.get('normal_submit_schedule') != schedule:
                    raise ValueError('missing/wrong runtime submission schedule')
                if data['identity']['backend'] != 'urma':
                    raise ValueError('URMA-only experiment')
                if not matched.base.read(data['case'] / 'status.json')['checks'].get('normal_submit_schedule'):
                    raise ValueError('missing submission schedule log check')
                identity = dict(data['identity'])
                identity.pop('normal_submit_schedule')
                signature = matched.base.sig(identity)
                reference = identities.setdefault(rank, (signature, rt['module_sha']))
                if reference != (signature, rt['module_sha']):
                    raise ValueError('A/B changed source, module, diagnostics, boot, library or workload')
                cases[repeat, schedule, rank] = data
                if repeat == 1 and schedule == 'lane-mixed':
                    context.append(dict(node=rank, **identity, module_sha=rt['module_sha']))
                for label, values in sorted(data['means'].items()):
                    rows.append(dict(repeat=repeat, schedule=schedule, node=rank, label=label, **values))
            raw = {rank: matched.base.rows(cases[repeat, schedule, rank]['case'] / 'api_samples.jsonl') for rank in (0, 1)}
            indexed = {rank: {(r['label'], r['index']): r for r in raw[rank] if r['used']} for rank in (0, 1)}
            if indexed[0].keys() != indexed[1].keys():
                raise ValueError('unpaired peer API samples')
            for (label, index), a in sorted(indexed[0].items()):
                b = indexed[1][label, index]
                paired.append(dict(repeat=repeat, schedule=schedule, label=label, index=index,
                                   node0_us=a['us'], node1_us=b['us'], paired_max_us=max(a['us'], b['us'])))
    rounds = []
    for repeat in (1, 2):
        for label in sorted(matched.base.ALL_LABELS):
            selected = {s: [r['paired_max_us'] for r in paired if r['repeat'] == repeat and r['schedule'] == s and r['label'] == label]
                        for s in ('lane-mixed', 'writes-first')}
            native, first = [statistics.mean(selected[s]) for s in ('lane-mixed', 'writes-first')]
            cv = max(r['cv_pct'] for r in rows if r['repeat'] == repeat and r['label'] == label)
            paired_cv = max(statistics.pstdev(v) / statistics.mean(v) * 100 for v in selected.values())
            rounds.append(dict(repeat=repeat, label=label, lane_mixed_paired_max_mean_us=native,
                               writes_first_paired_max_mean_us=first, writes_first_change_pct=(first/native-1)*100,
                               max_node_cv_pct=cv, max_paired_cv_pct=paired_cv))
    decisions = []
    for label in sorted(matched.base.ALL_LABELS):
        rr = [r for r in rounds if r['label'] == label]
        changes = [r['writes_first_change_pct'] for r in rr]
        decisions.append(dict(label=label, r01_change_pct=changes[0], r02_change_pct=changes[1],
                              decision=decision(changes, all(max(r['max_node_cv_pct'], r['max_paired_cv_pct']) <= 5 for r in rr))))
    output.mkdir(parents=True, exist_ok=False)
    for name, data in [('per_node', rows), ('paired_calls', paired), ('rounds', rounds), ('decision', decisions)]:
        matched.base.write(output / (name + '.csv'), data)
    (output / 'context.json').write_text(json.dumps(dict(nodes=context,
        note='Two reverse-order pairs. Primary endpoint BF16 mean of per-sample max(node0,node1) local CUDA event durations. This is not a globally timed makespan. Engineering rule fixed in advance: >=5% reduction in both pairs supports a material contribution of the submission strategy; <3% absolute change in both does not support a material effect here. CV>5%, mixed signs or borderline effects retain default and do not trigger automatic repetition. A positive result is not proof of a particular hardware fence/queue mechanism, nor of parity with historical verbs.'), indent=2))
    print('SUBMIT_PERFORMANCE_SUMMARY_PASSED ' + str(output))
    return decisions


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--job', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    summarize(a.root, a.job, a.output)
