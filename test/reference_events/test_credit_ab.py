"""Credit experiment: effective-setting rejection, provenance and fail-stop."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

from test_window_plan import driver_functions


class CreditPlanTests(unittest.TestCase):
    def arguments(self):
        return ['driver', '--node-rank', '1', '--job', 'credit', '--mode', 'normal',
                '--phase-suite', '--api-only', '--dispatch-focus', '--reference-events', 'batch',
                '--reference-profile', 'boundaries', '--normal-fadd-fence-plan', 'peer-tail-off',
                '--normal-window-plan', 'both32', '--warmups', '5', '--samples', '10']

    def test_exact_both_node_window_and_incompatible_paths(self):
        ns = driver_functions('urma')
        with patch.object(sys, 'argv', self.arguments()):
            args = ns['parser']()
        with patch.dict(os.environ, {'UCCL_IB_MAX_INFLIGHT_NORMAL': '4'}):
            for rank in (0, 1):
                args.node_rank = rank
                env = ns['environment'](args)
                self.assertEqual(env['UCCL_IB_MAX_INFLIGHT_NORMAL'], '32')
                self.assertEqual(env['UCCL_URMA_NORMAL_FADD_FENCE'], 'tail-off' if rank else 'on')
                self.assertEqual(env['UCCL_URMA_NORMAL_RETIRE'], 'ordered')
        for extra in (['--reference-events', 'on'], ['--reference-events', 'paced'],
                      ['--reference-profile', 'legacy'], ['--normal-fadd-fence-plan', 'all-on'],
                      ['--normal-retire-plan', 'peer-ready'], ['--mode', 'low_latency'],
                      ['--normal-submit-schedule', 'writes-first'], ['--normal-tokens', '128'],
                      ['--warmups', '1'], ['--action', 'build']):
            with patch.object(sys, 'argv', self.arguments() + extra), patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
                ns['parser']()

    def test_preflight_rejects_ignored_window(self):
        ns = driver_functions('urma')
        with patch.object(sys, 'argv', self.arguments()):
            args = ns['parser']()
        ns['support'] = NS(read_build=lambda p: (Path('ep.so'), dict(sources={}, module_sha='sha',
            flags={'UCCL_REFERENCE_EVENTS': '1', 'UCCL_GPU_STAGE_TRACE': '0'})))
        ns['urma_support'] = NS(library_manifest=lambda p: {}, hardware_check=lambda p: {})
        for actual in (None, 8, 32):
            metadata = dict(phase_diagnostics=True, dispatch_trace_version=1, reference_events_version=1,
                            reference_boundary_version=1, reference_profile='boundaries',
                            normal_fadd_fence_version=1, normal_atomic_role_version=1,
                            normal_fadd_fence='tail-off', normal_max_inflight=actual, loaded_libraries=[])
            ns['subprocess'] = NS(run=lambda *a, **kw: NS(returncode=0, stdout='MODE_PARITY_IMPORT_OK ' + json.dumps(metadata), stderr=''))
            if actual == 32:
                ns['preflight'](args)
            else:
                with self.assertRaisesRegex(RuntimeError, 'NORMAL_WINDOW_RUNTIME_MISMATCH_OR_REBUILD_REQUIRED'):
                    ns['preflight'](args)


class CreditSummaryTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools/reference_events'))
        import summarize_credit_ab
        self.tool = summarize_credit_ab

    def fake(self, root, rank, *args, **kw):
        width = 32 if root.name.endswith('w32') else 8
        identity = dict(backend='urma', reference_profile='boundaries', normal_submit_schedule='lane-mixed',
                        normal_window_plan='both32' if width == 32 else 'default', normal_max_inflight=width,
                        sources={'compiled': 'same'}, flags={}, sdk={}, libraries={}, maca={}, gpu_versions=[],
                        benchmark_sha='same', diagnostics='same', torch='same', boot='node%d' % rank)
        c = dict(job=root.name, normal_window_plan=identity['normal_window_plan'],
                 urma_device_plan='202c1-204c0', device='udmac1d1e2' if rank == 0 else 'udmac0d1e2',
                 normal_fadd_fence_plan='peer-tail-off')
        return dict(c=c, identity=identity, runtime=dict(module_sha='same', normal_max_inflight=width,
                    normal_fifo_retire_version=1, normal_fifo_retire='ordered'),
                    case=root / ('node%d' % rank) / 'normal',
                    means={label: dict(mean_us=900 if width == 32 else 1000, cv_pct=0)
                           for label in self.tool.matched.base.ALL_LABELS})

    def samples(self, path):
        value = 900 if 'w32' in str(path) else 1000
        return [dict(label=label, index=i, used=True, us=value)
                for label in self.tool.matched.base.ALL_LABELS for i in range(1, 10)]

    def run_summary(self, out, loader=None):
        with patch.object(self.tool.matched.base, 'case_data', loader or self.fake), \
             patch.object(self.tool.matched.base, 'read', return_value={'checks': {'normal_fifo_retire': True}}), \
             patch.object(self.tool.matched.base, 'rows', self.samples):
            return self.tool.summarize(Path('data'), 'credit', out)

    def test_material_gain_and_unchanged_sources_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = self.run_summary(Path(tmp) / 'pass')
            self.assertTrue(all(r['decision'] == 'material_improvement' for r in rows))
            for field in ('module', 'boot', 'window'):
                def changed(root, rank, *args, **kw):
                    data = self.fake(root, rank)
                    if root.name.endswith('r02_w8'):
                        if field == 'module': data['runtime']['module_sha'] = 'changed'
                        elif field == 'boot': data['identity']['boot'] = 'changed'
                        else: data['runtime']['normal_max_inflight'] = 32
                    return data
                with self.assertRaises(ValueError):
                    self.run_summary(Path(tmp) / field, changed)

    def test_no_gain_closes_direction_and_drift_blocks_acceptance(self):
        for mode in ('no_gain', 'drift'):
            def samples(path):
                rows = self.samples(path)
                for row in rows:
                    if mode == 'no_gain': row['us'] = 1000
                    elif 'r02_w8' in str(path): row['us'] = 1200
                return rows
            with tempfile.TemporaryDirectory() as tmp, \
                 patch.object(self.tool.matched.base, 'case_data', self.fake), \
                 patch.object(self.tool.matched.base, 'read', return_value={'checks': {'normal_fifo_retire': True}}), \
                 patch.object(self.tool.matched.base, 'rows', samples):
                result = self.tool.summarize(Path('data'), 'credit', Path(tmp) / 'out')
            expected = 'no_material_change_keep_default' if mode == 'no_gain' else 'unstable_keep_default'
            self.assertTrue(all(r['decision'] == expected for r in result))


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux shell runner')
class CreditRunnerTests(unittest.TestCase):
    def setUp(self):
        from test_fence_runner import FenceRunnerTests
        self.fixture = FenceRunnerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temporary.cleanup)
        (self.fixture.root / 'reference_events/run_fadd_fence.sh').write_text(
            Path(__file__).with_name('run_credit_ab.sh').read_text())

    def execute(self, rank=0, fail=None):
        import subprocess
        env = dict(os.environ, URMA_DIAG_PYTHON=sys.executable)
        if fail: env['FAIL_SUFFIX'] = fail
        f = self.fixture
        return subprocess.run(['bash', str(f.root / 'reference_events/run_fadd_fence.sh'),
            '--node-rank', str(rank), '--job', 'credit', '--build-dir', str(f.root / 'build'),
            '--output', str(f.root / 'out'), '--destination', str(f.root / 'dest')],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

    def test_both_nodes_abba_no_capture_and_no_overwrite(self):
        for rank in (0, 1):
            result = self.execute(rank)
            self.assertEqual(result.returncode, 0, result.stdout)
        runs = [r for r in self.fixture.calls() if r['--action'] == 'run']
        for rank in (0, 1):
            local = [r for r in runs if r['--node-rank'] == str(rank)]
            self.assertEqual([r['--normal-window-plan'] for r in local], ['default', 'both32', 'both32', 'default'])
            self.assertTrue(all(r['--reference-events'] == 'batch' and r['--normal-fadd-fence-plan'] == 'peer-tail-off' for r in local))
            self.assertEqual({r['--urma-device'] for r in local}, {'udmac1d1e2' if rank == 0 else 'udmac0d1e2'})
        before = len(self.fixture.calls())
        self.assertNotEqual(self.execute().returncode, 0)
        self.assertEqual(len(self.fixture.calls()), before)

    def test_failure_collected_and_stops_remaining_cases(self):
        result = self.execute(fail='r01_w32')
        self.assertEqual(result.returncode, 7, result.stdout)
        runs = [r for r in self.fixture.calls() if r['--action'] == 'run']
        self.assertEqual([r['--job'] for r in runs], ['credit_r01_w8', 'credit_r01_w32'])
        self.assertIn('credit_r01_w32', (self.fixture.root / 'out/collected.jsonl').read_text())


if __name__ == '__main__':
    unittest.main()
