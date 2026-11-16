"""Revalidate raw tokens-sweep results and compare full-call API latency."""
import argparse
import json
from pathlib import Path
import re
import statistics
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]/'tools/reference_events'))
import compare_matched
base = compare_matched.matched.base
import plan


def distribution(values):
    values = sorted(values)
    mean = statistics.mean(values)
    def percentile(q):
        pos = (len(values)-1)*q
        low = int(pos)
        return values[low]+(values[min(low+1, len(values)-1)]-values[low])*(pos-low)
    return dict(n=len(values), mean_us=mean, p50_us=percentile(.5), p95_us=percentile(.95),
                p99_us=percentile(.99), min_us=values[0], max_us=values[-1],
                cv_pct=statistics.pstdev(values)/mean*100)


def load_case(root, rank, backend, tokens):
    data = base.case_data(root, rank, False, reference_events='paced', token_sweep=plan.recipe(tokens))
    c, runtime, case = data['c'], data['runtime'], data['case']
    if data['identity']['backend'] != backend or c['job'] != root.name:
        raise ValueError('wrong backend or case job')
    if runtime.get('normal_max_inflight') != 8 or runtime.get('normal_fifo_retire') not in (None, 'ordered'):
        raise ValueError('tokens sweep changed software window or retirement')
    if backend == 'urma':
        if c.get('normal_fadd_fence_plan') != 'all-on' or runtime.get('normal_fadd_fence') != 'on':
            raise ValueError('tokens sweep requires default FADD fence on')
        if c.get('normal_retire_plan') != 'ordered' or runtime.get('normal_fifo_retire') != 'ordered':
            raise ValueError('tokens sweep requires ordered retirement')
        roles = re.findall(r'\[URMA_FADD_ROLE\] role=(\w+) fence=(\w+) order=(\w+)', (case/'bench.log').read_text())
        if not roles or any(fence != 'on' or order != 'strong' for _, fence, order in roles):
            raise ValueError('missing or changed effective FADD role constraints')
    else:
        config = base.read(case/'config.json')
        if config['shape'] != dict(tokens=tokens, hidden=7168, experts=256, topk=8):
            raise ValueError('wrong frozen verbs shape')
        if config['env'].get('UCCL_EP_DISABLE_CPU_AFFINITY') != '1':
            raise ValueError('tokens sweep changed Proxy affinity')
    if (case/'phases.jsonl').exists() and any(w.get('enabled') for w in base.rows(case/'phases.jsonl')):
        raise ValueError('tokens sweep unexpectedly enabled counters')
    data['values'] = {(r['label'], r['index']):r['us'] for r in base.rows(case/'api_samples.jsonl') if r['used']}
    return data


def load_plan(root, backend, rank):
    location = root/backend
    data = base.read(location/('plan_node%d.json' % rank))
    expected = dict(version=plan.VERSION, job=root.name, backend=backend, node_rank=rank,
                    tokens=list(plan.TOKENS), warmups=plan.WARMUPS, samples=plan.SAMPLES,
                    repeats=plan.REPEATS, cases=plan.cases(root.name, backend),
                    cpu_affinity='inherited', capture='inactive-paced')
    if any(data.get(k) != v for k, v in expected.items()):
        raise ValueError('missing/changed campaign plan: '+str(location))
    if set(data.get('script_hashes', {})) != {'plan.py','runner.py','run.sh','summarize.py'}:
        raise ValueError('missing tokens harness identity')
    if set(data.get('dependency_hashes', {})) != set(plan.DEPENDENCIES):
        raise ValueError('missing measurement dependency identity')
    return data


