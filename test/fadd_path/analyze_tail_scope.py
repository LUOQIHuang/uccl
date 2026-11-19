"""Review the narrow URMA fence comparison from the two nodes' raw logs."""
import argparse
import json
from pathlib import Path
import statistics

import analyze
import overlay
from runner import TAIL_RUNS

base = analyze.base
LABEL = analyze.LABEL


def receiver_rows(case, api):
    rows = base.rows(case / 'tail_receiver.jsonl')
    if [r['index'] for r in rows] != list(range(10)):
        raise ValueError('expected ten rank0 receiver windows')
    expected = {0: set(range(1, 24, 2)), 1: set(range(1, 24, 2)), 2: set(range(0, 24, 2))}
    result = []
    for row in rows:
        i, gpu = row['index'], row['gpu']
        clock = gpu['clock_stats']
        if (row['label'], row['us']) != (LABEL, api[i]) or gpu['version'] != 1 or gpu['epoch'] != i+1:
            raise ValueError('receiver window does not match timed BF16 invocation')
        if gpu['clock'] != 'CLOCK_MONOTONIC_RAW' or gpu.get('profile') is not None or gpu['dropped'] or gpu['counts'] != [12, 12, 12, 0, 0, 0]:
            raise ValueError('invalid receiver capture clock/profile/coverage')
        stamps = {e: {} for e in expected}
        slots = set()
        for rec in gpu['records']:
            event, block = rec['event'], rec['block']
            slot = (event, rec['slot'])
            if (event not in expected or block not in expected[event] or block in stamps[event]
                    or slot in slots or not 0 <= rec['slot'] < 12 or rec['epoch'] != gpu['epoch']
                    or rec['host_ns'] <= 0 or rec['device_before'] > rec['device_after']):
                raise ValueError('wrong or duplicate receiver event')
            slots.add(slot)
            stamps[event][block] = rec['host_ns']
        if any(set(stamps[e]) != blocks for e, blocks in expected.items()):
            raise ValueError('missing sender/receiver channel')
        first_token, first_write, last_visible = min(stamps[0].values()), min(stamps[1].values()), max(stamps[2].values())
        if not first_token <= first_write <= last_visible:
            raise ValueError('invalid rank0 local GPU event chronology')
        if i:
            result.append(dict(index=i, first_token_to_last_visible_us=(last_visible-first_token)/1000,
                               first_write_to_last_visible_us=(last_visible-first_write)/1000,
                               clock_max_gap_us=clock['max_gap_ns']/1000,
                               clock_ok=clock['updates']>0 and clock['scheduler_error']==0 and clock['max_gap_ns']>0))
    return result


def control_row(plan, node, pre, probe, post):
    controls = (pre['mean_us']+post['mean_us'])/2
    drift = (post['mean_us']/pre['mean_us']-1)*100
    change = (probe['mean_us']/controls-1)*100
    cv = max(r['cv_pct'] for r in (pre, probe, post))
    return dict(plan=plan, node=node, pre_us=pre['mean_us'], probe_us=probe['mean_us'], post_us=post['mean_us'],
                control_drift_pct=drift, probe_change_pct=change, max_cv_pct=cv,
                representative=max(abs(drift), abs(change), cv)<=5)


def value_stats(values):
    mean = statistics.mean(values)
    return dict(mean_us=mean, cv_pct=statistics.pstdev(values)/mean*100)


