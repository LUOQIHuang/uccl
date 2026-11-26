"""Measure how much the URMA tail-fence change closes the RDMA Dispatch gap."""

import argparse
import json
from pathlib import Path
from statistics import mean, pstdev

from summarize_matched import base


KERNEL = '6.6.0-gdr_f00955680_diag'
LABEL = 'dispatch_bf16_chunk32'
CASES = {
    'rdma_pre': 'verbs',
    'urma_all_on_a1': 'all-on',
    'urma_tail_off_b1': 'peer-tail-off',
    'urma_tail_off_b2': 'peer-tail-off',
    'urma_all_on_a2': 'all-on',
    'rdma_post': 'verbs',
}
SHARED_RUNTIME = ('host', 'boot', 'kernel', 'torch', 'maca', 'gpu_versions', 'benchmark_sha', 'ifname', 'gpu')
SHARED_FLAGS = ('USE_METAX', 'USE_DMABUF', 'MAX_NUM_GPUS', 'NUM_MAX_NVL_PEERS', 'PER_EXPERT_BATCHING')
NATIVE_SUFFIXES = {'.h', '.hpp', '.cuh', '.cpp', '.cc', '.cu'}


def comparable(data, name, plan, rank):
    identity = dict(data['identity'])
    backend = 'verbs' if plan == 'verbs' else 'urma'
    if data['c']['job'] != name or identity['backend'] != backend or identity['kernel'] != KERNEL:
        raise ValueError(f'wrong case or current kernel: {name}, rank {rank}')
    if (identity['reference_profile'] != 'legacy' or identity['normal_window_plan'] != 'default'
            or identity['normal_retire_plan'] != 'ordered' or data['c'].get('normal_submit_witness')):
        raise ValueError(f'wrong timing or queue configuration: {name}, rank {rank}')
    if identity['flags'].get('USE_URMA') != ('0' if backend == 'verbs' else '1'):
        raise ValueError(f'wrong compiled backend: {name}, rank {rank}')
    if backend == 'verbs':
        status = base.read(data['case'] / 'status.json')
        if not status['checks'].get('dmabuf') or identity['flags'].get('USE_DMABUF') != '1':
            raise ValueError(f'GPU DMA-BUF not verified: {name}, rank {rank}')
        log = (data['case'] / 'bench.log').read_text(errors='replace')
        if not identity['hca'] or f"Selected NIC {identity['hca']} (" not in log:
            raise ValueError(f'configured verbs NIC not observed: {name}, rank {rank}')
    else:
        if identity.pop('normal_fadd_fence_plan') != plan or identity['normal_submit_schedule'] != 'lane-mixed':
            raise ValueError(f'wrong URMA fence or submit plan: {name}, rank {rank}')
        identity.pop('normal_fadd_fence')  # Effective per-rank setting is checked by case_data.
        if identity['device'] != ('udmac1d1e2', 'udmac0d1e2')[rank]:
            raise ValueError(f'wrong URMA device: {name}, rank {rank}')
    return identity


def shared_signature(identity):
    return {
        **{key: identity[key] for key in SHARED_RUNTIME},
        'flags': {key: identity['flags'].get(key) for key in SHARED_FLAGS},
        'sources': {key: value for key, value in identity['sources'].items()
                    if key == 'ep/setup.py' or Path(key).suffix in NATIVE_SUFFIXES},
    }


def drift_pct(first, second):
    return abs(first - second) / mean((first, second)) * 100


def gap_metrics(rounds):
    r1, a1, b1, b2, a2, r2 = (rounds[name]['mean_us'] for name in CASES)
    rdma, before, after = mean((r1, r2)), mean((a1, a2)), mean((b1, b2))
    old_gap, new_gap, recovered = before - rdma, after - rdma, before - after
    cv = max(row['max_cv_pct'] for row in rounds.values())
    drift = dict(rdma=drift_pct(r1, r2), all_on=drift_pct(a1, a2), tail_off=drift_pct(b1, b2))
    return dict(
        rdma_us=rdma, urma_before_us=before, urma_after_us=after,
        old_gap_us=old_gap, new_gap_us=new_gap, recovered_us=recovered,
        old_gap_pct_of_rdma=old_gap / rdma * 100, new_gap_pct_of_rdma=new_gap / rdma * 100,
        recovered_pct_of_old_gap=recovered / old_gap * 100 if old_gap > 0 else None,
        improvement_pct_of_urma_before=recovered / before * 100,
        abba_pair_savings_us=[a1 - b1, a2 - b2], max_cv_pct=cv, repeat_drift_pct=drift,
        stable_controls=cv <= 5 and max(drift.values()) <= 3,
        both_pairs_improved=a1 > b1 and a2 > b2,
    )


def summarize(root, output):
    backend_signatures, common_signatures = {}, {}
    rounds, paired_calls, contexts = {}, [], []
    for name, plan in CASES.items():
        samples, rank_cvs = [], []
        for rank in (0, 1):
            data = base.case_data(root / name, rank, False, reference_events='paced')
            identity = comparable(data, name, plan, rank)
            module_sha = data['runtime']['module_sha']
            signature = (identity, module_sha)
            if backend_signatures.setdefault((rank, identity['backend']), signature) != signature:
                raise ValueError(f'backend build/setup changed: {name}, rank {rank}')
            shared = shared_signature(identity)
            prior = common_signatures.setdefault(rank, shared)
            differences = [key for key in prior if prior[key] != shared[key]]
            if differences:
                raise ValueError(f'cross-backend environment/workload changed: {name}, rank {rank}, {differences}')
            contexts.append(dict(case=name, rank=rank, module_sha=module_sha, **data['identity']))
            samples.append({row['index']: row['us'] for row in base.rows(data['case'] / 'api_samples.jsonl')
                            if row['label'] == LABEL and row['used']})
            rank_cvs.append(data['means'][LABEL]['cv_pct'])
        if samples[0].keys() != samples[1].keys():
            raise ValueError(f'unpaired BF16 samples: {name}')
        values = []
        for index, rank0_us in samples[0].items():
            rank1_us = samples[1][index]
            duration = max(rank0_us, rank1_us)
            paired_calls.append(dict(case=name, index=index, rank0_us=rank0_us, rank1_us=rank1_us, max_us=duration))
            values.append(duration)
        avg = mean(values)
        rounds[name] = dict(case=name, mean_us=avg, max_cv_pct=max(*rank_cvs, pstdev(values) / avg * 100))

    result = gap_metrics(rounds)
    output.mkdir(parents=True, exist_ok=False)
    base.write(output / 'rounds.csv', list(rounds.values()))
    base.write(output / 'paired_calls.csv', paired_calls)
    (output / 'context.json').write_text(json.dumps(contexts, indent=2) + '\n', encoding='utf-8')
    (output / 'gap.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))
    print(f'SAME_KERNEL_GAP_SUMMARY_WRITTEN {output}')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    summarize(args.root, args.output)
