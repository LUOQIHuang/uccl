import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import summarize_scope_cadence as summary

LABEL = 'dispatch_bf16_chunk32'


class ScopeCadenceTests(unittest.TestCase):
    def fixture(self):
        data = {}
        for rank in (0, 1):
            for tag in ('batch_pre', 'batch_post', 'paced', 'post'):
                value = ((7979.833, 7984.839) if tag.startswith('batch') else
                         (8113.949, 6702.592))[rank]
                identity = dict(backend='urma', boot='same-boot', reference_profile='boundaries',
                    normal_window_plan='default', normal_submit_schedule='lane-mixed',
                    normal_fadd_fence_plan='peer-tail-off', normal_fadd_fence='tail-off' if rank else 'on')
                data[tag, rank] = dict(identity=identity, runtime=dict(module_sha='same-module'),
                    means={LABEL: dict(mean_us=value, cv_pct=0.5)})
        return data

    def run_summary(self, root, data):
        def load(path, rank, trace, reference_events):
            self.assertFalse(trace)
            self.assertEqual(reference_events, 'batch' if path.name.startswith('batch') else 'paced')
            return copy.deepcopy(data[path.name, rank])
        with patch.object(summary.boundaries.matched.base, 'case_data', side_effect=load), \
             patch.object(summary.boundaries.matched.base, 'ALL_LABELS', {LABEL}), \
             contextlib.redirect_stdout(io.StringIO()):
            return summary.summarize(*(root/tag for tag in ('batch_pre','batch_post','paced','post')),
                root/'summary', 'peer-tail-off')

    def test_local_16pct_drop_is_not_16pct_task_speedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows, combined = self.run_summary(Path(tmp), self.fixture())
            local = next(r for r in rows if r['node'] == 1)
            self.assertAlmostEqual(local['paced_vs_batch_pct'], -16.0585, places=3)
            self.assertGreater(combined[0]['paced_vs_batch_pct'], 1)
            self.assertEqual(combined[0]['batch_slow_node'], 1)
            self.assertEqual(combined[0]['paced_slow_node'], 0)
            self.assertTrue(combined[0]['both_nodes_stable'])

    def test_changed_boot_module_or_fence_is_rejected(self):
        for key, value in (('boot', 'different'), ('normal_fadd_fence', 'off')):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                data=self.fixture();data['batch_post',1]['identity'][key]=value
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.run_summary(Path(tmp), data)
        with tempfile.TemporaryDirectory() as tmp:
            data=self.fixture();data['post',0]['runtime']['module_sha']='other'
            with self.assertRaisesRegex(ValueError, 'changed'):
                self.run_summary(Path(tmp), data)

    def test_drift_is_preserved_and_marked_unstable(self):
        with tempfile.TemporaryDirectory() as tmp:
            data=self.fixture();data['batch_post',0]['means'][LABEL]['mean_us'] *= 1.10
            rows, combined=self.run_summary(Path(tmp), data)
            self.assertFalse(next(r for r in rows if r['node']==0)['stable'])
            self.assertFalse(combined[0]['both_nodes_stable'])

    def test_reusing_same_case_does_not_count_as_two_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with self.assertRaisesRegex(ValueError, 'distinct'):
                summary.summarize(root/'a',root/'a',root/'b',root/'c',root/'out')


if __name__ == '__main__':
    unittest.main()