def summarize(root, output):
    campaign = root / 'urma'
    overlays = [base.read(campaign / f'overlay_node{rank}.json') for rank in (0, 1)]
    if (overlays[0]!=overlays[1] or not overlays[0].get('tail_scope') or overlays[0]['poll_stride']!=1
            or not overlays[0]['base'].startswith(overlay.TAIL_BASE)):
        raise ValueError('wrong or different two-node tail-scope source overlays')
    samples, stats, identities = {}, {}, {}
    pairs, receiver, calls = [], [], []
    for suffix, plan, active in TAIL_RUNS:
        job = f'{root.name}_urma_{suffix}'
        for rank in (0, 1):
            data = base.case_data(campaign/'cases'/job, rank, False, reference_events='paced', bf16_only=True)
            case, runtime, config = data['case'], data['runtime'], data['c']
            status = base.read(case/'status.json')
            if (config['job']!=job or config['normal_fadd_fence_plan']!=plan or config['fadd_path_capture']!=active
                    or runtime.get('fadd_path_version')!=1 or runtime.get('normal_atomic_role_version')!=1
                    or not status['checks'].get('normal_atomic_roles')):
                raise ValueError('wrong job, observer, or actual FADD role capability')
            identity = dict(data['identity'], module_sha=runtime['module_sha'])
            identity.pop('normal_fadd_fence_plan')
            identity.pop('normal_fadd_fence')
            if identity!=identities.setdefault(rank, identity):
                raise ValueError('module, source, boot, runtime or benchmark changed between cases')
            for name, owner in (('fadd_path.jsonl', 1), ('tail_receiver.jsonl', 0)):
                if (case/name).is_file()!=(active and rank==owner):
                    raise ValueError(f'wrong probe ownership/presence: {case/name}')
            api = {r['index']:r['us'] for r in base.rows(case/'api_samples.jsonl')}
            samples[suffix, rank] = api
            stats[suffix, rank] = data['means'][LABEL]
            if active and rank==0:
                receiver.extend(dict(plan=plan, **r) for r in receiver_rows(case, api))
            if active and rank==1:
                pairs.extend(dict(plan=plan, **r) for r in analyze.capture_rows(case, 'urma', 1, tail_fence=int(plan=='all-on')))
        paired = [dict(case=suffix, plan=plan, index=i, node202_us=samples[suffix,0][i],
                       node204_us=samples[suffix,1][i], paired_max_us=max(samples[suffix,0][i], samples[suffix,1][i]))
                  for i in range(1, 10)]
        calls.extend(paired)
        stats[suffix, 'paired_max'] = value_stats([r['paired_max_us'] for r in paired])

    controls = [control_row(plan, node, *(stats[prefix+'_'+s,node] for s in ('pre','probe','post')))
                for prefix, plan in (('all','all-on'),('tail','peer-tail-off')) for node in (0,1,'paired_max')]
    contrast = {}
    for phase in ('pre', 'post'):
        a, c = (stats[prefix+'_'+phase,'paired_max']['mean_us'] for prefix in ('all','tail'))
        contrast[phase] = dict(all_on_us=a, tail_off_us=c, saved_us=a-c, saved_pct=(a-c)/a*100)
    differential = {str(node):abs(next(r['probe_change_pct'] for r in controls if r['node']==node and r['plan']=='all-on')
                                 -next(r['probe_change_pct'] for r in controls if r['node']==node and r['plan']=='peer-tail-off'))
                    for node in (0,1,'paired_max')}
    representative = all(r['representative'] for r in controls) and max(differential.values())<=1

    stages = []
    for plan in ('all-on','peer-tail-off'):
        rr = [r for r in receiver if r['plan']==plan]
        pp = [p for p in pairs if p['plan']==plan]
        stage = dict(plan=plan, pairs=len(pp), return_bounds=sum(p['result_to_cqe_upper_us'] is not None for p in pp),
                     return_seen_before_cqe=sum(p['result_observed_before_cqe'] for p in pp),
                     max_mapped_clock_gap_us=max(r['clock_max_gap_us'] for r in rr), clock_ok=all(r['clock_ok'] for r in rr))
        for field in ('submit_to_cqe_us','submit_to_result_lower_us','submit_to_result_upper_us','result_to_cqe_lower_us',
                      'result_to_cqe_upper_us','empty_polls_after_observed_result','gap_us','first_token_to_last_visible_us',
                      'first_write_to_last_visible_us'):
            valid = [r[field] for r in pp+rr if r.get(field) is not None]
            stage['median_'+field] = statistics.median(valid) if valid else None
        stages.append(stage)
    a, c = stages
    receiver_delta = a['median_first_write_to_last_visible_us']-c['median_first_write_to_last_visible_us']
    receiver_clock_screen = a['max_mapped_clock_gap_us']+c['max_mapped_clock_gap_us']
    receiver_usable = representative and all(r['clock_ok'] for r in stages)
    decision = dict(mechanism_usable=representative, receiver_usable=receiver_usable,
                    receiver_clock_screen_us=receiver_clock_screen,
                    receiver_shift_exceeds_clock_screen=receiver_usable and abs(receiver_delta)>receiver_clock_screen,
                    receiver_first_write_span_saved_us=receiver_delta,
                    differential_observer_effect_pp=differential, inactive_bf16_contrasts=contrast,
                    physical_cqe_readiness_measured=False)
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('controls',controls),('paired_calls',calls),('fadd_pairs_204',pairs),('receiver_202',receiver),('stages',stages)]:
        base.write(output/(name+'.csv'), rows)
    (output/'decision.json').write_text(json.dumps(decision,indent=2)+'\n',encoding='utf-8')
    (output/'context.json').write_text(json.dumps(dict(nodes=identities,overlay=overlays[0]),indent=2)+'\n',encoding='utf-8')
    report(output, decision, stages)
    print(f'FADD_TAIL_SCOPE_ANALYSIS_PASSED mechanism_usable={representative} receiver_usable={receiver_usable}')
    return decision


