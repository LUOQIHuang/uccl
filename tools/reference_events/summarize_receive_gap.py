"""Compare fine receiver boundaries on the same kernel with inactive controls."""

import argparse
import json
from pathlib import Path
from statistics import mean, median, pstdev

from summarize_boundaries import channel_records
from summarize_same_kernel_gap import KERNEL, LABEL, base, shared_signature

BACKENDS = {'rdma': 'verbs', 'urma': 'urma'}
PHASES = ('pre', 'probe', 'post')
INTERVALS = (
    'first_write_to_final_tail_check_us', 'final_tail_check_to_seen_us',
    'tail_seen_to_last_epoch_check_us', 'last_epoch_check_to_visible_us',
    'last_visible_to_output_done_us',
)
METRICS = (*INTERVALS, 'first_write_to_output_done_us', 'api_outside_intervals_signed_us',
           'first_token_to_first_write_us')


def first_event(records, event):
    return min(r['host_ns'] for r in records[event])


def receiver_channels(receiver, sender):
    """Match channel identity across peers, with a local time base on each host."""
    rx, tx = channel_records(receiver), channel_records(sender)
    if rx.keys() != tx.keys() or set(rx) != set(range(12)):
        raise ValueError('incomplete channel correspondence')
    rx_anchor = min(first_event(records, 1) for records in rx.values())
    rx_first_token = min(first_event(records, 0) for records in rx.values())
    tx_anchor = min(first_event(records, 1) for records in tx.values())
    tx_last = max(first_event(records, 4) for records in tx.values())
    rx_last_visible = max(first_event(records, 2) for records in rx.values())
    rows = []
    for channel, records in sorted(rx.items()):
        # E5 may have multiple attempts. E6 is the successful observed tail.
        t = [rx_anchor, *(first_event(records, event) for event in (5, 6, 7, 2, 9))]
        parts = {name: (b-a)/1000 for name, a, b in zip(INTERVALS, t, t[1:])}
        duration = (t[-1]-t[0])/1000
        publish = first_event(tx[channel], 4)
        rows.append(dict(index=receiver['index'], channel=channel,
            receiver_ordered=t==sorted(t), receiver_output_done_ns=t[-1],
            receiver_last_visible=t[-2]==rx_last_visible,
            sender_last_publish=publish==tx_last,
            sender_chunk6_published_local_us=(first_event(tx[channel], 11)-tx_anchor)/1000,
            sender_last_published_local_us=(publish-tx_anchor)/1000,
            sender_publish_before_own_last_us=(tx_last-publish)/1000,
            receiver_visible_before_own_last_us=(rx_last_visible-t[-2])/1000,
            receiver_forwarder_done_minus_last_visible_us=(first_event(records, 8)-t[-2])/1000,
            **parts, first_write_to_output_done_us=duration,
            first_token_to_first_write_us=(rx_anchor-rx_first_token)/1000,
            api_outside_intervals_signed_us=receiver['us']-duration))
    # CUDA timing, including signed residuals, is reported for one selected
    # receiver channel per sample. Other channels run concurrently.
    latest = max(rows, key=lambda row: row['receiver_output_done_ns'])
    return rows, latest


def control_row(backend, node, phases):
    a, t, b = (mean(phases[phase]) for phase in PHASES)
    ref = (a + b) / 2
    drift, impact = 100*(b-a)/ref, 100*(t-ref)/ref
    cv = max(100*pstdev(values)/mean(values) for values in phases.values())
    return dict(backend=backend, node=node, pre_us=a, probe_us=t, post_us=b,
                drift_pct=drift, perturbation_pct=impact, max_cv_pct=cv,
                representative=max(abs(drift), abs(impact), cv)<=5)


