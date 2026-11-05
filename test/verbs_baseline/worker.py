"""Run compatible real benchmarks; baseline never invokes Kineto."""
import faulthandler
import glob
import json
import os
from pathlib import Path
import runpy
import sys
from datetime import timedelta
import common

faulthandler.enable()
case = Path(os.environ['VB_CASE'])
bundle = case.parents[1] / 'benchmark'
mode = os.environ['VB_MODE']
profile = os.environ['VB_PROFILE'] == '1'
warmups, samples = int(os.environ['VB_WARMUPS']), int(os.environ['VB_SAMPLES'])
ep, runtime = common.load_extension(Path(os.environ['VB_MODULE']))
assert runtime['module_sha'] == os.environ['VB_SHA'], 'extension changed after preflight'
(case / 'runtime.json').write_text(json.dumps(runtime, indent=2))
if hasattr(ep, 'urma_phase_enable'):
    ep.urma_phase_enable(False)
sys.path.insert(0, str(bundle))
import torch
import torch.distributed as dist
import utils
import buffer
assert Path(buffer.__file__).resolve().parent == bundle.resolve()
assert Path(utils.__file__).resolve().parent == bundle.resolve()


def init(local_rank, num_local_ranks):
    torch.cuda.set_device(local_rank)
    dist.init_process_group(backend='nccl', timeout=timedelta(seconds=180))
    torch.set_default_dtype(torch.bfloat16)
    torch.set_default_device(f'cuda:{local_rank}')
    return dist.get_rank(), dist.get_world_size(), dist.new_group(list(range(dist.get_world_size())))


utils.init_dist_under_torchrun = init
original_init = buffer.Buffer.__init__


def checked_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    expected = mode == 'normal'
    assert self.low_latency_mode == (not expected), 'Buffer mode mismatch'
    assert self.proxies and all(bool(p.use_normal_mode()) == expected for p in self.proxies), 'Proxy mode mismatch'
    print(f'VERBS_MODE_OK mode={mode} proxies={len(self.proxies)}', flush=True)


buffer.Buffer.__init__ = checked_init
original_glob = glob.glob


def scoped_glob(pattern, *args, **kwargs):
    if isinstance(pattern, str) and pattern.startswith('/dev/shm/uccl_barrier_*'):
        return []
    return original_glob(pattern, *args, **kwargs)


glob.glob = scoped_glob


def append(name, data):
    with (case / name).open('a') as stream:
        stream.write(json.dumps(data) + '\n')


def measure(fn, label, post_fn=None):
    torch.cuda.synchronize()
    dist.barrier()
    cache = torch.empty(int(256e6 // 4), dtype=torch.int, device=f'cuda:{torch.cuda.current_device()}')
    for _ in range(warmups):
        fn()
    cache.zero_()
    torch.cuda.synchronize()
    dist.barrier()
    start = [torch.cuda.Event(enable_timing=True) for _ in range(samples)]
    end = [torch.cuda.Event(enable_timing=True) for _ in range(samples)]
    for i in range(samples):
        start[i].record()
        fn()
        end[i].record()
        if post_fn is not None:
            post_fn()
    torch.cuda.synchronize()
    values = [s.elapsed_time(e) * 1000 for s, e in zip(start, end)]
    for i, value in enumerate(values):
        append('api_samples.jsonl', dict(label=label, mode=mode, index=i, us=value, used=i > 0))
    summary = dict(label=label, mode=mode, profile=profile, warmups=warmups, measured=samples,
                   dropped=1, **common.phase_support.distribution(values[1:]))
    append('sample_summary.jsonl', summary)
    print('VERBS_SAMPLES ' + json.dumps(summary), flush=True)
    dist.barrier()
    return summary['mean_us'] / 1e6, summary['min_us'] / 1e6, summary['max_us'] / 1e6


def measured_api(fn, *args, **kwargs):
    if args or any(k not in ('num_warmups', 'num_tests', 'post_fn') for k in kwargs):
        raise RuntimeError('unexpected bench signature')
    return measure(fn, 'layout' if mode == 'normal' else 'll_no_hook_dispatch_combine', kwargs.get('post_fn'))


utils.bench = measured_api
exec(compile((bundle / 'profile_adapter.py').read_text(), str(bundle / 'profile_adapter.py'), 'exec'), utils.__dict__)
original_profile = utils.bench_kineto
profile_count = 0


def measured_profile(fn, kernel_names, *args, **kwargs):
    global profile_count
    index = profile_count
    profile_count += 1
    label = f'{common.NORMAL_PLAN[index][0]}_chunk{common.NORMAL_PLAN[index][1]}' if mode == 'normal' else ('ll_no_hook' if index == 0 else 'll_immediate_hook')
    # The original benchmark uses these returned values only for tuning/printing.
    # On baseline Normal runs return whole-call time plus a nonzero sentinel
    # notify so the benchmark's best-config selection still executes. These
    # console tuning fields are placeholders, never GPU stage measurements.
    timing = measure(fn, label) if mode == 'normal' else None
    if not profile:
        print('VERBS_PROFILE_SKIPPED ' + label, flush=True)
        if mode == 'normal':
            return timing[0], 1e-12
        return (0.0, 0.0) if index == 0 else ([0.0, 0.0], [0.0, 0.0])
    kwargs['trace_path'] = str(case / f'gpu_trace_{index}.json')
    torch.cuda.synchronize()
    dist.barrier()
    result = original_profile(fn, kernel_names, *args, **kwargs)
    def positive(value):
        return all(positive(v) for v in value) if isinstance(value, (list, tuple)) else 0 < value < float('inf')
    if not positive(result):
        raise RuntimeError('EMPTY_GPU_PROFILE: ' + label)
    append('gpu_stages.jsonl', dict(label=label, mode=mode, kernel_names=kernel_names,
                                   kernel_seconds=result, hook_workload='immediate',
                                   note='GPU durations include waits; overlapping stages are not additive'))
    print('VERBS_GPU_PROFILE ' + label, flush=True)
    dist.barrier()
    return result


utils.bench_kineto = measured_profile
sys.argv = [str(bundle / ('test_internode.py' if mode == 'normal' else 'test_low_latency.py')),
            '--num-processes=1', '--num-tokens=' + ('4096' if mode == 'normal' else '128'),
            '--hidden=7168', '--num-experts=256', '--num-topk=8', '--pressure-test-mode=0']
if mode == 'normal':
    sys.argv += ['--num-topk-groups=2', '--test-ll-compatibility']
print('VERBS_MEASUREMENT_CONFIG ' + json.dumps(dict(profile=profile, warmups=warmups, samples=samples)), flush=True)
runpy.run_path(sys.argv[0], run_name='__main__')
assert profile_count == (4 if mode == 'normal' else 2), profile_count
print('VERBS_BASELINE_DONE', flush=True)
