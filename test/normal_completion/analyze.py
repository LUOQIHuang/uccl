"""Separate target CQ readability, SDK delivery and CPU control draining."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('completion_pair_metrics', HERE.parent / 'fadd_path/analyze.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
base = legacy.base
LABEL = legacy.LABEL


def operation_rows(window, rank):
    sources = {s['proxy']: s for s in window['streams']}
    snapshots = {s['proxy']: s for s in window['cq_ready']}
    if set(sources) != set(range(4)) or set(snapshots) != set(sources):
        raise ValueError('missing Proxy/CQ stream')
    output, controls = [], []
    for proxy, stream in sources.items():
        if stream['errors'] or stream['dropped'] or len(stream['pairs']) != 1:
            raise ValueError('incomplete target stream')
        pair = stream['pairs'][0]
        legacy.pair_metrics(pair, stream['polls'], 'urma', 1, expected_fence=1 if rank == 0 else 0)
        snap = snapshots[proxy]
        if snap['dropped'] or snap['lookahead'] != 16 or snap['depth'] < 16 or snap['stride'] not in (64, 128, 256):
            raise ValueError('wrong/truncated CQ layout')
        polls = snap['polls']
        if [p['seq'] for p in polls] != list(range(1, len(polls) + 1)):
            raise ValueError('missing CQ snapshots')
        sdk = {p['seq']: p for p in stream['polls']}
        positions = {}
        previous = None
        for poll in polls:
            observed = sdk.get(poll['seq'])
            if (observed is None or observed['count'] != poll['count'] or observed['end'] != poll['sdk_return'] or
                    not observed['begin'] <= poll['begin'] <= poll['end'] <= poll['sdk_return']):
                raise ValueError('snapshot/SDK mismatch')
            if poll['count'] < 0 or poll['after_ci'] - poll['ci'] != poll['count'] or (previous is not None and previous != poll['ci']):
                raise ValueError('consumer index mismatch')
            previous = poll['after_ci']
            if len(poll['completed']) != poll['count'] or not 0 <= poll['ready_mask'] < 1 << 16:
                raise ValueError('completion identity/mask mismatch')
            for i, item in enumerate(poll['completed']):
                if item['position'] != poll['ci'] + i:
                    raise ValueError('wrong completion position')
                positions.setdefault(item['wr_id'], []).append((poll, item))
        for operation in ('write', 'atomic'):
            target = pair[operation + '_id']
            matches = positions.get(target, [])
            if len(matches) != 1:
                raise ValueError('ambiguous target CQ position')
            delivered, identity = matches[0]
            returned = pair[operation + '_sdk_return']
            posted = pair[operation + '_post_end']
            if delivered['seq'] != pair[operation + '_poll_seq'] or delivered['sdk_return'] != returned:
                raise ValueError('target CQ mapping disagrees with SDK record')
            position = identity['position']
            views = [p for p in polls if p['ci'] <= position < p['ci'] + 16]
            ready = [p for p in views if p['ready_mask'] & (1 << (position - p['ci']))]
            first = ready[0] if ready else None
            if first and any(not p['ready_mask'] & (1 << (position - p['ci'])) for p in views if p['seq'] > first['seq']):
                raise ValueError('owner became invalid before target consumption')
            invalids = [p for p in views if first is None or p['seq'] < first['seq']]
            invalid = invalids[-1] if invalids else None
            relevant = [p for p in polls if p['sdk_return'] <= returned]
            output.append(dict(node=rank, index=window['index'], proxy=proxy, operation=operation,
                wr_id=target, cq_position=position, target_in_range_polls=len(views), observed_valid=bool(first),
                post_to_sdk_us=(returned - posted) / 1000,
                post_to_last_invalid_lower_us=max(0, invalid['begin'] - posted) / 1000 if invalid else None,
                valid_to_sdk_lower_us=(returned - first['end']) / 1000 if first else None,
                first_valid_begin_ns=first['begin'] if first else None,
                first_valid_end_ns=first['end'] if first else None,
                last_invalid_begin_ns=invalid['begin'] if invalid else None,
                target_valid_head_invalid_polls=sum(p['ci'] < position and not p['ready_mask'] & 1 for p in ready),
                target_valid_at_head_polls=sum(p['ci'] == position for p in ready),
                owner_snapshot_total_us=sum(p['end'] - p['begin'] for p in relevant) / 1000,
                after_snapshot_to_sdk_return_total_us=sum(p['sdk_return'] - p['end'] for p in relevant) / 1000,
                max_between_poll_us=max([0] + [(b['begin'] - a['sdk_return']) / 1000 for a, b in zip(relevant, relevant[1:])]),
                result_to_sdk_lower_us=(returned - pair['result_upper']) / 1000
                    if operation == 'atomic' and pair['result_upper'] and pair['result_upper'] <= returned else None))
        for item in stream.get('controls', []):
            if item['kind'] not in (3, 4) or not 0 < item['begin'] <= item['end']:
                raise ValueError('invalid CPU control interval')
            controls.append(dict(node=rank, index=window['index'], proxy=proxy, wr_id=item['wr_id'],
                                 kind='quiet' if item['kind'] == 3 else 'barrier',
                                 cpu_processing_us=(item['end'] - item['begin']) / 1000))
    quiet = [r for r in controls if r['kind'] == 'quiet']
    if len(quiet) != 32 or {(r['proxy'], r['wr_id'] >> 32) for r in quiet} != {(p, ring) for p in range(4) for ring in range(8)}:
        raise ValueError('incomplete 32-ring QUIET CPU observation')
    if len([r for r in controls if r['kind'] == 'barrier']) != 2:
        raise ValueError('incomplete CPU barrier processing observation')
    return output, controls


def summarize(root, output):
    overlays = [base.read(root / ('overlay_node%d.json' % rank)) for rank in (0, 1)]
    if overlays[0] != overlays[1]:
        raise ValueError('different source overlays')
    data, identities, controls, paired = {}, {}, [], []
    for stage in ('pre', 'probe', 'post'):
        for rank in (0, 1):
            d = base.case_data(root / 'cases' / (root.name + '_' + stage), rank, False,
                               reference_events='paced', bf16_only=True)
            c, runtime = d['c'], d['runtime']
            expected_device = 'udmac1d1e2' if rank == 0 else 'udmac0d1e2'
            if (c['job'] != root.name + '_' + stage or runtime.get('normal_completion_probe_version') != 1 or
                    c.get('normal_fadd_fence_plan') != 'peer-tail-off' or c.get('device') != expected_device or
                    c.get('urma_device_plan') != '202c1-204c0' or c.get('normal_window_plan') != 'default' or
                    c.get('normal_retire_plan') != 'ordered' or c.get('reference_profile') != 'boundaries'):
                raise ValueError('wrong module/device/strategy')
            if c['fadd_path_capture'] != (stage == 'probe') or (d['case'] / 'fadd_path.jsonl').exists() != (stage == 'probe'):
                raise ValueError('wrong probe ownership')
            identity = dict(d['identity'], module_sha=runtime['module_sha'])
            if identities.setdefault(rank, identity) != identity:
                raise ValueError('source/module/boot/library/workload drift')
            data[stage, rank] = d
    for who in (0, 1, 'paired_max'):
        stats = []
        for stage in ('pre', 'probe', 'post'):
            if who != 'paired_max':
                stats.append(data[stage, who]['means'][LABEL])
                continue
            raw = {r: {v['index']: v['us'] for v in base.rows(data[stage, r]['case'] / 'api_samples.jsonl') if v['used']}
                   for r in (0, 1)}
            if raw[0].keys() != raw[1].keys():
                raise ValueError('unpaired API samples')
            values = [max(raw[0][i], raw[1][i]) for i in sorted(raw[0])]
            stats.append(dict(mean_us=statistics.mean(values), cv_pct=100 * statistics.pstdev(values) / statistics.mean(values)))
            paired.extend(dict(stage=stage, index=i, node0_us=raw[0][i], node1_us=raw[1][i], max_local_us=max(raw[0][i], raw[1][i])) for i in sorted(raw[0]))
        a, p, b = stats
        drift = 100 * (b['mean_us'] / a['mean_us'] - 1)
        effect = 100 * (p['mean_us'] / ((a['mean_us'] + b['mean_us']) / 2) - 1)
        controls.append(dict(node=who, pre_us=a['mean_us'], probe_us=p['mean_us'], post_us=b['mean_us'],
            drift_pct=drift, probe_change_pct=effect, max_cv_pct=max(s['cv_pct'] for s in stats),
            representative=abs(drift) <= 5 and abs(effect) <= 5 and max(s['cv_pct'] for s in stats) <= 5))
    operations, drains = [], []
    for rank in (0, 1):
        windows = base.rows(data['probe', rank]['case'] / 'fadd_path.jsonl')
        if [w['index'] for w in windows] != list(range(10)):
            raise ValueError('expected ten CPU probe windows')
        api = {r['index']: r['us'] for r in base.rows(data['probe', rank]['case'] / 'api_samples.jsonl')}
        for window in windows:
            if (window['version'], window['target_payload'], window['ring'], window['poll_stride'], window['label'], window['clock']) != (1, 6, 0, 1, LABEL, 'CLOCK_MONOTONIC_RAW') or window['us'] != api[window['index']]:
                raise ValueError('wrong probe recipe/API association')
            op, drain = operation_rows(window, rank)
            if window['index']:
                operations.extend(op)
                drains.extend(drain)
    summary = []
    for rank in (0, 1):
        for kind in ('write', 'atomic'):
            rows = [r for r in operations if r['node'] == rank and r['operation'] == kind]
            values = [r['valid_to_sdk_lower_us'] for r in rows if r['observed_valid']]
            summary.append(dict(node=rank, operation=kind, targets=len(rows), observed_valid=len(values),
                censored=len(rows) - len(values), post_to_sdk_median_us=statistics.median(r['post_to_sdk_us'] for r in rows),
                valid_to_sdk_lower_median_us=statistics.median(values) if values else None,
                targets_valid_behind_invalid_head=sum(r['target_valid_head_invalid_polls'] > 0 for r in rows)))
    control_summary = []
    for rank in (0, 1):
        for index in range(1, 10):
            selected = [r for r in drains if r['node'] == rank and r['index'] == index]
            quiet = [r for r in selected if r['kind'] == 'quiet']
            control_summary.append(dict(node=rank, index=index, quiet_count=len(quiet),
                barrier_count=sum(r['kind'] == 'barrier' for r in selected),
                max_single_quiet_cpu_us=max(r['cpu_processing_us'] for r in quiet),
                max_proxy_quiet_cpu_sum_us=max(sum(r['cpu_processing_us'] for r in quiet if r['proxy'] == proxy) for proxy in range(4))))
    decision = dict(mechanism_usable=all(r['representative'] for r in controls), physical_timestamp=False,
        target_observations=len(operations), independent_retained_api_calls_per_node=9,
        note='CQ readability bounds, not device execution timestamps. Missing early valid snapshots remain censored. '
        'A target valid behind an invalid head directly shows CQ consumption blocking, not its Dispatch contribution. '
        'CPU control intervals exclude GPU publication/wait and time before CPU processing. '
        'Overlapping request/proxy waits cannot be added into API latency. Paced controls do not establish continuous-batch performance or a new RDMA gap.')
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in (('controls', controls), ('paired_calls', paired), ('operations', operations),
                       ('operation_summary', summary), ('cpu_control_intervals', drains), ('cpu_control_summary', control_summary)):
        base.write(output / (name + '.csv'), rows)
    (output / 'decision.json').write_text(json.dumps(decision, indent=2), encoding='utf-8')
    (output / 'context.json').write_text(json.dumps(dict(overlay=overlays[0], nodes=identities), indent=2), encoding='utf-8')
    report = ['# WRITE/FADD完成路径与CPU排空观察', '',
        '机制观测门槛通过：' + str(decision['mechanism_usable']), '',
        '| 节点 | 请求 | 目标数 | 捕获有效CQE | 未捕获有效 | 提交→SDK交付中位μs | 已有效→SDK交付下界中位μs | 有效但队首无效目标数 |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in summary:
        report.append('| {node} | {operation} | {targets} | {observed_valid} | {censored} | {post_to_sdk_median_us:.3f} | {valid_to_sdk_lower_median_us} | {targets_valid_behind_invalid_head} |'.format(**r))
    report += ['', 'None表示未观测到有效瞬间，不是零等待。门槛失败时仅保留带探针现象，不作正常执行时延归因。',
               'CPU控制处理不是完整GPU quiet等待；不同请求、Proxy之间存在重叠，不相加为Dispatch关键路径。',
               '首次/最后有效性观测是软件读取界限，不是硬件精确发布时间。', '']
    (output / 'REPORT.zh-CN.md').write_text('\n'.join(report), encoding='utf-8')
    print('NORMAL_COMPLETION_ANALYSIS_PASSED mechanism_usable=' + str(decision['mechanism_usable']))
    return decision


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
