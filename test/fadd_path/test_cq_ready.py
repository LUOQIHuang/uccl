"""Target slot association and uncertainty bounds must survive queue wrapping."""
import copy
import subprocess
import unittest

import analyze_cq_ready
import overlay


def window():
    pair=dict(write_id=10,atomic_id=11,bytes=459264,operand=32,finished=True,status=0,fence=0,order=2,
              cq=7,write_post_begin=10,write_post_end=20,atomic_post_begin=10,atomic_post_end=20,
              write_sdk_return=400,atomic_sdk_return=400,write_handled=410,atomic_handled=420,
              write_poll_seq=3,atomic_poll_seq=3,initial_zero=True,result_at_completion=160,
              result_lower=60,result_upper=70,result_value=160)
    sdk=[dict(cq=7,seq=i+1,begin=b,end=e,count=n) for i,(b,e,n) in enumerate([(90,150,0),(190,250,0),(290,400,2)])]
    snapshots=[dict(seq=1,begin=100,end=110,sdk_return=150,ci=31,after_ci=31,count=0,ready_mask=0,completed=[]),
               dict(seq=2,begin=200,end=210,sdk_return=250,ci=31,after_ci=31,count=0,ready_mask=2,completed=[]),
               dict(seq=3,begin=300,end=310,sdk_return=400,ci=31,after_ci=33,count=2,ready_mask=3,
                    completed=[dict(wr_id=10,token=1000,position=31),dict(wr_id=11,token=1001,position=32)])]
    return dict(index=1,streams=[dict(proxy=i,errors=0,dropped=0,pairs=[copy.deepcopy(pair)],polls=copy.deepcopy(sdk)) for i in range(4)],
                cq_ready=[dict(proxy=i,dropped=0,depth=32,stride=64,lookahead=16,polls=copy.deepcopy(snapshots)) for i in range(4)])


class CqReadyTests(unittest.TestCase):
    def test_valid_target_behind_invalid_head_maps_across_ring_wrap(self):
        rows=analyze_cq_ready.pair_rows(window())
        self.assertEqual(len(rows),4)
        self.assertEqual(rows[0]['cq_position'],32)
        self.assertEqual(rows[0]['target_valid_head_invalid_polls'],1)
        self.assertAlmostEqual(rows[0]['valid_to_sdk_lower_us'],.19)
        self.assertAlmostEqual(rows[0]['result_to_last_invalid_lower_us'],.03)

    def test_count_mismatch_and_owner_reversal_are_rejected(self):
        w=window();w['cq_ready'][0]['polls'][-1]['after_ci']=34
        with self.assertRaisesRegex(ValueError,'consumer index'):analyze_cq_ready.pair_rows(w)
        w=window();w['cq_ready'][0]['polls'][-1]['ready_mask']=1
        with self.assertRaisesRegex(ValueError,'became invalid'):analyze_cq_ready.pair_rows(w)

    def test_unobserved_target_is_censored(self):
        w=window()
        for s in w['cq_ready']:
            for p in s['polls']:p['ready_mask']=0
        r=analyze_cq_ready.pair_rows(w)[0]
        self.assertFalse(r['target_observed_valid'])
        self.assertIsNone(r['valid_to_sdk_lower_us'])

    def test_overlay_python_anchors_match_fixed_sources(self):
        for path in overlay.TARGETS+('test/urma_mode_parity/worker.py','ep/bench/test_internode.py','test/reference_events/run.sh'):
            source=subprocess.check_output(['git','show',overlay.TAIL_BASE+':'+path],cwd=overlay.REPO).decode()
            if path in overlay.TARGETS:source=overlay.changes(path,source)
            source=overlay.cq_changes(path,overlay.tail_changes(path,source))
            if path.endswith('.py'):compile(source,path,'exec')


if __name__=='__main__':unittest.main()
