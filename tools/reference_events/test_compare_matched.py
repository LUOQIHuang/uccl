import copy
import unittest

import compare_matched as compare


class BackendComparisonTests(unittest.TestCase):
    def test_same_cta_pairing_uses_block_epoch_across_role_warps(self):
        rows = []
        for slot in range(12):
            for event, block, warp, ns in [(0, 2*slot+1, 0, 1000+slot*100),
                                            (1, 2*slot+1, 7, 3000+slot*100),
                                            (2, 2*slot, 0, 9000+slot*100)]:
                rows.append(dict(node='0', label='dispatch_fp8_chunk32', index='1',
                                 event=str(event), block=str(block), warp=str(warp),
                                 epoch='2', host_ns=str(ns)))
        result = compare.gpu_windows(list(reversed(rows)))[0]
        self.assertEqual(result['sender_cta_0_to_1_median_us'], 2)
        self.assertEqual(result['first_write_to_last_visible_us'], 7.1)
        self.assertEqual(result['first_token_to_last_visible_us'], 9.1)
        with self.assertRaisesRegex(ValueError, 'incomplete GPU window'):
            compare.gpu_windows(rows[:-1])
        bad = copy.deepcopy(rows)
        bad[1]['block'] = '99'
        with self.assertRaisesRegex(ValueError, 'unpaired Sender CTA'):
            compare.gpu_windows(bad)

    def test_only_expected_stack_differences_are_allowed(self):
        v = {key: 'same' for key in ('node', 'host', 'torch', 'maca', 'gpu_versions',
                                     'sources', 'benchmark_sha', 'ifname', 'gpu')}
        v.update(backend='verbs', flags={'USE_URMA': '0', 'USE_DMABUF': '1'}, kernel='verbs-kernel')
        u = copy.deepcopy(v)
        u.update(backend='urma', flags={'USE_URMA': '1', 'USE_DMABUF': '1'}, kernel='urma-kernel')
        compare.compatible(v, u)
        ablation=copy.deepcopy(u)
        ablation['normal_submit_schedule']='writes-first'
        with self.assertRaisesRegex(ValueError,'analyze submission A/B separately'):
            compare.compatible(v, ablation)
        for field in ('sources', 'benchmark_sha', 'maca', 'host', 'gpu_versions'):
            changed = copy.deepcopy(u)
            changed[field] = 'different'
            with self.assertRaisesRegex(ValueError, 'cross-backend mismatch'):
                compare.compatible(v, changed)
        u['flags']['USE_DMABUF'] = '0'
        with self.assertRaisesRegex(ValueError, 'build flags'):
            compare.compatible(v, u)


if __name__ == '__main__':
    unittest.main()
