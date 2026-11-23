"""Four inactive controls: separate cadence sensitivity from run-to-run drift.

No GPU access. Local means are descriptive; max(node means) is not makespan.
"""
import argparse
import json
from pathlib import Path

import summarize_boundaries as boundaries


def summarize(batch_pre, batch_post, paced, post, output, fence_plan='default'):
    roots = dict(batch_pre=batch_pre, batch_post=batch_post, paced=paced, post=post)
    if len({p.resolve() for p in roots.values()}) != 4:
        raise ValueError('four distinct control cases required')
    if fence_plan not in ('default', 'peer-tail-off'):
        raise ValueError('unsupported scope fence plan')
    base = boundaries.matched.base
    rows, contexts = [], []
    for rank in (0, 1):
        data = {tag: base.case_data(root, rank, False,
                reference_events='batch' if tag.startswith('batch') else 'paced')
                for tag, root in roots.items()}
        first = data['batch_pre']
        if any(base.sig(d['identity']) != base.sig(first['identity']) or
               d['runtime']['module_sha'] != first['runtime']['module_sha']
               for d in data.values()):
            raise ValueError('control source/runtime/boot/module changed')
        identity = first['identity']
        expected = 'tail-off' if rank == 1 and fence_plan == 'peer-tail-off' else 'on'
        if (identity.get('reference_profile') != 'boundaries' or
            identity.get('normal_window_plan', 'default') != 'default' or
            identity.get('normal_submit_schedule', 'lane-mixed') != 'lane-mixed' or
            identity.get('normal_fadd_fence_plan', 'default') != fence_plan or
            identity.get('normal_fadd_fence', 'on') != expected or
            fence_plan != 'default' and identity['backend'] != 'urma'):
            raise ValueError('wrong scope transport/profile plan')
        contexts.append(dict(node=rank, **identity, module_sha=first['runtime']['module_sha']))
        for label in sorted(base.ALL_LABELS):
            values = {tag: d['means'][label]['mean_us'] for tag, d in data.items()}
            batch = (values['batch_pre'] + values['batch_post']) / 2
            continuous_drift = (values['batch_post'] / values['batch_pre'] - 1) * 100
            paced_drift = (values['post'] / values['paced'] - 1) * 100
            paced_mean = (values['paced'] + values['post']) / 2
            cv = max(d['means'][label]['cv_pct'] for d in data.values())
            rows.append(dict(node=rank, label=label,
                **{tag + '_us': us for tag, us in values.items()},
                batch_reference_us=batch, paced_reference_us=paced_mean,
                batch_drift_pct=continuous_drift, paced_drift_pct=paced_drift,
                paced_vs_batch_pct=(paced_mean / batch - 1) * 100, max_cv_pct=cv,
                stable=abs(continuous_drift) <= 5 and abs(paced_drift) <= 5 and cv <= 5))
    combined = []
    for label in sorted(base.ALL_LABELS):
        pair = [r for r in rows if r['label'] == label]
        b = max(pair, key=lambda r: r['batch_reference_us'])
        p = max(pair, key=lambda r: r['paced_reference_us'])
        combined.append(dict(label=label, batch_max_node_mean_us=b['batch_reference_us'],
            paced_max_node_mean_us=p['paced_reference_us'], batch_slow_node=b['node'],
            paced_slow_node=p['node'],
            paced_vs_batch_pct=(p['paced_reference_us'] / b['batch_reference_us'] - 1) * 100,
            both_nodes_stable=all(r['stable'] for r in pair)))
    output.mkdir(parents=True, exist_ok=False)
    base.write(output/'cadence_nodes.csv', rows)
    base.write(output/'cadence_max_node_means.csv', combined)
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts, fence_plan=fence_plan,
        controls={k: str(v) for k, v in roots.items()},
        note='All controls inactive, same module and fixed input. Each family has two independent cases; do not call their samples independent benchmark repetitions. max_node_mean is max of two local reference means, not mean of synchronized per-call maxima or global makespan. Cadence changes barriers/synchronization/Event creation jointly; differences do not identify one cause. Preserve unstable points. Compare backends in the same cadence before using paced stage evidence to explain continuous execution.'), indent=2))
    print('NORMAL_SCOPE_CADENCE_SUMMARY_PASSED ' + str(output))
    return rows, combined


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('batch-pre', 'batch-post', 'paced', 'post', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--fence-plan', choices=('default', 'peer-tail-off'), default='default')
    a = parser.parse_args()
    summarize(a.batch_pre, a.batch_post, a.paced, a.post, a.output, a.fence_plan)
