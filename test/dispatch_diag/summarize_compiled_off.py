"""Compare uninstrumented controls against a trace build with no capture calls."""
import argparse
import json
from pathlib import Path

from summarize import ALL_LABELS, LABELS, case_data, sig, write


def summarize(baseline, compiled_off, post, output):
    data = []
    contexts = []
    for rank in (0, 1):
        b = case_data(baseline, rank, False)
        t = case_data(compiled_off, rank, False, compiled_off=True)
        p = case_data(post, rank, False)
        if not sig(b['identity']) == sig(t['identity']) == sig(p['identity']):
            raise ValueError('build/source/runtime/boot changed between controls and compiled-off')
        if b['runtime']['module_sha'] != p['runtime']['module_sha']:
            raise ValueError('baseline/post module changed')
        contexts.append(dict(node=rank, **t['identity'],
                             baseline_module_sha=b['runtime']['module_sha'],
                             compiled_off_module_sha=t['runtime']['module_sha']))
        for label in sorted(ALL_LABELS):
            bv, tv, pv = [x['means'][label]['mean_us'] for x in (b, t, p)]
            drift = (pv / bv - 1) * 100
            change = (tv / ((bv + pv) / 2) - 1) * 100
            cvs = [x['means'][label]['cv_pct'] for x in (b, t, p)]
            stable = abs(drift) <= 5 and max(cvs) <= 5
            verdict = ('unstable_measurement' if not stable else
                       'compiled_build_slower' if change > 5 else
                       'compiled_build_faster' if change < -5 else
                       'no_large_compiled_build_penalty')
            data.append(dict(node=rank, label=label, baseline_us=bv,
                             compiled_off_us=tv, post_us=pv,
                             baseline_cv_pct=cvs[0], compiled_off_cv_pct=cvs[1],
                             post_cv_pct=cvs[2], baseline_drift_pct=drift,
                             compiled_off_change_pct=change, stable=stable,
                             verdict=verdict))
    dispatch = [r for r in data if r['label'] in LABELS]
    if any(not r['stable'] for r in dispatch):
        decision = 'unstable_dispatch_controls_inspect_environment'
    elif any(r['verdict'] != 'no_large_compiled_build_penalty' for r in dispatch):
        decision = 'compiled_build_changes_dispatch_inspect_codegen_before_active_tracing'
    else:
        decision = 'no_large_disabled_penalty_reduce_active_probes_and_revalidate'
    output.mkdir(parents=True, exist_ok=True)
    write(output / 'compiled_off_controls.csv', data)
    (output / 'context.json').write_text(json.dumps(dict(nodes=contexts,
        decision=decision, thresholds_pct=dict(drift=5, cv=5, change=5),
        note='Same API measurement schedule; no GPU capture begin/end, extra per-sample barrier, GPU timestamps or scope writes. This tests the compiled trace implementation including disabled host launch checks, not pure GPU instruction cost. Thresholds screen large effects, not statistical significance. Does not identify the original URMA-versus-verbs root cause.'), indent=2))
    print('DISPATCH_COMPILED_OFF_SUMMARY_PASSED ' + str(output))
    print('DISPATCH_COMPILED_OFF_DECISION ' + decision)
    return data


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('baseline', 'compiled-off', 'post', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    summarize(args.baseline, args.compiled_off, args.post, args.output)
