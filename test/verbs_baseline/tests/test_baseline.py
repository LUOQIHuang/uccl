import ast
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import common
import driver
import summarize


class BaselineTests(unittest.TestCase):
    def test_historical_bundle_matches_validated_benchmark(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            meta = common.prepare(root, 'original')
            for name, expected in common.legacy.EXPECTED.items():
                self.assertEqual(meta['input_hashes'][name], expected)
            for name in common.FILES:
                ast.parse((root / name).read_text())
            original = common.git('show', meta['benchmark_revision'] + ':ep/bench/test_low_latency.py')
            before, after = ast.parse(original), ast.parse((root / 'test_low_latency.py').read_text())
            for tree in (before, after):
                node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'large_gemm_with_hook')
                node.body = []
            self.assertEqual(ast.dump(before, include_attributes=False), ast.dump(after, include_attributes=False))
            self.assertEqual((root / 'buffer.py').read_text(), common.git('show', meta['benchmark_revision'] + ':ep/bench/buffer.py'))

    def test_pure_verbs_build_and_environment(self):
        args = SimpleNamespace(variant='current', hca='roce0', ifname='eth0', gpu='0')
        with patch.dict(os.environ, {'UCCL_EP_DISPATCH_CONFIG': 'bad', 'UCCL_URMA_TM': 'bad', 'LD_PRELOAD': 'bad'}):
            env = driver.environment(args)
        self.assertEqual(env['UCCL_EP_TRANSPORT'], 'verbs')
        self.assertNotIn('UCCL_URMA_TM', env)
        self.assertNotIn('LD_PRELOAD', env)
        self.assertNotIn('UCCL_EP_DISPATCH_CONFIG', env)
        self.assertEqual(driver.BUILD_FLAGS['USE_URMA'], '0')
        self.assertEqual(driver.BUILD_FLAGS['USE_DMABUF'], '1')

    def worker_fixture(self, mode, profile):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / 'benchmark'
            bundle.mkdir()
            case = root / 'r01_baseline' / mode
            case.mkdir(parents=True)
            # A profiler that MUST never be invoked on a baseline path.
            (bundle / 'profile_adapter.py').write_text(
                "def bench_kineto(fn, names, **kwargs):\n"
                "    assert PROFILE_ALLOWED, 'profiler entered baseline'\n"
                "    PROFILE_CALLS.append(names)\n"
                "    fn()\n"
                "    from pathlib import Path\n"
                "    Path(kwargs['trace_path']).write_text('{}')\n"
                "    return ([.001,.002],[.003,.004]) if kwargs.get('num_kernels_per_period')==2 else (.001,.002)\n")
            torch, dist = ModuleType('torch'), ModuleType('torch.distributed')
            class Event:
                def __init__(self, **kwargs): pass
                def record(self): pass
                def elapsed_time(self, end): return 2.0
            torch.cuda = SimpleNamespace(synchronize=lambda: None, current_device=lambda: 0, Event=Event)
            torch.distributed, torch.int = dist, 'int'
            torch.empty = lambda *args, **kwargs: SimpleNamespace(zero_=lambda: None)
            dist.barrier = lambda: None
            utils, buffer = ModuleType('utils'), ModuleType('buffer')
            utils.__file__, buffer.__file__ = str(bundle / 'utils.py'), str(bundle / 'buffer.py')
            utils.PROFILE_ALLOWED, utils.PROFILE_CALLS = profile, []
            class Buffer:
                def __init__(self):
                    self.low_latency_mode = mode != 'normal'
                    self.proxies = [SimpleNamespace(use_normal_mode=lambda: mode == 'normal')]
            buffer.Buffer = Buffer
            calls = Mock()
            def benchmark(path, **kwargs):
                buffer.Buffer()
                utils.bench(calls)
                for i in range(4 if mode == 'normal' else 2):
                    result = utils.bench_kineto(calls, ('dispatch', 'notify') if mode == 'normal' else ('dispatch', 'combine'),
                                                num_kernels_per_period=2 if mode != 'normal' and i == 1 else 1)
                    if mode == 'normal':
                        self.assertGreater(result[0], 0)
                        self.assertGreater(result[1], 0, 'zero notify would skip best-config selection')
            env = dict(VB_CASE=str(case), VB_MODE=mode, VB_PROFILE=str(int(profile)), VB_MODULE='fake.so',
                       VB_SHA='sha', VB_WARMUPS='2', VB_SAMPLES='4')
            modules = {'torch': torch, 'torch.distributed': dist, 'utils': utils, 'buffer': buffer}
            with patch.dict(os.environ, env), patch.dict(sys.modules, modules), \
                 patch.object(common, 'load_extension', return_value=(SimpleNamespace(), dict(module_sha='sha'))), \
                 patch.object(runpy, 'run_path', side_effect=benchmark), redirect_stdout(io.StringIO()):
                # Execute the real worker while replacing only GPU/distributed
                # dependencies and the benchmark body with CPU test doubles.
                exec(compile((HERE / 'worker.py').read_text(), str(HERE / 'worker.py'), 'exec'), {'__name__': '__main__'})
            self.assertEqual(len(utils.PROFILE_CALLS), (4 if mode == 'normal' else 2) if profile else 0)
            measurements = 5 if mode == 'normal' else 1
            self.assertEqual(calls.call_count, measurements * 6 + (4 if mode == 'normal' else 2) * int(profile))
            common.validate_samples(case, mode, 4)
            rows = [json.loads(line) for line in (case / 'sample_summary.jsonl').read_text().splitlines()]
            self.assertTrue(all(r['n'] == 3 and r['mean_us'] == 2000 for r in rows))
            if not profile:
                self.assertFalse((case / 'gpu_stages.jsonl').exists())
                self.assertFalse(list(case.glob('gpu_trace*')))

    def test_baseline_normal_never_invokes_profiler(self): self.worker_fixture('normal', False)
    def test_baseline_ll_skips_hook_and_profiler(self): self.worker_fixture('low_latency', False)
    def test_gpu_profile_normal_is_separate(self): self.worker_fixture('normal', True)
    def test_gpu_profile_ll_retains_send_receive_periods(self): self.worker_fixture('low_latency', True)

    def test_original_preflight_rejects_unrecognized_module(self):
        args = SimpleNamespace(variant='original', module=Path('missing.so'), expected_kernel=None)
        with self.assertRaisesRegex(RuntimeError, 'SHA_MISMATCH'):
            driver.preflight(args)

    def test_pair_failure_stops_remaining_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(output=Path(tmp), job='test', node_rank=0, mode='both', repeats=3,
                warmups=2, samples=4, no_profile=False, profile_only=False, hca='fake', ifname='eth0', gpu='0', variant='original')
            result = (Path('fake.so'), {}, {}, {'torch': 'fake', 'module_sha': 'sha'}, '')
            with patch.object(driver, 'preflight', return_value=result), patch.object(common, 'prepare', return_value={}), \
                 patch.object(common, 'diagnostics', return_value={}), patch.object(driver.legacy, 'snapshot_host'), \
                 patch.object(driver.legacy, 'pair'), patch.object(driver.os, 'uname', create=True, return_value=SimpleNamespace(release='test-kernel')), \
                 patch.object(driver, 'run_case', side_effect=RuntimeError('PAIR_STOP')) as case:
                with self.assertRaisesRegex(RuntimeError, 'PAIR_STOP'):
                    driver.run(args)
            self.assertEqual(case.call_count, 1)

    def test_default_schedule_has_six_baselines_then_two_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(output=Path(tmp), job='test', node_rank=0, mode='both', repeats=3,
                warmups=2, samples=4, no_profile=False, profile_only=False, hca='fake', ifname='eth0', gpu='0', variant='original')
            result = (Path('fake.so'), {}, {}, {'torch': 'fake', 'module_sha': 'sha'}, '')
            with patch.object(driver, 'preflight', return_value=result), patch.object(common, 'prepare', return_value={}), \
                 patch.object(common, 'diagnostics', return_value={}), patch.object(driver.legacy, 'snapshot_host'), \
                 patch.object(driver.legacy, 'pair'), patch.object(driver.os, 'uname', create=True, return_value=SimpleNamespace(release='test-kernel')), \
                 patch.object(driver, 'run_case') as case, redirect_stdout(io.StringIO()):
                driver.run(args)
            expected = [(f'r{r:02d}_baseline', mode, False) for r in range(1, 4) for mode in ('normal', 'low_latency')]
            expected += [('gpu_profile', mode, True) for mode in ('normal', 'low_latency')]
            self.assertEqual([(c.args[2], c.args[3], c.args[4]) for c in case.call_args_list], expected)

    def test_collect_excludes_binaries_and_cache_and_rejects_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            case = root / 'raw/job/node0/r01_baseline/normal'
            case.mkdir(parents=True)
            for name in ('bench.log', 'status.json', 'api_samples.jsonl', 'extension.so'):
                (case / name).write_text('test')
            (case / 'cache').mkdir()
            (case / 'cache/skip.json').write_text('cache')
            args = SimpleNamespace(output=root / 'raw', job='job', node_rank=0)
            with patch.object(driver, 'HERE', root / 'scripts'), redirect_stdout(io.StringIO()):
                driver.collect(args)
                out = root / 'scripts/results/job/node0/r01_baseline/normal'
                self.assertTrue((out / 'api_samples.jsonl').exists())
                self.assertFalse((out / 'extension.so').exists())
                self.assertFalse((out / 'cache').exists())
                (case / 'bench.log').write_text('changed')
                with self.assertRaisesRegex(RuntimeError, 'RESULT_CONFLICT'): driver.collect(args)

    def test_current_build_records_pure_verbs_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(build_dir=root, variant='current', build_jobs=2, hca='fake', ifname='eth0', gpu='0')
            def compiler(command, **kwargs):
                self.assertEqual(kwargs['env']['USE_URMA'], '0')
                self.assertNotIn('URMA_INCLUDE_DIR', kwargs['env'])
                if 'host_tests.py' in command[1]:return
                module = root / 'lib/ep.abi3.so'
                module.parent.mkdir()
                module.write_bytes(b'test extension')
            with patch.object(common, 'sources', return_value={'code.cpp': 'sha'}), patch.object(common, 'git', return_value='revision'), \
                 patch.object(driver.subprocess, 'run', side_effect=compiler), redirect_stdout(io.StringIO()):
                driver.build(args)
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(manifest['flags']['USE_URMA'], '0')
            with patch.object(common, 'sources', return_value={'code.cpp': 'changed'}):
                with self.assertRaisesRegex(RuntimeError, 'BUILD_CHANGED'): driver.build(args)

    def make_job(self, node, host='node202', profile=False):
        node.mkdir(parents=True)
        meta = dict(job='job', variant='original', mode='low_latency', repeats=3,
                    samples=4, no_profile=not profile, profile_only=False, kernel='verbs-kernel')
        (node / 'setup.json').write_text(json.dumps(dict(error=None, fingerprint=meta)))
        (node / 'host.json').write_text(json.dumps(dict(host=host, boot_id='boot')))
        tags = ['r01_baseline', 'r02_baseline', 'r03_baseline'] + (['gpu_profile'] if profile else [])
        for i, tag in enumerate(tags):
            case = node / tag / 'low_latency'
            case.mkdir(parents=True)
            (case / 'status.json').write_text(json.dumps(dict(rc=0, checks=dict(ok=True))))
            (case / 'config.json').write_text(json.dumps(dict(profile=tag == 'gpu_profile')))
            values = [dict(label='ll_no_hook_dispatch_combine', index=j, used=j > 0, us=100 + i) for j in range(4)]
            (case / 'api_samples.jsonl').write_text('\n'.join(json.dumps(v) for v in values))
            if tag == 'gpu_profile':
                (case / 'gpu_stages.jsonl').write_text(json.dumps(dict(label='ll_no_hook', kernel_names=['dispatch', 'combine'], kernel_seconds=[.001, .002])) + '\n')

    def test_summary_separates_ranks_and_profiles_and_rejects_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rank in (0, 1): self.make_job(root / 'job' / f'node{rank}', 'host' + str(rank), True)
            runs, summary, stages = summarize.summarize([root / 'job'], root / 'summary')
            self.assertEqual(len(runs), 8)
            self.assertEqual(len(summary), 2)
            self.assertTrue(all(r['independent_runs'] == 3 and r['median_run_mean_us'] == 101 for r in summary))
            self.assertEqual(len(stages), 4)
            (root / 'job/node1/r03_baseline/low_latency/status.json').unlink()
            with self.assertRaises(FileNotFoundError): summarize.summarize([root / 'job'], root / 'summary')

    def test_sample_validation_rejects_truncation_and_bad_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [dict(label='ll_no_hook_dispatch_combine', index=i, used=i > 0, us=100) for i in range(4)]
            path = root / 'api_samples.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in rows[:-1]))
            with self.assertRaisesRegex(ValueError, 'incomplete'): common.validate_samples(root, 'low_latency', 4)
            rows[-1]['us'] = float('nan')
            path.write_text('\n'.join(json.dumps(row) for row in rows))
            with self.assertRaisesRegex(ValueError, 'invalid'): common.validate_samples(root, 'low_latency', 4)


if __name__ == '__main__':
    unittest.main()