def summarize(root, output):
    controls, channels, selected, contexts = [], [], [], []
    shared = {}
    for prefix, backend in BACKENDS.items():
        raw, trace = {}, {}
        for rank in (0, 1):
            signature = None
            for phase in PHASES:
                name = f'{prefix}_{phase}'
                data = base.case_data(root/name, rank, False,
                    reference_events='on' if phase=='probe' else 'paced', reference_gpu_only=True)
                c, ident = data['c'], data['identity']
                if (c['job']!=name or ident['backend']!=backend or ident['kernel']!=KERNEL
                        or ident['reference_profile']!='boundaries' or ident['normal_window_plan']!='default'
                        or ident['normal_retire_plan']!='ordered'):
                    raise ValueError(f'wrong run configuration: {name}, rank {rank}')
                if backend=='urma' and (ident['normal_fadd_fence_plan']!='peer-tail-off'
                        or ident['normal_submit_schedule']!='lane-mixed'):
                    raise ValueError('expected current URMA tail-off configuration')
                if backend=='verbs' and not base.read(data['case']/'status.json')['checks'].get('dmabuf'):
                    raise ValueError('GPU DMA-BUF was not verified')
                local = (ident, data['runtime']['module_sha'])
                if signature is not None and signature!=local:
                    raise ValueError('runtime/module/boot changed during controls')
                signature = local
                common = shared_signature(ident)
                if shared.setdefault(rank, common)!=common:
                    raise ValueError('cross-backend host/boot/runtime/source/workload changed')
                raw[rank,phase] = {r['index']:r['us'] for r in base.rows(data['case']/'api_samples.jsonl')
                                   if r['label']==LABEL and r['used']}
                if phase=='probe':
                    trace[rank] = {r['index']:r for r in base.rows(data['case']/'event_trace.jsonl')
                                   if r['label']==LABEL and r['index']>0}
                contexts.append(dict(case=name, rank=rank, **ident))
            controls.append(control_row(backend, rank, {p:list(raw[rank,p].values()) for p in PHASES}))
        paired = {p:[max(raw[0,p][i], raw[1,p][i]) for i in range(1,10)] for p in PHASES}
        controls.append(control_row(backend, 'paired_max', paired))
        for index in range(1,10):
            receiver, sender = trace[0][index], trace[1][index]
            rows, latest = receiver_channels(receiver, sender)
            clocks = [w['gpu']['clock_stats'] for w in (receiver, sender)]
            channels.extend(dict(backend=backend, **r) for r in rows)
            selected.append(dict(backend=backend, **latest, rank0_api_us=receiver['us'], rank1_api_us=sender['us'],
                clock_ok=all(s['scheduler_error']==0 for s in clocks),
                rank0_max_clock_gap_us=clocks[0]['max_gap_ns']/1000,
                rank1_max_clock_gap_us=clocks[1]['max_gap_ns']/1000))

    results = []
    for field in METRICS:
        samples = {backend:[r[field] for r in selected if r['backend']==backend] for backend in BACKENDS.values()}
        v, u = (samples[name] for name in ('verbs','urma'))
        results.append(dict(interval=field, verbs_mean_us=mean(v), urma_mean_us=mean(u),
            urma_minus_verbs_mean_us=mean(u)-mean(v), verbs_median_us=median(v), urma_median_us=median(u)))
    v, u = (next(r for r in controls if r['backend']==backend and r['node']=='paired_max')
            for backend in ('verbs', 'urma'))
    control_gap = ((u['pre_us']+u['post_us'])-(v['pre_us']+v['post_us']))/2
    probe_gap = u['probe_us']-v['probe_us']
    decision = dict(controls_passed=all(r['representative'] for r in controls),
        rank0_output_order_and_clock_passed=all(r['receiver_ordered'] and r['clock_ok'] for r in selected),
        control_gap_us=control_gap, probe_gap_us=probe_gap, probe_gap_change_us=probe_gap-control_gap,
        all_calls_limited_by_rank0=all(r['rank0_api_us']>=r['rank1_api_us'] for r in selected),
        note='The rank0 local output timeline is partitioned on the last finished receiver channel. '
             'Cross-host joins use channel and sample identity, never a timestamp subtraction. '
             'GPU publication is not CPU collection, and these observations do not measure FIFO or network causality.')
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('controls', controls), ('receiver_channels', channels),
                       ('latest_receiver', selected), ('receive_comparison', results)]:
        base.write(output/f'{name}.csv', rows)
    (output/'decision.json').write_text(json.dumps(decision, indent=2)+'\n', encoding='utf-8')
    (output/'context.json').write_text(json.dumps(contexts, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(decision, indent=2))


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