def collect_backend(root, backend):
    plans = [load_plan(root, backend, rank) for rank in (0, 1)]
    shared = ('version','job','backend','tokens','warmups','samples','repeats','cases','script_hashes','dependency_hashes',
              'expected_kernel','master_addr','port','gid_index','hca','ifname','urma_device','cpu_affinity','capture')
    if any(plans[0][k] != plans[1][k] for k in shared):
        raise ValueError('two-node tokens campaign plan mismatch')
    identities, contexts, per_run, raw, paired = {}, [], [], [], []
    for point in plan.cases(root.name, backend):
        current = []
        for rank in (0, 1):
            data = load_case(root/backend/'cases'/point['job'], rank, backend, point['tokens'])
            if data['c']['kernel'] != plans[rank]['expected_kernel']:
                raise ValueError('runtime kernel differs from tokens plan')
            signature = base.sig(dict(data['identity'], module_sha=data['runtime']['module_sha']))
            if identities.setdefault(rank, signature) != signature:
                raise ValueError('source/benchmark/diagnostics/module/boot/library changed within sweep')
            if point == plan.cases(root.name, backend)[0]:
                contexts.append(dict(node=rank, **data['identity'], module_sha=data['runtime']['module_sha']))
            current.append(data)
            for label in sorted(base.ALL_LABELS):
                values = [v for (l, _), v in sorted(data['values'].items()) if l == label]
                per_run.append(dict(backend=backend, node=str(rank), tokens=point['tokens'],
                                    repeat=point['repeat'], label=label, **distribution(values)))
            raw.extend(dict(backend=backend, node=str(rank), tokens=point['tokens'], repeat=point['repeat'],
                            label=label, index=i, us=value) for (label, i), value in sorted(data['values'].items()))
        for key in ('sources','flags','sdk','libraries','benchmark_sha','diagnostics','torch','maca','gpu_versions'):
            if current[0]['identity'][key] != current[1]['identity'][key]:
                raise ValueError('two-node source/runtime mismatch: '+key)
        if current[0]['values'].keys() != current[1]['values'].keys():
            raise ValueError('unpaired two-node samples')
        for (label, index), value in sorted(current[0]['values'].items()):
            paired.append(dict(backend=backend, node='max_local', tokens=point['tokens'], repeat=point['repeat'],
                               label=label, index=index, us=max(value, current[1]['values'][label,index])))
        for label in sorted(base.ALL_LABELS):
            values = [r['us'] for r in paired if r['tokens']==point['tokens'] and r['repeat']==point['repeat'] and r['label']==label]
            per_run.append(dict(backend=backend, node='max_local', tokens=point['tokens'],
                                repeat=point['repeat'], label=label, **distribution(values)))
    return plans, contexts, per_run, raw, paired


def aggregate(per_run, raw):
    result = []
    for backend, node, tokens, label in sorted({(r['backend'],r['node'],r['tokens'],r['label']) for r in per_run}):
        selected = [r for r in per_run if (r['backend'],r['node'],r['tokens'],r['label'])==(backend,node,tokens,label)]
        values = [r['us'] for r in raw if (r['backend'],r['node'],r['tokens'],r['label'])==(backend,node,tokens,label)]
        means = [r['mean_us'] for r in selected]
        if len(means) != plan.REPEATS or len(values) != plan.REPEATS*(plan.SAMPLES-1):
            raise ValueError('incomplete tokens point')
        main = statistics.median(means)
        spread = (max(means)-min(means))/main*100
        cv = max(r['cv_pct'] for r in selected)
        result.append(dict(backend=backend, node=node, tokens=tokens, label=label,
                           rounds=len(means), median_run_mean_us=main, mean_run_mean_us=statistics.mean(means),
                           min_run_mean_us=min(means), max_run_mean_us=max(means), round_span_pct=spread,
                           max_run_cv_pct=cv, stable=cv<=5 and spread<=5, **distribution(values)))
    return result


def compare(aggregated):
    result = []
    for node in ('0','1','max_local'):
        for tokens in plan.TOKENS:
            for label in sorted(base.ALL_LABELS):
                rows = {r['backend']:r for r in aggregated if (r['node'],r['tokens'],r['label'])==(node,tokens,label)}
                v, u = (rows[b]['median_run_mean_us'] for b in ('verbs','urma'))
                result.append(dict(node=node, tokens=tokens, label=label, verbs_us=v, urma_us=u,
                                   urma_minus_verbs_us=u-v, urma_change_pct=(u/v-1)*100,
                                   stable=all(r['stable'] for r in rows.values()),
                                   verbs_max_cv_pct=rows['verbs']['max_run_cv_pct'],
                                   urma_max_cv_pct=rows['urma']['max_run_cv_pct'],
                                   verbs_round_span_pct=rows['verbs']['round_span_pct'],
                                   urma_round_span_pct=rows['urma']['round_span_pct']))
    return result