def report(output, decision, stages):
    lines = ['# URMA Normal BF16 tail fence 对照', '',
             f"阶段归因门槛通过：{decision['mechanism_usable']}。202 接收时钟门槛通过：{decision['receiver_usable']}。", '',
             '| 无探针配对 | 全开 μs | 只关 tail μs | 节省 μs | 节省 % |',
             '| --- | ---: | ---: | ---: | ---: |']
    for phase, row in decision['inactive_bf16_contrasts'].items():
        lines.append(f"| {phase} | {row['all_on_us']:.2f} | {row['tail_off_us']:.2f} | {row['saved_us']:.2f} | {row['saved_pct']:.2f} |")
    lines.extend(['', '| 阶段观测 | 全开 | 只关 tail |', '| --- | ---: | ---: |'])
    for label, key in [('204 FADD 提交开始 → CQE 观测中位 μs','median_submit_to_cqe_us'),
                       ('204 提交开始 → 返回值可读区间中位下界 μs','median_submit_to_result_lower_us'),
                       ('204 提交开始 → 返回值可读区间中位上界 μs','median_submit_to_result_upper_us'),
                       ('204 返回值可读 → CQE 观测中位下界 μs','median_result_to_cqe_lower_us'),
                       ('204 返回值可读 → CQE 观测中位上界 μs','median_result_to_cqe_upper_us'),
                       ('204 可约束返回值时间的配对数（共36）','return_bounds'),
                       ('204 在 CQE 前确实读到返回值的配对数','return_seen_before_cqe'),
                       ('204 读到返回值之后，SDK 空轮询次数中位数','median_empty_polls_after_observed_result'),
                       ('204 WRITE CQE → FADD CQE 观测，中位 μs','median_gap_us'),
                       ('202 首次本地 WRITE → 最后远端 token 可消费，中位 μs','median_first_write_to_last_visible_us'),
                       ('202 首个本地 token → 最后远端 token 可消费，中位 μs','median_first_token_to_last_visible_us'),
                       ('202 最大映射时钟更新间隔 μs','max_mapped_clock_gap_us')]:
        values = ['—' if r[key] is None else f'{r[key]:.2f}' for r in stages]
        lines.append(f'| {label} | {values[0]} | {values[1]} |')
    lines.extend(['', 'CQE 是 SDK 返回的本地完成观测。返回值晚于 CQE 首次被读到时仅给出界限，零下界不表示没有等待。',
                  '202 的两个时间跨度来自同机 GPU 事件。两端只有调用序号相同，各自的时间戳不跨机相减。映射时钟最大间隔之和用于筛查小差异。',
                  '阶段归因门槛失败时，仅报告带观察器的现象。纯性能以关闭探针的两组配对为准。', ''])
    (output/'REPORT.zh-CN.md').write_text('\n'.join(lines),encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
