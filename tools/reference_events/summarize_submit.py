"""Decide a fixed two-pair Normal submission A/B, without mixing node timings."""
import argparse
import json
from pathlib import Path
import statistics
import sys

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


def summarize(root, job, output, candidate='writes-first', cadence='paced'):
    if cadence not in ('paced','batch') or (cadence=='batch' and candidate!='writes-first'):
        raise ValueError('unsupported submission experiment')
    schedules=('lane-mixed',candidate)
    name=candidate.replace('-','_')
    cases = {}
    identities = {}
    rows, paired, context, coverage = [], [], [], []
    for repeat in (1, 2):
        for schedule in schedules:
            case_root = root / ('%s_r%02d_%s' % (job, repeat, schedule.replace('-', '_')))
            for rank in (0, 1):
                data = matched.base.case_data(case_root, rank, False, reference_events=cadence)
                c, rt = data['c'], data['runtime']
                if c.get('job') != case_root.name or c.get('normal_submit_schedule') != schedule or rt.get('normal_submit_schedule') != schedule:
                    raise ValueError('missing/wrong runtime submission schedule')
                if data['identity']['backend'] != 'urma':
                    raise ValueError('URMA-only experiment')
                if candidate=='tail-interleaved' and data['identity']['reference_profile']!='order':
                    raise ValueError('order profile required')
                if not matched.base.read(data['case'] / 'status.json')['checks'].get('normal_submit_schedule'):
                    raise ValueError('missing submission schedule log check')
                if cadence=='batch':
                    if not c.get('normal_submit_witness') or c.get('normal_fadd_fence_plan')!='peer-tail-off' or c.get('normal_window_plan')!='default' or c.get('normal_retire_plan')!='ordered' or data['identity']['reference_profile']!='boundaries' or not matched.base.read(data['case']/'status.json')['checks'].get('normal_submit_witness'):
                        raise ValueError('wrong batch submission intervention/witness')
                    sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'test/reference_events'))
                    from submit_witness import inspect
                    coverage.extend(dict(repeat=repeat,schedule=schedule,node=rank,**v) for v in inspect(data['case']/'submit_witness.json',schedule))
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
                        for s in schedules}
            native, first = [statistics.mean(selected[s]) for s in schedules]
            cv = max(r['cv_pct'] for r in rows if r['repeat'] == repeat and r['label'] == label)
            paired_cv = max(statistics.pstdev(v) / statistics.mean(v) * 100 for v in selected.values())
            rounds.append(dict(repeat=repeat, label=label, lane_mixed_paired_max_mean_us=native,
                               **{name+'_paired_max_mean_us':first,name+'_change_pct':(first/native-1)*100},
                               max_node_cv_pct=cv, max_paired_cv_pct=paired_cv))
    decisions = []
    for label in sorted(matched.base.ALL_LABELS):
        rr = [r for r in rounds if r['label'] == label]
        changes = [r[name+'_change_pct'] for r in rr]
        stable=all(max(r['max_node_cv_pct'], r['max_paired_cv_pct']) <= 5 for r in rr)
        result=dict(label=label,r01_change_pct=changes[0],r02_change_pct=changes[1])
        if cadence=='batch':
            a_drift=100*(rr[1]['lane_mixed_paired_max_mean_us']/rr[0]['lane_mixed_paired_max_mean_us']-1)
            b_drift=100*(rr[1]['writes_first_paired_max_mean_us']/rr[0]['writes_first_paired_max_mean_us']-1)
            stable &= max(abs(a_drift),abs(b_drift))<=3
            adequate=all(100*sum(r['mixed_payload_bytes'] for r in coverage if r['repeat']==repeat and r['node']==rank and r['schedule']=='lane-mixed')/sum(r['payload_bytes'] for r in coverage if r['repeat']==repeat and r['node']==rank and r['schedule']=='lane-mixed')>=10 for repeat in (1,2) for rank in (0,1))
            result.update(a_drift_pct=a_drift,b_drift_pct=b_drift,baseline_mixed_payload_coverage_adequate=adequate)
        result['decision']=decision(changes,stable)
        if cadence=='batch' and label=='dispatch_bf16_chunk32' and not adequate:
            result['decision']='coverage_incomplete_keep_default'
        decisions.append(result)
    output.mkdir(parents=True, exist_ok=False)
    for name, data in [('per_node', rows), ('paired_calls', paired), ('rounds', rounds), ('decision', decisions)]:
        matched.base.write(output / (name + '.csv'), data)
    if cadence=='batch':matched.base.write(output/'submission_coverage.csv',coverage)
    (output / 'context.json').write_text(json.dumps(dict(nodes=context,cadence=cadence,
        note='Two reverse-order pairs. Primary endpoint BF16 mean of per-sample max(node0,node1) local CUDA event durations. This is not a globally timed makespan. Engineering rule fixed in advance: >=5% reduction in both pairs supports a material contribution of the submission strategy; <3% absolute change in both does not support a material effect here. CV>5%, mixed signs or borderline effects retain default and do not trigger automatic repetition. A positive result is not proof of a particular hardware fence/queue mechanism, nor of parity with historical verbs.'), indent=2))
    if cadence=='batch':
        (output/'witness_limits.txt').write_text('One extra CPU-only call per case AFTER timed samples. Submission coverage only; no witness latency is included in API averages. Require >=10% mixed payload bytes in each baseline witness; candidate must contain no mixed payload/FADD chain. This is not a sampled estimate of the uninstrumented batch distribution. Both within-plan drifts <=3%, CV<=5%; keep the original thresholds for net gain. No new GPU observer or queue/fence/completion changes.\n')
    print('SUBMIT_PERFORMANCE_SUMMARY_PASSED ' + str(output))
    return decisions


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--job', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--candidate', choices=('writes-first','tail-interleaved'), default='writes-first')
    p.add_argument('--cadence',choices=('paced','batch'),default='paced')
    a = p.parse_args()
    summarize(a.root, a.job, a.output, a.candidate,a.cadence)