def report(aggregated, comparison):
    lines = ['# Normal tokens扫描结果', '',
             '每点3轮，每轮10次预热、31次计时，丢首值后30个有效值。主指标为三轮均值的中位数。', '',
             'tokens为每rank输入数，两rank总输入为其两倍。单位μs；正变化表示URMA更慢。', '',
             'max_local是对应序号两端本地API耗时较大值，不是全局makespan。每点90个样本来自3轮相关采样；P99仅描述样本尾部，不代表稳定尾延迟保证。', '',
             '稳定标记要求每轮CV≤5%，且三轮均值跨度/中位数≤5%；这是工程筛选，不是统计显著性检验。不稳定点保留原值，不自动重跑。', '']
    for label in ('dispatch_bf16_chunk32','dispatch_fp8_chunk32','combine_chunk12','combine_chunk16'):
        lines += ['## '+label, '']
        if comparison:
            lines += ['| tokens/rank | 202 verbs / URMA | 202变化 | 204 verbs / URMA | 204变化 | max_local verbs / URMA | max_local变化 | 三项均稳定 |',
                      '| ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
            for tokens in plan.TOKENS:
                rows = {r['node']:r for r in comparison if r['tokens']==tokens and r['label']==label}
                cells = []
                for node in ('0','1','max_local'):
                    r=rows[node];cells += ['%.2f / %.2f'%(r['verbs_us'],r['urma_us']), '%+.2f%%'%r['urma_change_pct']]
                lines.append('| %d | %s | %s |'%(tokens,' | '.join(cells),all(r['stable'] for r in rows.values())))
        else:
            lines += ['| 后端 | tokens/rank | 202 | 204 | max_local | 三项均稳定 |', '| --- | ---: | ---: | ---: | ---: | --- |']
            for tokens in plan.TOKENS:
                rows = {r['node']:r for r in aggregated if r['tokens']==tokens and r['label']==label}
                lines.append('| %s | %d | %s | %s |'%(next(iter(rows.values()))['backend'],tokens,
                    ' | '.join('%.2f'%rows[n]['median_run_mean_us'] for n in ('0','1','max_local')),
                    all(r['stable'] for r in rows.values())))
        lines.append('')
    lines += ['后端各自在适用内核/设备/SDK下测量；跨后端比较不能隔离成单纯API成本。',
              '这轮回答差距随tokens是否变化，不独立证明fence、FIFO或设备内部的根因；不与历史smoke或旧4096数据拼成曲线。', '']
    return '\n'.join(lines)


def summarize(root, output, backends=('verbs','urma')):
    contexts, runs, raw, paired, plans = {}, [], [], [], {}
    for backend in backends:
        pp, cc, rr, ss, mm = collect_backend(root, backend)
        plans[backend], contexts[backend] = pp, cc
        runs.extend(rr);raw.extend(ss);paired.extend(mm)
    if len(backends)==2:
        if plans['verbs'][0]['script_hashes'] != plans['urma'][0]['script_hashes']:
            raise ValueError('cross-backend tokens harness changed')
        if plans['verbs'][0]['dependency_hashes'] != plans['urma'][0]['dependency_hashes']:
            raise ValueError('cross-backend measurement dependencies changed')
        for v, u in zip(contexts['verbs'], contexts['urma']):
            compare_matched.compatible(v, u)
    aggregated = aggregate(runs, raw+paired)
    comparison = compare(aggregated) if len(backends)==2 else []
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('per_run',runs),('samples',raw),('paired_calls',paired),('aggregate',aggregated)]:
        base.write(output/(name+'.csv'), rows)
    if comparison:
        base.write(output/'comparison.csv', comparison)
    (output/'REPORT.zh-CN.md').write_text(report(aggregated, comparison), encoding='utf-8')
    (output/'context.json').write_text(json.dumps(dict(version=plan.VERSION, nodes=contexts, plans=plans,
        sampling='inactive paced Dispatch; batch CUDA events for layout/Combine; no active tracing',
        primary='median of three per-run means; per-node and matched-index max of local durations',
        no_independent_tail_claim=True), indent=2))
    print('NORMAL_TOKENS_SUMMARY_PASSED '+str(output))
    return aggregated, comparison


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='campaign directory containing verbs/ and/or urma/')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--backend', choices=('verbs','urma','both'), default='both')
    args = parser.parse_args()
    summarize(args.root, args.output, ('verbs','urma') if args.backend=='both' else (args.backend,))
